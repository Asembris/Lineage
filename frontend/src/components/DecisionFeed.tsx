/*
 * DecisionFeed — the left region. The fleet-wide decision stream, newest first.
 *
 * Presentational only (Frontend Phase 2): renders the most-recent window of real
 * decisions from GET /decisions. No selection / investigate — that's Phase 3.
 *
 * The feed is a bounded live window, NOT a full-table dump: the hook fetches the
 * backend's max page (limit=200, offset 0, newest first). The panel header shows
 * loaded / total so the bounding over thousands of rows stays honest.
 *
 * Cold by default. The single signal is --alert on is_fraud rows (fraud is the
 * palette's designated alert meaning, distinct from the Phase-3 trace warmth):
 * a thin left rule + a small dot, kept minimal so it reads as a forensic flag
 * even where recent-generation fraud density is high, not as visual noise.
 */

import type { DecisionsData } from "../hooks/useConsoleData";
import type { Decision } from "../api/types";
import { formatAmount, formatConfidence, splitInstant } from "../lib/format";
import "./DecisionFeed.css";

function DecisionRow({ d }: { d: Decision }) {
  const { date, time } = splitInstant(d.decided_at);
  const beliefDriven = d.driving_belief_id !== null;
  return (
    <li className={`feed__row${d.is_fraud ? " feed__row--fraud" : ""}`}>
      <span className="feed__time">
        <span className="feed__date">{date}</span>
        <span className="feed__clock">{time}</span>
      </span>
      <span className="feed__amount">{formatAmount(d.amount)}</span>

      <span className="feed__where">
        <span className="feed__merchant">{d.merchant}</span>
        <span className="feed__txn">{d.txn_ref}</span>
      </span>
      <span className="feed__tags">
        {beliefDriven && (
          <span className="feed__belief" title="belief-driven decision">
            belief
          </span>
        )}
        {d.is_fraud && (
          <span
            className="feed__fraud-dot"
            role="img"
            aria-label="labelled fraud"
            title="labelled fraud"
          />
        )}
        <span className={`feed__verdict feed__verdict--${d.verdict}`}>
          {d.verdict}
        </span>
        <span className="feed__conf">{formatConfidence(d.confidence)}</span>
      </span>
    </li>
  );
}

export function DecisionFeed({ data }: { data: DecisionsData }) {
  if (data.total === 0) {
    return (
      <p className="panel__note">
        No decisions on the cluster yet. Rerun the backfill to populate the feed.
      </p>
    );
  }
  return (
    <ol className="feed">
      {data.decisions.map((d) => (
        <DecisionRow key={d.id} d={d} />
      ))}
    </ol>
  );
}
