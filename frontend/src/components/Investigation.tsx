/*
 * Investigation — the Inspector's take-over view when a decision is selected.
 *
 * Shows the selected decision, the belief that drove it (resolved from the loaded
 * catalog — no new fetch), and the centrepiece tag: INHERITED vs FORMED HERE,
 * computed from real fields (see lib/investigation.ts for why the comparison is
 * exact). Quiet and cold: no warmth (that's reserved for Trace), no motion. The
 * only signal colors are the already-sanctioned --alert (fraud) and --alive
 * (active belief). ✕ clears back to the default fleet + catalog Inspector.
 */

import type { Investigation as InvestigationData } from "../lib/investigation";
import type { UUID } from "../api/types";
import { fragId, formatAmount, formatConfidence, formatDate, splitInstant } from "../lib/format";
import { TimeTravel } from "./TimeTravel";
import { InvalidateBlock, type InvalidateHandlers } from "./Invalidate";
import "./Investigation.css";

/** UI-facing view of the App's trace state (App owns the real state/animation). */
export type InvestigationTrace =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "empty" }
  | { status: "active"; phase: "animating" | "done"; chainLength: number };

export interface TraceHandlers {
  trace: InvestigationTrace;
  onStartTrace: (beliefId: UUID, agentId: UUID) => void;
  onReplay: () => void;
}

/** "gen 7 · 108cf7" — a real agent's generation + id fragment, or a fallback. */
function AgentTag({
  agent,
  fallbackId,
}: {
  agent: InvestigationData["decidingAgent"];
  fallbackId: string;
}) {
  if (!agent) {
    return <span className="inv__agent inv__agent--unknown">{fragId(fallbackId)}</span>;
  }
  return (
    <span className="inv__agent" title={agent.id}>
      gen {agent.generation} · {fragId(agent.id)}
    </span>
  );
}

