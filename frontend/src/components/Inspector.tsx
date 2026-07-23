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

/** A single stat: a mono value over an uppercase label. "—" when not yet ready. `tone` tints the
 *  value (alive → --alive); `wide` spans both columns of the hairline grid so an odd 5th tile
 *  (we keep the real "dead" count, unlike the DC's 4) sits on its own full row rather than leaving
 *  a hollow cell. */
function Stat({
  value,
  label,
  tone,
  wide,
}: {
  value: string;
  label: string;
  tone?: "alive";
  wide?: boolean;
}) {
  return (
    <div className={`stat${wide ? " stat--wide" : ""}`}>
      <span className={`stat__value${tone ? ` stat__value--${tone}` : ""}`}>{value}</span>
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
        <Stat value={n(alive)} label="alive" tone="alive" />
        <Stat value={n(dead)} label="dead" />
        <Stat value={n(decisions?.total)} label="decisions" />
        <Stat
          value={
            beliefs === undefined
              ? dash
              : `${formatCount(activeBeliefs ?? 0)} / ${formatCount(beliefs.count)}`
          }
          label="beliefs active"
          wide
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
        <span className="belief__id" title={`belief ${b.id}`}>
          {fragId(b.id)}
        </span>
        <span className={`belief__status belief__status--${b.status}`}>{b.status}</span>
      </div>
      <p className="belief__rule">{b.rule_text}</p>
      <div className="belief__meta">
        <span title={`originating agent ${b.originating_agent_id}`}>
          origin {fragId(b.originating_agent_id)}
        </span>
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
