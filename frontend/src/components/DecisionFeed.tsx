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
 * PAGINATION IS LOAD-BEARING, NOT HYGIENE. The feed fetched ONE page (the backend max, 200) and
 * stopped, so `kind=aml` reached 200 of 1,500 — leaving 1,300 AML decisions, and the 1,300 real
 * money-flow transactions they cite 1:1, unreachable by any user action. Among them 50 of the 57
 * CYCLE rings. "Load more" is what makes the evidence layer navigable at all.
 *
 * THE WITNESS CHIPS MAKE AN APPROVAL'S BASIS VISIBLE, and that is the point of the seam's whole
 * disclosure. Two different witness outcomes both map to `approve` and the verdict CANNOT tell them
 * apart: CONCLUSIVE_NO (there is no cycle — though only 16 of those 463 were actually SEARCHED; the
 * other 447 are self-loops, an account paying itself, where no search was ever possible) and
 * INCONCLUSIVE (the search ran off the edge of the extract — 65.3% of it, silently approving 252 of
 * the 300 laundering rows). Each chip carries its REAL cluster count, counted and never retyped,
 * exactly as the honesty ledger does: this census has been corrupted by prose three times — once
 * misstated, once falsely sourced, once misdescribing its own complement — and a number read from
 * the cluster cannot be wrong about the cluster.
 *
 * Cold by default. The single always-on signal is --alert on is_fraud rows (fraud
 * is the palette's designated alert meaning, distinct from the Phase-3 trace
 * warmth): a thin left rule + a small dot, kept minimal so it reads as a forensic
 * flag even where recent-generation fraud density is high, not as visual noise.
 * The chips are cold too (--bone/--ash): a filter is not a state of alarm.
 */

import { useEffect, useRef } from "react";
import {
  PAGE_SIZE,
  type DecisionsData,
  type KindCounts,
  type Loadable,
  type WitnessCounts,
} from "../hooks/useConsoleData";
import type { Decision, DecisionKind, UUID, WitnessOutcome } from "../api/types";
import { formatAmount, formatConfidence, formatCount, fragId, splitInstant } from "../lib/format";
import { RestoreHint } from "./RestoreHint";
import "./DecisionFeed.css";

/* The basis, spelled out. Deliberately NOT the "463 searches" gloss: only 16 of those 463 were
   searched — the other 447 are self-loops, an account paying itself, where no search ever ran. */
const BASIS_TITLE: Record<WitnessOutcome, string> = {
  MATCH: "MATCH — the graph re-derived a real directed cycle. Corroborated; the transfer is blocked.",
  CONCLUSIVE_NO:
    "CONCLUSIVE_NO — there is no cycle. Approved. (Only 16 of these 463 were actually searched; " +
    "447 are self-loops, where no search was possible.)",
  INCONCLUSIVE:
    "INCONCLUSIVE — the search ran off the edge of the 1,500-edge extract and COULD NOT DETERMINE. " +
    "Approved anyway: a disclosed modeling choice, and 65.3% of the extract.",
};