function InheritedBadge({ inv }: { inv: InvestigationData }) {
  const decidingGen = inv.decidingAgent?.generation;
  const originGen = inv.originAgent?.generation;

  if (inv.inherited) {
    const gap =
      decidingGen !== undefined && originGen !== undefined
        ? decidingGen - originGen
        : undefined;
    return (
      <div className="inv__tag inv__tag--inherited">
        <span className="inv__tag-label">inherited</span>
        <div className="inv__tag-detail">
          <span>
            {originGen !== undefined ? `formed by gen ${originGen}` : "formed upstream"}
            {" · "}
            {decidingGen !== undefined
              ? `acted on by gen ${decidingGen}`
              : "acted on downstream"}
          </span>
          {gap !== undefined && gap > 0 && (
            <span className="inv__gap">
              {gap} generation{gap === 1 ? "" : "s"} downstream
            </span>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="inv__tag inv__tag--formed">
      <span className="inv__tag-label">formed here</span>
      <span className="inv__tag-detail">
        {decidingGen !== undefined
          ? `this agent (gen ${decidingGen}) originated the belief`
          : "this agent originated the belief"}
      </span>
    </div>
  );
}

/* The Trace trigger and its resolved conclusion. Only rendered for a resolved
   belief. The button is cold (warmth is earned by the animation, not the button);
   the conclusion — shown once the origin has ignited (phase "done") — carries the
   only warm accents in the Inspector, echoing the origin it just lit. */
function TraceBlock({ inv, handlers }: { inv: InvestigationData; handlers: TraceHandlers }) {
  const b = inv.belief;
  if (!b) return null;
  const { trace, onStartTrace, onReplay } = handlers;
  const start = () => onStartTrace(b.id, inv.decision.agent_id);

  if (trace.status === "idle") {
    return (
      <button className="inv__trace-btn" onClick={start}>
        Trace lineage →
      </button>
    );
  }
  if (trace.status === "loading") {
    return (
      <button className="inv__trace-btn" disabled>
        Tracing…
      </button>
    );
  }
  if (trace.status === "error") {
    return (
      <div className="inv__trace-wrap">
        <p className="panel__note inv__note">Trace failed: {trace.message}</p>
        <button className="inv__trace-btn" onClick={start}>
          Retry trace
        </button>
      </div>
    );
  }
  if (trace.status === "empty") {
    return (
      <p className="panel__note inv__note">
        No inheritance chain resolved for this agent — nothing to trace.
      </p>
    );
  }

  // active
  if (trace.phase === "animating") {
    return <p className="inv__tracing">Tracing lineage…</p>;
  }
  const hops = Math.max(0, trace.chainLength - 1);
  return (
    <div className="inv__conclusion">
      <p className="inv__conclusion-text">
        Belief <span className="inv__mono inv__hot-trace">{fragId(b.id)}</span> originated with
        agent <span className="inv__mono inv__hot-origin">{fragId(b.originating_agent_id)}</span>
        {hops > 0 ? (
          <>
            {" — "}
            <span className="inv__hot-trace">
              {hops} generation{hops === 1 ? "" : "s"} ago
            </span>
            .
          </>
        ) : (
          <> — formed by this agent.</>
        )}
      </p>
      <p className="inv__conclusion-meta">formed {formatDate(b.formed_at)}</p>
      <button className="inv__trace-btn" onClick={onReplay}>
        Replay ↺
      </button>
    </div>
  );
}

function DrivingBelief({
  inv,
  handlers,
  invalidateHandlers,
}: {
  inv: InvestigationData;
  handlers: TraceHandlers;
  invalidateHandlers: InvalidateHandlers;
}) {
  if (inv.beliefState === "none") {
    return (
      <p className="panel__note inv__note">
        Not belief-driven — this decision cited no belief.
      </p>
    );
  }
  if (inv.beliefState === "pending") {
    return <p className="panel__note inv__note">Loading belief…</p>;
  }
  if (inv.beliefState === "missing" || !inv.belief) {
    return (
      <p className="panel__note inv__note">
        Driving belief{" "}
        <span className="inv__mono">{fragId(inv.decision.driving_belief_id ?? "")}</span> is
        not in the loaded catalog.
      </p>
    );
  }

  const b = inv.belief;
  // If THIS session just invalidated the belief, reflect that in the card immediately — the
  // invalidation response is authoritative, so the header never contradicts the outcome block
  // below it (the loaded catalog, fetched at page load, would otherwise still read "active").
  const done = invalidateHandlers.ui.status === "done" ? invalidateHandlers.ui.result : null;
  const status = done ? "invalidated" : b.status;
  const invalidatedAt = done ? done.invalidated_at : b.invalidated_at;
  return (
    <>
      <div className={`inv__belief${status !== "active" ? " inv__belief--invalidated" : ""}`}>
        <div className="inv__belief-head">
          <span className={`inv__belief-status inv__belief-status--${status}`}>{status}</span>
          <span className="inv__belief-origin" title={`originating agent ${b.originating_agent_id}`}>
            origin {fragId(b.originating_agent_id)}
          </span>
        </div>
        <p className="inv__belief-rule">{b.rule_text}</p>
        <div className="inv__belief-meta">
          <span>formed {formatDate(b.formed_at)}</span>
          {invalidatedAt && (
            <span className="inv__invalidated">invalidated {formatDate(invalidatedAt)}</span>
          )}
        </div>
      </div>
      <InheritedBadge inv={inv} />
      <TraceBlock inv={inv} handlers={handlers} />
      <TimeTravel inv={inv} />
      <InvalidateBlock belief={b} handlers={invalidateHandlers} />
    </>
  );
}

export function Investigation({
  inv,
  onClear,
  handlers,
  invalidateHandlers,
}: {
  inv: InvestigationData;
  onClear: () => void;
  handlers: TraceHandlers;
  invalidateHandlers: InvalidateHandlers;
}) {
  const d = inv.decision;
  const { date, time } = splitInstant(d.decided_at);

  return (
    <div className="inv">
      <div className="inv__head">
        <h3 className="inspector__heading inv__heading">Investigation</h3>
        <button className="inv__close" onClick={onClear} aria-label="Close investigation">
          ✕
        </button>
      </div>

      {/* The selected decision */}
      <section className="inspector__section inv__section">
        <div className="inv__decision">
          <div className="inv__decision-top">
            {/* null for AML decisions — see DecisionFeed. */}
            <span className="inv__merchant">{d.merchant ?? "—"}</span>
            <span className="inv__amount">{formatAmount(d.amount, d.amount_currency)}</span>
          </div>
          <div className="inv__decision-tags">
            <span className={`inv__verdict inv__verdict--${d.verdict}`}>{d.verdict}</span>
            <span className="inv__conf">conf {formatConfidence(d.confidence)}</span>
            {d.is_fraud && (
              <span className="inv__fraud">
                <span className="inv__fraud-dot" aria-hidden="true" /> labelled fraud
              </span>
            )}
          </div>
          <dl className="inv__kv">
            <div>
              <dt>txn</dt>
              <dd className="inv__mono">{d.txn_ref}</dd>
            </div>
            <div>
              <dt>agent</dt>
              <dd>
                <AgentTag agent={inv.decidingAgent} fallbackId={d.agent_id} />
              </dd>
            </div>
            <div>
              <dt>decided</dt>
              <dd className="inv__mono">
                {date} {time}
              </dd>
            </div>
          </dl>
        </div>
      </section>

      {/* The belief that drove it */}
      <section className="inspector__section inv__section">
        <h3 className="inspector__heading">Driving belief</h3>
        <DrivingBelief inv={inv} handlers={handlers} invalidateHandlers={invalidateHandlers} />
      </section>
    </div>
  );
}
