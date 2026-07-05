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
import { fragId, formatAmount, formatConfidence, formatDate, splitInstant } from "../lib/format";
import "./Investigation.css";

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

function DrivingBelief({ inv }: { inv: InvestigationData }) {
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
  return (
    <>
      <div className={`inv__belief${b.status !== "active" ? " inv__belief--invalidated" : ""}`}>
        <div className="inv__belief-head">
          <span className={`inv__belief-status inv__belief-status--${b.status}`}>
            {b.status}
          </span>
          <span className="inv__belief-origin" title={`originating agent ${b.originating_agent_id}`}>
            origin {fragId(b.originating_agent_id)}
          </span>
        </div>
        <p className="inv__belief-rule">{b.rule_text}</p>
        <div className="inv__belief-meta">
          <span>formed {formatDate(b.formed_at)}</span>
          {b.invalidated_at && (
            <span className="inv__invalidated">invalidated {formatDate(b.invalidated_at)}</span>
          )}
        </div>
      </div>
      <InheritedBadge inv={inv} />
    </>
  );
}

export function Investigation({
  inv,
  onClear,
}: {
  inv: InvestigationData;
  onClear: () => void;
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
            <span className="inv__merchant">{d.merchant}</span>
            <span className="inv__amount">{formatAmount(d.amount)}</span>
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
        <DrivingBelief inv={inv} />
      </section>
    </div>
  );
}
