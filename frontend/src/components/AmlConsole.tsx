/*
 * AmlConsole — the EVIDENCE surface. The first witness pixel in this project.
 *
 * ============================ WHY THIS IS A VIEW, NOT A PANE ============================
 * It takes over the console body. It is not a fourth region beside the feed and the Inspector,
 * and that is the single most important decision in this file.
 *
 * The audit layer (verdict, witness_outcome, is_fraud, the driving belief, the lineage) and the
 * evidence layer (what structure the graph actually found) were already one component tree apart:
 * `DecisionFeed` and `Investigation` both render `is_fraud` today, harmlessly, ONLY because no
 * witness was on screen. Draw the witness beside them and the console shows the answer key next to
 * the exam — and no single component would have received both props, so a composition guard alone
 * would have stayed green while the thing it protects was broken in pixels.
 *
 * Ground truth is meaningful ONLY because the witness never saw it. CYCLE's honest 75.4% precision
 * (14 of the 57 edges it fires on are benign) is a fact a reader can only evaluate if they cannot
 * already see which of the two they are looking at. Print the label beside the witness's work and
 * the reader can no longer tell detection from lookup.
 *
 * So the two layers are joined by an ORDERED REVEAL, never by adjacency. Whitespace is not the
 * mechanism; SEQUENCE is. This surface is mounted alone; the audit layer is not mounted at all
 * while it is up; and the outcome arrives afterwards, as the SCORE of what the reader just watched,
 * never as an input to it. (That reveal is Rung 4. This rung ships the exam.)
 *
 * THE JOIN IS A BARE `UUID`. App holds both layers in state — it is the composition root and it
 * must — and it hands this surface a transaction id and nothing else. An id carries no verdict and
 * no ground truth. Everything below is rendered from GET /aml/transactions/{id}/interrogate ALONE.
 *
 * None of the above is a promise. `frontend/scripts/composition-guard.mjs` walks the TypeScript
 * type graph and fails the build if any component's prop surface reaches both layers, if this
 * module imports the audit layer through any channel, or if an audit component is ever mounted in
 * the same JSX subtree as this one.
 *
 * NO GEOMETRY. The ring, the legs and the bundle are Rung 3. What ships here is typographic: the
 * subject, the four verdicts, the named boundary account, the competing structures, and the size
 * and SHAPE of each witness — never its drawing.
 *
 * COLOUR. `--bone` marks what the system is pointing at, and nothing else. There is deliberately no
 * `--alert` anywhere on this surface: "the graph found a structure" and "this is fraud" are
 * different claims, and painting a witness red would fuse them — the oracle-boundary collapse in
 * colour form. Warmth stays Trace's. The world here is cold, and it stays cold.
 */

import { motion, useReducedMotion } from "framer-motion";
import { useInterrogation } from "../hooks/useInterrogation";
import type { AmlAccount, AmlInterrogationResponse, AmlWitness, UUID } from "../api/types";
import {
  BASIS_COUNT,
  BASIS_DETAIL,
  BASIS_HEADLINE,
  BASIS_LABEL,
  DECIDING_TYPOLOGY,
  basisOf,
  cycleWitness,
  isSelfLoop,
} from "../lib/basis";
import { formatAmount, formatCount, fragId, splitInstant } from "../lib/format";
import "./AmlConsole.css";

/** An account, named. Node identity in this graph is the compound (bank, account) — never the
 *  account number alone, which is not unique across banks. */
function AccountTag({ account, id }: { account: AmlAccount | undefined; id: UUID }) {
  if (!account) return <span className="aml__acct aml__acct--unresolved">{fragId(id)}</span>;
  return (
    <span className="aml__acct">
      <span className="aml__bank">{account.bank}</span>
      <span className="aml__acct-no">{account.account}</span>
    </span>
  );
}