function DecisionRow({
  d,
  selected,
  onSelect,
  onInterrogate,
  returnFocusTxn,
  onFocusReturned,
}: {
  d: Decision;
  selected: boolean;
  onSelect: (id: UUID) => void;
  onInterrogate: (txnId: UUID) => void;
  returnFocusTxn: UUID | null;
  onFocusReturned: () => void;
}) {
  const { date, time } = splitInstant(d.decided_at);
  // The driving belief, as its id fragment (fragId → first 6, the console's identity convention) —
  // it NAMES which belief drove the decision, not merely THAT one did. Null for a card decision
  // without a driving belief; the ternary narrows so no non-null assertion is needed.
  const beliefFrag = d.driving_belief_id !== null ? fragId(d.driving_belief_id) : null;

  // The seam's focus handoff (return leg). When the feed remounts after the evidence view closes,
  // the row whose "see why" opened it takes focus back — so a keyboard user lands where they left,
  // not on <body>. App holds the txn (returnFocusTxn); only the matching row fires, exactly once
  // (it clears the pending txn, so the guard is false on the next render).
  const seeWhyRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (returnFocusTxn && d.aml_transaction_id === returnFocusTxn) {
      seeWhyRef.current?.focus();
      onFocusReturned();
    }
  }, [returnFocusTxn, d.aml_transaction_id, onFocusReturned]);
  // The per-row confidence bar is a real reading of d.confidence — a card decision's model
  // confidence, or nothing for an AML row (its structural witness is deterministic, so confidence
  // is null and there is no bar to draw). --ghost fill: it is a magnitude, not a signal.
  const confKnown = d.confidence !== null;
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
        {/* Line 1 — merchant (or the em dash for a bank-to-bank AML transfer) · amount */}
        <span className="feed__line feed__line--head">
          <span className="feed__merchant">{d.merchant ?? "—"}</span>
          <span className="feed__amount">{formatAmount(d.amount, d.amount_currency)}</span>
        </span>

        {/* Line 2 — when · what-it-was-about (left) · fraud / basis / verdict (right).
            THE ROW MUST NAME WHAT THE DECISION WAS ABOUT, and for an AML row `txn_ref` is NOT it:
            0008 makes txn_ref carry the BASIS (`aml:INCONCLUSIVE`), and the real reference is the
            FK. Without the transaction, every AML row showed one shared `decided_at`, no merchant,
            no confidence and the same basis string — 12 of the 1,500 were VISUALLY IDENTICAL. */}
        <span className="feed__line feed__line--meta">
          <span className="feed__when">
            {date} {time} · {d.aml_transaction_id ? `txn ${fragId(d.aml_transaction_id)}` : d.txn_ref}
          </span>
          <span className="feed__tags">
            {/* Fraud is now shown as an always-on dot+badge here (not only as the left rule), so a
                selected fraud row still reads as fraud even though selection takes the left rule. */}
            {d.is_fraud && (
              <span className="feed__fraud">
                <span
                  className="feed__fraud-dot"
                  role="img"
                  aria-label="labelled fraud"
                  title="labelled fraud"
                />
                <span className="feed__fraud-badge" aria-hidden="true">
                  fraud
                </span>
              </span>
            )}
            {/* THE BASIS. An AML `approve` without it is unreadable: CONCLUSIVE_NO means "there is
                no cycle" (447 of its 463 are self-loops, never searched at all; only 16 were),
                INCONCLUSIVE means "we could not determine" — 463 and 980 rows of the SAME verdict.
                Card decisions have no witness and get nothing here. */}
            {d.witness_outcome && (
              <span
                className={`feed__basis feed__basis--${d.witness_outcome.toLowerCase()}`}
                title={BASIS_TITLE[d.witness_outcome]}
              >
                {d.witness_outcome === "CONCLUSIVE_NO" ? "no cycle" : d.witness_outcome.toLowerCase()}
              </span>
            )}
            <span className={`feed__verdict feed__verdict--${d.verdict}`}>{d.verdict}</span>
          </span>
        </span>

        {/* Line 3 — confidence bar · confidence value · belief marker */}
        <span className="feed__line feed__line--conf">
          <span className="feed__confbar-track">
            {confKnown && (
              <span
                className="feed__confbar"
                style={{ width: `${Math.round((d.confidence as number) * 100)}%` }}
              />
            )}
          </span>
          <span className={`feed__conf${confKnown ? "" : " feed__conf--none"}`}>
            {formatConfidence(d.confidence)}
          </span>
          {beliefFrag && (
            <span
              className="feed__belief"
              title={`belief-driven decision · driving belief ${beliefFrag}`}
            >
              {beliefFrag}
            </span>
          )}
        </span>
      </button>

      {/* THE JUSTIFICATION SEAM. The verdict above is the CONCLUSION; this is the path to the
          witness that DEFENDS it. It was three clicks deep (select → Investigation → interrogate);
          the seam puts it at the verdict's own depth. It is a SIBLING button, never nested inside
          the row button, and it NAVIGATES (onInterrogate → the `aml` view arm), so the feed — and
          every `is_fraud` on it — unmounts: the witness and the label are joined by an ordered
          transition, never co-mounted. Present on EVERY AML row: a MATCH lands on the drawn ring, a
          self-loop/closed-search on an honest "no witness to draw", an INCONCLUSIVE on its named
          boundary account. It never silently no-ops. Card rows have no money-flow graph to witness,
          so they carry no seam — the same reason they carry no basis chip. */}
      {d.aml_transaction_id && (
        <div className="feed__seewhy-wrap">
          <button
            ref={seeWhyRef}
            type="button"
            className="feed__seewhy"
            aria-label="See the witness the graph derived for this transaction"
            onClick={() => onInterrogate(d.aml_transaction_id as UUID)}
          >
            see why <span aria-hidden="true">→</span>
          </button>
        </div>
      )}
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

/** The BASIS chips. Only meaningful under `kind=aml` — a card decision has no witness — so they
 *  appear only there. Each carries its real cluster count, COUNTED (never retyped): this census has
 *  been corrupted by prose three times, once in each available way. The "no cycle" chip covers 463
 *  decisions, of which only 16 were genuinely searched; the other 447 are self-loops, structurally
 *  incapable of forming a cycle, so no search ran. Rung 2's evidence pane renders that fourth state
 *  from /interrogate's `detail` — it is a fact about the evidence, not about the record. */
