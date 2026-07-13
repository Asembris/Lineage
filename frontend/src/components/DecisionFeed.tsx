/*
 * DecisionFeed — the left region. The fleet-wide decision stream, newest first.
 *
 * Investigate (Frontend Phase 3): each row is a real button — selecting it drives
 * the Inspector's investigation view (which belief drove it, inherited or not).
 * Selection is a raised background + a cold --bone left bar (NOT warmth — that is
 * reserved for Trace), coexisting with the fraud left rule below it.
 *
 * The feed is a bounded live window, NOT a full-table dump: the hook fetches the
 * backend's max page (limit=200, offset 0, newest first). The panel header shows
 * loaded / total so the bounding over thousands of rows stays honest.
 *
 * THE KIND FILTER, AND THE DEFAULT — a navigation-honesty decision, not a convenience.
 * Every AML decision carries one fixed `decided_at`, so all 1,500 sort above all 4,000 card rows
 * and a single unfiltered page of 200 was 200 AML rows: the crimson belief's whole staleness curve
 * was unreachable from this console (see useConsoleData's header for the full finding). The filter
 * fixes reachability. The DEFAULT stays UNFILTERED — a console that silently pre-filters the
 * fleet's record is choosing for the supervisor what is worth looking at, and this one does not.
 * What it does instead is COUNT: each chip states its real cluster total, so the 4,000 card
 * decisions are visibly present even while you are looking at the AML ones. Nothing is hidden;
 * either kind is one click away.
 *
 * Cold by default. The single always-on signal is --alert on is_fraud rows (fraud
 * is the palette's designated alert meaning, distinct from the Phase-3 trace
 * warmth): a thin left rule + a small dot, kept minimal so it reads as a forensic
 * flag even where recent-generation fraud density is high, not as visual noise.
 * The chips are cold too (--bone/--ash): a filter is not a state of alarm.
 */

import type { DecisionsData, KindCounts, Loadable } from "../hooks/useConsoleData";
import type { Decision, DecisionKind, UUID } from "../api/types";
import { formatAmount, formatConfidence, formatCount, splitInstant } from "../lib/format";
import { RestoreHint } from "./RestoreHint";
import "./DecisionFeed.css";

function DecisionRow({
  d,
  selected,
  onSelect,
}: {
  d: Decision;
  selected: boolean;
  onSelect: (id: UUID) => void;
}) {
  const { date, time } = splitInstant(d.decided_at);
  const beliefDriven = d.driving_belief_id !== null;
  return (
    <li className="feed__item">
      <button
        type="button"
        className={`feed__row${d.is_fraud ? " feed__row--fraud" : ""}${
          selected ? " feed__row--selected" : ""
        }`}
        aria-pressed={selected}
        onClick={() => onSelect(d.id)}
      >
        <span className="feed__time">
          <span className="feed__date">{date}</span>
          <span className="feed__clock">{time}</span>
        </span>
        <span className="feed__amount">{formatAmount(d.amount, d.amount_currency)}</span>

        <span className="feed__where">
          {/* merchant is null for AML decisions (a bank-to-bank transfer has no merchant). An em
              dash keeps the absence visible; presenting AML decisions properly is the deferred
              frontend session's job, not a drive-by here. */}
          <span className="feed__merchant">{d.merchant ?? "—"}</span>
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
      </button>
    </li>
  );
}

/** The kind chips. Each carries its REAL cluster count, so the kind you are not looking at is
 *  still visibly there. A count that is not ready yet shows no number rather than a guessed one. */
function KindFilter({
  kind,
  onKind,
  counts,
}: {
  kind: DecisionKind | null;
  onKind: (k: DecisionKind | null) => void;
  counts: Loadable<KindCounts>;
}) {
  const n = (key: keyof KindCounts) =>
    counts.status === "ready" ? formatCount(counts.data[key]) : null;

  const chips: { label: string; value: DecisionKind | null; key: keyof KindCounts }[] = [
    { label: "all", value: null, key: "all" },
    { label: "card", value: "card", key: "card" },
    { label: "aml", value: "aml", key: "aml" },
  ];

  return (
    <div className="feed__filter" role="group" aria-label="Filter decisions by kind">
      {chips.map((c) => {
        const count = n(c.key);
        return (
          <button
            key={c.label}
            type="button"
            className={`feed__chip${kind === c.value ? " feed__chip--on" : ""}`}
            aria-pressed={kind === c.value}
            onClick={() => onKind(c.value)}
          >
            {c.label}
            {count !== null && <span className="feed__chip-n">{count}</span>}
          </button>
        );
      })}
    </div>
  );
}

export function DecisionFeed({
  data,
  selectedId,
  onSelect,
  kind,
  onKind,
  counts,
}: {
  data: DecisionsData;
  selectedId: UUID | null;
  onSelect: (id: UUID) => void;
  kind: DecisionKind | null;
  onKind: (k: DecisionKind | null) => void;
  counts: Loadable<KindCounts>;
}) {
  // TWO DIFFERENT EMPTIES, AND CONFLATING THEM WOULD BE A LIE. An empty FILTER on a populated
  // cluster is not a broken world and must never print restore instructions; only a genuinely
  // empty cluster gets the (single, shared) restore procedure.
  const clusterEmpty = counts.status === "ready" ? counts.data.all === 0 : data.total === 0;

  return (
    <>
      <KindFilter kind={kind} onKind={onKind} counts={counts} />
      {clusterEmpty ? (
        <p className="panel__note">
          No decisions on the cluster — <RestoreHint />
        </p>
      ) : data.total === 0 ? (
        <p className="panel__note">
          No <span className="feed__chip-inline">{kind}</span> decisions on the cluster. The other
          kinds are still there — the cluster is populated.
        </p>
      ) : (
        <ol className="feed">
          {data.decisions.map((d) => (
            <DecisionRow
              key={d.id}
              d={d}
              selected={d.id === selectedId}
              onSelect={onSelect}
            />
          ))}
        </ol>
      )}
    </>
  );
}