/** The subject: one real edge of the money-flow graph, resolved to its row. */
function SubjectRow({ r }: { r: AmlInterrogationResponse }) {
  const s = r.subject;
  const { date, time } = splitInstant(s.ts);
  const selfLoop = isSelfLoop(r);
  return (
    <section className="aml__subject">
      <div className="aml__subject-head">
        <span className="aml__label">subject</span>
        <span className="aml__txn">txn {fragId(s.id)}</span>
      </div>
      <div className="aml__flow">
        <AccountTag account={r.accounts[s.from_account_id]} id={s.from_account_id} />
        <span className="aml__arrow" aria-hidden="true">
          →
        </span>
        <AccountTag account={r.accounts[s.to_account_id]} id={s.to_account_id} />
        {/* The self-loop is a property of these two ids and nothing else. It is not an annotation
            the backend sent — it is the same predicate the graph decides on, re-derived here. */}
        {selfLoop && <span className="aml__selfloop">same account</span>}
      </div>
      <dl className="aml__kv">
        <div>
          <dt>amount</dt>
          <dd className="aml__mono">{formatAmount(s.amount_paid, s.payment_currency)}</dd>
        </div>
        <div>
          <dt>format</dt>
          <dd className="aml__mono">{s.payment_format}</dd>
        </div>
        <div>
          <dt>observed</dt>
          <dd className="aml__mono">
            {date} {time}
          </dd>
        </div>
      </dl>
    </section>
  );
}

/** THE BASIS — four states, and a self-loop must never render like a closed search. */
function BasisBlock({ r }: { r: AmlInterrogationResponse }) {
  const basis = basisOf(r);
  const cycle = cycleWitness(r);
  if (!basis || !cycle) return null;

  const boundary = cycle.boundary_account_id
    ? r.accounts[cycle.boundary_account_id]
    : undefined;

  return (
    <section className={`aml__basis aml__basis--${basis.toLowerCase()}`}>
      <div className="aml__basis-head">
        <span className="aml__label">basis · {DECIDING_TYPOLOGY}</span>
        <span className="aml__basis-count">
          {formatCount(BASIS_COUNT[basis])} of 1,500
        </span>
      </div>
      <h2 className="aml__basis-label">{BASIS_LABEL[basis]}</h2>
      <p className="aml__basis-headline">{BASIS_HEADLINE[basis]}</p>
      <p className="aml__basis-detail">{BASIS_DETAIL[basis]}</p>

      {/* "We ran off the edge of the data" is always renderable as a PLACE: all 980 INCONCLUSIVE
          rows name a boundary account, and no other basis has one. That categorical difference is
          total in the data, and the console must not collapse it. */}
      {cycle.boundary_account_id && (
        <div className="aml__boundary">
          <span className="aml__label">the search stopped here</span>
          <AccountTag account={boundary} id={cycle.boundary_account_id} />
          <p className="aml__boundary-note">
            This account&rsquo;s outgoing edges are not in the extract. Beyond it, the graph does
            not know — so no negative can be honest.
          </p>
        </div>
      )}
    </section>
  );
}

/** The SHAPE of a witness, stated — never drawn. The drawing is Rung 3. */
const KIND_NOTE: Record<string, string> = {
  RING: "a closed ring — contiguous, and it returns to its source",
  LEGS: "two parallel routes — not one path",
  BUNDLE: "a real edge set with no single traversal",
  NONE: "no structure",
};

