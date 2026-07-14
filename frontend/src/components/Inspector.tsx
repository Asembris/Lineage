/*
 * Inspector — the right region. It is one evolving surface, not an accreting
 * stack: with no selection it shows the default (a fleet summary stat block over
 * the belief catalog); when a decision is investigated it is TAKEN OVER by the
 * Investigation view (the fleet counts persist in the app header, so nothing
 * critical is lost). ✕ in that view returns here. This keeps the region free for
 * Trace / Time-travel / Invalidate to occupy the same space in later steps.
 *
 * The fleet summary reads counts from all three console slots (agents / decisions
 * / beliefs), each defensively — a slot that isn't ready yet shows "—" rather than
 * blanking the panel, preserving the shell's per-source degradation. The belief
 * catalog is gated on the beliefs slot like the other panels.
 *
 * Purely presentational: every value is a real API field, nothing is fabricated.
 */

import type { Loadable } from "../hooks/useConsoleData";
import type { AgentsData, BeliefsData, DecisionsData } from "../hooks/useConsoleData";
import type { Belief, UUID } from "../api/types";
import type { Investigation as InvestigationData } from "../lib/investigation";
import { Loaded } from "./Panel";
import { Investigation, type TraceHandlers } from "./Investigation";
import type { InvalidateHandlers } from "./Invalidate";
import { fragId, formatCount, formatDate } from "../lib/format";
import "./Inspector.css";

/** A single stat: a mono value over an uppercase label. "—" when not yet ready. */
function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div className="stat">
      <span className="stat__value">{value}</span>
      <span className="stat__label">{label}</span>
    </div>
  );
}

function ready<T>(slot: Loadable<T>): T | undefined {
  return slot.status === "ready" ? slot.data : undefined;
}

function FleetSummary(props: {
  agents: Loadable<AgentsData>;
  decisions: Loadable<DecisionsData>;
  beliefs: Loadable<BeliefsData>;
}) {
  const agents = ready(props.agents);
  const decisions = ready(props.decisions);
  const beliefs = ready(props.beliefs);

  const alive = agents?.agents.filter((a) => a.status === "alive").length;
  const dead = agents ? agents.count - (alive ?? 0) : undefined;
  const activeBeliefs = beliefs?.beliefs.filter((b) => b.status === "active").length;

  const dash = "—";
  const n = (v: number | undefined) => (v === undefined ? dash : formatCount(v));

  return (
    <section className="inspector__section">
      <h3 className="inspector__heading">Fleet</h3>
      <div className="stat-grid">
        <Stat value={n(agents?.count)} label="agents" />
        <Stat value={n(alive)} label="alive" />
        <Stat value={n(dead)} label="dead" />
        <Stat value={n(decisions?.total)} label="decisions" />
        <Stat
          value={
            beliefs === undefined
              ? dash
              : `${formatCount(activeBeliefs ?? 0)} / ${formatCount(beliefs.count)}`
          }
          label="beliefs active"
        />
      </div>
    </section>
  );
}

function BeliefCard({ b }: { b: Belief }) {
  const invalidated = b.status !== "active";
  return (
    <li className={`belief${invalidated ? " belief--invalidated" : ""}`}>
      <div className="belief__head">
        <span className={`belief__status belief__status--${b.status}`}>
          {b.status}
        </span>
        <span className="belief__origin" title={`originating agent ${b.originating_agent_id}`}>
          {fragId(b.originating_agent_id)}
        </span>
      </div>
      <p className="belief__rule">{b.rule_text}</p>
      <div className="belief__meta">
        <span>formed {formatDate(b.formed_at)}</span>
        {b.invalidated_at && (
          <span className="belief__invalidated">
            invalidated {formatDate(b.invalidated_at)}
          </span>
        )}
      </div>
    </li>
  );
}

export function Inspector(props: {
  agents: Loadable<AgentsData>;
  decisions: Loadable<DecisionsData>;
  beliefs: Loadable<BeliefsData>;
  investigation: InvestigationData | null;
  onClear: () => void;
  traceHandlers: TraceHandlers;
  invalidateHandlers: InvalidateHandlers;
  /* Passed straight through to Investigation. A `UUID` in, the evidence surface out. */
  onInterrogate: (txnId: UUID) => void;
}) {
  // A selected decision takes over the whole Inspector surface.
  if (props.investigation) {
    return (
      <Investigation
        inv={props.investigation}
        onClear={props.onClear}
        handlers={props.traceHandlers}
        invalidateHandlers={props.invalidateHandlers}
        onInterrogate={props.onInterrogate}
      />
    );
  }

  return (
    <div className="inspector">
      <FleetSummary
        agents={props.agents}
        decisions={props.decisions}
        beliefs={props.beliefs}
      />

      <section className="inspector__section">
        <h3 className="inspector__heading">Belief catalog</h3>
        <Loaded state={props.beliefs} loadingLabel="Loading beliefs…">
          {(data) =>
            data.count === 0 ? (
              <p className="panel__note">No beliefs on the cluster.</p>
            ) : (
              <ol className="belief-list">
                {data.beliefs.map((b) => (
                  <BeliefCard key={b.id} b={b} />
                ))}
              </ol>
            )
          }
        </Loaded>
      </section>
    </div>
  );
}
