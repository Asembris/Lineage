/*
 * investigation.ts — the pure resolver behind the Investigate interaction.
 *
 * Given the currently-selected decision id and the three already-loaded console
 * slots, it assembles everything the Inspector's investigation view needs — with
 * NO new network call. Two facts are resolved entirely from data the shell already
 * fetched:
 *
 *   1. The driving belief — looked up in the loaded /beliefs catalog by
 *      decision.driving_belief_id. The catalog is the full, unfiltered, bounded
 *      list, so the lookup resolves whenever a belief drove the decision. (There is
 *      no GET /beliefs/{id}; the only other source is /beliefs/{id}/lineage, and
 *      calling that here would be starting Trace early.)
 *
 *   2. inherited vs formed-here — decision.agent_id !== belief.originating_agent_id.
 *      This is definitionally exact in the data model: an agent either FORMED a
 *      belief (originating_agent_id == agent) or HOLDS it via a belief_inheritance
 *      edge (!=) — there is no third way to hold one. So the tag is real, not a
 *      heuristic. The belief_inheritance rows are the ground truth Trace will walk;
 *      here the comparison yields the same verdict without the traversal.
 */

import type { AgentGenealogy, Belief, Decision, UUID } from "../api/types";
import type {
  AgentsData,
  BeliefsData,
  DecisionsData,
  Loadable,
} from "../hooks/useConsoleData";

/** How the driving belief resolved for the selected decision. */
export type BeliefState =
  | "none" /* decision cited no belief (driving_belief_id is null) */
  | "pending" /* a belief drove it, but the catalog slot isn't ready yet */
  | "missing" /* a belief drove it, but its id isn't in the loaded catalog */
  | "resolved"; /* the driving belief was found */

export interface Investigation {
  decision: Decision;
  decidingAgent: AgentGenealogy | null;
  beliefState: BeliefState;
  belief: Belief | null;
  originAgent: AgentGenealogy | null;
  /** null unless beliefState === "resolved". */
  inherited: boolean | null;
}

function ready<T>(slot: Loadable<T>): T | undefined {
  return slot.status === "ready" ? slot.data : undefined;
}

/**
 * Resolve the investigation view for a selected decision, or null when there is
 * no selection / the selected decision is no longer in the loaded feed. Degrades
 * per-slot: an agents/beliefs slot that isn't ready yet leaves the corresponding
 * field null (or beliefState "pending") rather than throwing.
 */
export function resolveInvestigation(
  selectedId: UUID | null,
  decisions: Loadable<DecisionsData>,
  agents: Loadable<AgentsData>,
  beliefs: Loadable<BeliefsData>,
): Investigation | null {
  if (selectedId === null) return null;

  const decisionsData = ready(decisions);
  const decision = decisionsData?.decisions.find((d) => d.id === selectedId);
  if (!decision) return null; // selection stale / feed not ready

  const agentsData = ready(agents);
  const decidingAgent =
    agentsData?.agents.find((a) => a.id === decision.agent_id) ?? null;

  let beliefState: BeliefState;
  let belief: Belief | null = null;

  if (decision.driving_belief_id === null) {
    beliefState = "none";
  } else {
    const beliefsData = ready(beliefs);
    if (!beliefsData) {
      beliefState = "pending";
    } else {
      belief =
        beliefsData.beliefs.find((b) => b.id === decision.driving_belief_id) ?? null;
      beliefState = belief ? "resolved" : "missing";
    }
  }

  const originAgent =
    belief && agentsData
      ? (agentsData.agents.find((a) => a.id === belief.originating_agent_id) ?? null)
      : null;

  const inherited = belief ? decision.agent_id !== belief.originating_agent_id : null;

  return { decision, decidingAgent, beliefState, belief, originAgent, inherited };
}