function WitnessRow({ w, r }: { w: AmlWitness; r: AmlInterrogationResponse }) {
  const boundary = w.boundary_account_id ? r.accounts[w.boundary_account_id] : undefined;
  const size = w.transaction_ids.length;
  return (
    <li className={`aml__witness aml__witness--${w.outcome.toLowerCase()}`}>
      {/* TYPOLOGY LEFT, OUTCOME RIGHT, CAPABILITY BELOW — a fixed two-row head.
          Driving it caught the reason: with all three on one wrapping flex row, the outcome chip
          landed on line 1 for STACK and wrapped to line 2 for GATHER-SCATTER, so the same fact sat
          in a different place on each of the four cards and the row could not be read across. The
          four witnesses are meant to be COMPARED; a layout that moves the comparison key per card
          defeats that. */}
      <div className="aml__witness-head">
        <span className="aml__typology">{w.typology}</span>
        <span className="aml__outcome">{w.outcome.toLowerCase().replace("_", " ")}</span>
      </div>
      {/* FLAG-CAPABLE is a measured property of this extract (the witness never fires on an edge
          belonging to a different typology), not a design choice. A witness that is not
          flag-capable is still real evidence — it simply may not authorize a flag on its own. Both
          states are legible: an asymmetry where only one of a binary fact passes contrast would
          make the negative case quietly harder to read than the positive one. */}
      <span
        className={`aml__capable${w.flag_capable ? " aml__capable--yes" : ""}`}
        title={
          w.flag_capable
            ? "flag-capable: this witness never fires on an edge belonging to a different typology"
            : "not flag-capable here: it may not authorize a flag on its own"
        }
      >
        {w.flag_capable ? "flag-capable" : "not flag-capable"}
      </span>
      <p className="aml__witness-detail">{w.detail}</p>
      <div className="aml__witness-foot">
        {size > 0 && (
          <span className="aml__witness-size">
            {size} {size === 1 ? "transaction" : "transactions"}
            {w.kind !== "NONE" && <span className="aml__witness-kind"> · {KIND_NOTE[w.kind]}</span>}
          </span>
        )}
        {w.boundary_account_id && (
          <span className="aml__witness-boundary">
            stopped at <AccountTag account={boundary} id={w.boundary_account_id} />
          </span>
        )}
      </div>
    </li>
  );
}

/**
 * The evidence pane. Renders from an AmlInterrogationResponse and NOTHING ELSE — this is the
 * component the composition guard exists to protect. It has no access to a verdict, a belief, a
 * lineage, or a label, and it must never acquire one.
 */
export function EvidencePane({ interrogation }: { interrogation: AmlInterrogationResponse }) {
  const reduced = useReducedMotion();
  const rise = reduced
    ? {}
    : {
        initial: { opacity: 0, y: 6 },
        animate: { opacity: 1, y: 0 },
        transition: { type: "spring" as const, stiffness: 260, damping: 30 },
      };

  return (
    <motion.div className="aml__evidence" {...rise}>
      <SubjectRow r={interrogation} />
      <BasisBlock r={interrogation} />

      <section className="aml__witnesses">
        <div className="aml__witnesses-head">
          <span className="aml__label">all four witnesses, run against this subject</span>
          {/* Competing structure is SURFACED, never used to suppress anything. More than one
              witness means the structural evidence genuinely supports more than one story — the
              honest face of a detector, and the reader is owed it. */}
          {interrogation.has_competing_structure && (
            <span className="aml__competing">
              competing structure · {interrogation.competing_typologies.join(" · ")}
            </span>
          )}
        </div>
        <ul className="aml__witness-list">
          {interrogation.witnesses.map((w) => (
            <WitnessRow key={w.typology} w={w} r={interrogation} />
          ))}
        </ul>
      </section>

      <p className="aml__provenance">
        Every row above is a real edge of the IBM AML extract, re-derived from the unlabeled graph
        by GET /aml/transactions/{fragId(interrogation.transaction_id)}/interrogate. No model was
        called. No ground truth was read.
      </p>
    </motion.div>
  );
}

/**
 * The evidence VIEW. Owns the fetch, and hands the pane a resolved interrogation.
 *
 * Its entire prop surface is a transaction id and a way back. That is the seam: App knows which
 * decision the supervisor was looking at, and passes down an ID — never the decision.
 */
export function AmlConsole({ txnId, onClose }: { txnId: UUID; onClose: () => void }) {
  const state = useInterrogation(txnId);

  return (
    <div className="aml">
      <header className="aml__head">
        <div>
          <h1 className="aml__title">INTERROGATION</h1>
          <p className="aml__sub">
            what the graph witnesses around one real transaction — and nothing about what it is
          </p>
        </div>
        <button type="button" className="aml__close" onClick={onClose}>
          ← back to the console
        </button>
      </header>

      {state.status === "loading" && <p className="aml__note">Interrogating the graph…</p>}
      {state.status === "error" && (
        <p className="aml__note aml__note--error">
          {state.code === 404
            ? "This transaction is not in the AML evidence layer."
            : state.message}
        </p>
      )}
      {state.status === "ready" && <EvidencePane interrogation={state.data} />}
    </div>
  );
}