function WitnessFilter({
  witness,
  onWitness,
  counts,
}: {
  witness: WitnessOutcome | null;
  onWitness: (w: WitnessOutcome | null) => void;
  counts: Loadable<WitnessCounts>;
}) {
  const chips: { label: string; value: WitnessOutcome | null }[] = [
    { label: "any basis", value: null },
    { label: "match", value: "MATCH" },
    { label: "no cycle", value: "CONCLUSIVE_NO" },
    { label: "inconclusive", value: "INCONCLUSIVE" },
  ];

  return (
    <div
      className="feed__filter feed__filter--basis"
      role="group"
      aria-label="Filter AML decisions by the basis of the decision"
    >
      {chips.map((c) => {
        const count =
          c.value !== null && counts.status === "ready"
            ? formatCount(counts.data[c.value])
            : null;
        return (
          <button
            key={c.label}
            type="button"
            className={`feed__chip${witness === c.value ? " feed__chip--on" : ""}`}
            aria-pressed={witness === c.value}
            onClick={() => onWitness(c.value)}
            title={c.value ? BASIS_TITLE[c.value] : "Every AML decision, whatever its basis."}
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
  onInterrogate,
  kind,
  onKind,
  counts,
  witness,
  onWitness,
  witnessCounts,
  loadMore,
  loadingMore,
  returnFocusTxn,
  onFocusReturned,
}: {
  data: DecisionsData;
  selectedId: UUID | null;
  onSelect: (id: UUID) => void;
  /* The justification seam: open the evidence surface for an AML row's transaction. It hands over
     a bare UUID and switches the view — the feed unmounts, so no witness is ever on screen beside
     an `is_fraud`. See DecisionRow's "see why" and NOTES "AML CONSOLE — RUNG 4". */
  onInterrogate: (txnId: UUID) => void;
  kind: DecisionKind | null;
  onKind: (k: DecisionKind | null) => void;
  counts: Loadable<KindCounts>;
  witness: WitnessOutcome | null;
  onWitness: (w: WitnessOutcome | null) => void;
  witnessCounts: Loadable<WitnessCounts>;
  loadMore: () => void;
  loadingMore: boolean;
  returnFocusTxn: UUID | null;
  onFocusReturned: () => void;
}) {
  // TWO DIFFERENT EMPTIES, AND CONFLATING THEM WOULD BE A LIE. An empty FILTER on a populated
  // cluster is not a broken world and must never print restore instructions; only a genuinely
  // empty cluster gets the (single, shared) restore procedure.
  const clusterEmpty = counts.status === "ready" ? counts.data.all === 0 : data.total === 0;
  const loaded = data.decisions.length;
  const remaining = data.total - loaded;

  return (
    <>
      <KindFilter kind={kind} onKind={onKind} counts={counts} />
      {kind === "aml" && (
        <WitnessFilter witness={witness} onWitness={onWitness} counts={witnessCounts} />
      )}
      {clusterEmpty ? (
        <p className="panel__note">
          No decisions on the cluster — <RestoreHint />
        </p>
      ) : data.total === 0 ? (
        <p className="panel__note">
          No matching decisions on the cluster. The other kinds and bases are still there — the
          cluster is populated.
        </p>
      ) : (
        <>
          <ol className="feed">
            {data.decisions.map((d) => (
              <DecisionRow
                key={d.id}
                d={d}
                selected={d.id === selectedId}
                onSelect={onSelect}
                onInterrogate={onInterrogate}
                returnFocusTxn={returnFocusTxn}
                onFocusReturned={onFocusReturned}
              />
            ))}
          </ol>
          {/* The feed is a bounded window over a growing table, so it must SAY how much of the
              match it is showing. Silence here is what hid 1,300 AML decisions — and 50 of the 57
              rings — behind a page that looked complete. */}
          {remaining > 0 ? (
            <div className="feed__more">
              <button
                type="button"
                className="feed__more-btn"
                onClick={loadMore}
                disabled={loadingMore}
              >
                {loadingMore
                  ? "loading…"
                  : `load ${formatCount(Math.min(remaining, PAGE_SIZE))} more`}
              </button>
              <span className="feed__more-n">
                {formatCount(loaded)} of {formatCount(data.total)} · {formatCount(remaining)} not
                yet loaded
              </span>
            </div>
          ) : (
            <p className="feed__more-done">
              all {formatCount(data.total)} loaded
            </p>
          )}
        </>
      )}
    </>
  );
}
