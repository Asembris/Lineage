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
 * THE GEOMETRY IS DRAWN (Rung 3), in `WitnessGeometry.tsx`. This file still owns the typographic
 * evidence — the subject, the four verdicts, the named boundary account, the competing structures,
 * and the size and shape of each witness — and it states each witness's shape in words whether or
 * not that shape can be drawn. The drawing is an addition to the record, never a replacement for
 * it: 1,287 of the 1,500 subjects have no structure to draw at all.
 *
 * COLOUR. `--bone` marks what the system is pointing at, and nothing else. There is deliberately no
 * `--alert` anywhere on this surface: "the graph found a structure" and "this is fraud" are
 * different claims, and painting a witness red would fuse them — the oracle-boundary collapse in
 * colour form. Warmth stays Trace's. The world here is cold, and it stays cold.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { DUR, EASE } from "../lib/motion";
import { useInterrogation } from "../hooks/useInterrogation";
import type { AmlAccount, AmlInterrogationResponse, AmlWitness, UUID } from "../api/types";
import {
  BASIS_COUNT,
  BASIS_DETAIL,
  BASIS_HEADLINE,
  BASIS_LABEL,
  BASIS_ORDER,
  BASIS_TOTAL,
  DECIDING_TYPOLOGY,
  ZERO_WITNESS,
  ZERO_WITNESS_PCT,
  basisOf,
  basisPct,
  cycleWitness,
  isSelfLoop,
  ringHops,
} from "../lib/basis";
import type { Basis, RingHop } from "../lib/basis";
import { formatAmount, formatCount, fragId, splitInstant } from "../lib/format";
import { WitnessGeometrySection } from "./WitnessGeometry";
import "./AmlConsole.css";

/* THE PROCEDURE (design-port S8). The DC frames the evidence surface as a 5-phase interrogation.
 * Ported as a STACKED single-scroll procedure — a step rail plus five anchored phase sections, all
 * mounted at once — deliberately, so the witness geometry (`.geo`) and the four-witness list
 * (`.aml__witnesses`) stay in the DOM by default: the geometry guard reaches them with no
 * phase-clicking, and the oracle-boundary sweep reads the WHOLE surface, not just an active phase.
 *
 * Phase 1 is "WITNESS INSPECTOR", not the DC's "RING CLOSURE": only 57 of 1,500 subjects have a ring
 * to close, so the label must be honest for the 85.8% that do not. */
const IV_PHASES = [
  "SUBJECT LOCK",
  "WITNESS INSPECTOR",
  "CENSUS",
  "EVIDENCE VACUUM",
  "FINDING",
] as const;

const IV_SUBTITLES = [
  "lock the subject edge",
  "what the graph witnesses around it",
  "reconcile all 1,500 searches",
  "absence as a measured finding",
  "assemble the conclusion",
];

/** The advance affordance at the foot of each phase; the last phase has none. */
const IV_NEXT = [
  "witness the structure →",
  "reconcile all 1,500 searches →",
  "the evidence vacuum →",
  "assemble the finding →",
  null,
];

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

/** The SHAPE of a witness, stated in words. `WitnessGeometry` draws it; this still says it, because
 *  the sentence is the thing a reader can carry away and a picture is not always available. */
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

/* ============================ THE FIVE PHASES ============================
 * Each renders from the interrogation (and the vetted basis constants) and NOTHING ELSE. No verdict,
 * no belief, no lineage, no label — and the two DC elements that DID read the label (the "252 of 300
 * laundering silently approved" vacuum block and its twin finding line) are CUT, not ported: they
 * would put the answer key on the exam and trip the oracle-boundary sweep. */

/** Phase 0 — SUBJECT LOCK. The real edge, and the DC's "NOT USED" card reframed to OUR withholding:
 *  we do NOT hide account identity (it is masked IBM-synthetic already); we withhold the ground-truth
 *  label and the recorded verdict. That withholding IS the oracle boundary, made visible. */
function SubjectLockPhase({ r }: { r: AmlInterrogationResponse }) {
  return (
    <>
      <SubjectRow r={r} />
      <div className="aml__lock-cards">
        <div className="aml__lock-card">
          <span className="aml__label">observed fact</span>
          <p className="aml__lock-note">
            One real edge — its two masked accounts, amount, format and time, exactly as the extract
            holds them.
          </p>
        </div>
        <div className="aml__lock-card aml__lock-card--withheld">
          <span className="aml__label">not used</span>
          <p className="aml__lock-note">
            The ground-truth label and the recorded verdict are withheld from this surface — it
            receives only a transaction id. Nothing here prejudges the edge.
          </p>
        </div>
      </div>
      <p className="aml__phase-lead">
        The subject is locked as an evidentiary anchor. The interrogation asks only what structure the
        graph can witness around it — never what it is.
      </p>
    </>
  );
}

/** The ring hop inspector (phase 1, RING subjects only). Steps the real ordered hops of the closed
 *  cycle with ◄►/keyboard — every hop a real transaction row, closure the only claim. The DC's
 *  invented per-hop "edge relation"/bank/IBAN is NOT here; we carry only what the row holds. */
function HopInspector({ hops }: { hops: RingHop[] }) {
  const [sel, setSel] = useState(0);
  const n = hops.length;
  const step = (d: number) => setSel((c) => (c + d + n) % n);
  const hop = hops[sel];
  const { date, time } = splitInstant(hop.ts);
  const role = hop.isSubject
    ? "subject edge · leaves the origin"
    : hop.isClosure
      ? "closure · returns to the origin"
      : `intermediate hop ${sel} of ${n}`;

  return (
    <div
      className="aml__hop"
      tabIndex={0}
      role="group"
      aria-label="Ring hop inspector — arrow keys step the cycle"
      onKeyDown={(e) => {
        if (e.key === "ArrowRight") {
          e.preventDefault();
          step(1);
        } else if (e.key === "ArrowLeft") {
          e.preventDefault();
          step(-1);
        }
      }}
    >
      <div className="aml__hop-head">
        <span className="aml__label">
          hop {sel} of {n}
        </span>
        <div className="aml__hop-nav">
          <button type="button" className="aml__hop-btn" aria-label="previous hop" onClick={() => step(-1)}>
            ◄
          </button>
          <button type="button" className="aml__hop-btn" aria-label="next hop" onClick={() => step(1)}>
            ►
          </button>
        </div>
      </div>
      <div className={`aml__hop-card${hop.isSubject ? " aml__hop-card--subject" : ""}`}>
        <div className="aml__hop-flow">
          <AccountTag account={hop.from} id={hop.fromId} />
          <span className="aml__arrow" aria-hidden="true">
            →
          </span>
          <AccountTag account={hop.to} id={hop.toId} />
        </div>
        <span className="aml__hop-role">{role}</span>
        <dl className="aml__kv">
          <div>
            <dt>amount</dt>
            <dd className="aml__mono">{formatAmount(hop.amount, hop.currency)}</dd>
          </div>
          <div>
            <dt>observed</dt>
            <dd className="aml__mono">
              {date} {time}
            </dd>
          </div>
        </dl>
      </div>
      <p className="aml__hop-note">
        Closure is the only claim: the last hop lands back on the account the first one left. Sequence
        is evidence; nothing here judges the transaction.
      </p>
    </div>
  );
}

/** Phase 1 — WITNESS INSPECTOR. This subject's basis, the drawing (`.geo`), the ring hop inspector
 *  (rings only), and the four witnesses. The geometry and the witness list are UNCHANGED from the
 *  flat surface — same DOM, same `.geo`/`.aml__witnesses` the geometry guard measures. */
function WitnessInspectorPhase({ r }: { r: AmlInterrogationResponse }) {
  const hops = ringHops(r);
  return (
    <>
      <BasisBlock r={r} />

      {/* THE GEOMETRY LANDS DIRECTLY UNDER THE SENTENCE IT PROVES. It renders every MATCHING witness
          (competing subjects draw separate figures, never one merged picture), and for the 1,287 of
          1,500 that witness nothing it says so — the negative space is the product, the ring the
          exception. This is where the dominant "no ring to close" case reads as a RESULT. */}
      <WitnessGeometrySection r={r} />

      {hops && <HopInspector hops={hops} />}

      <section className="aml__witnesses">
        <div className="aml__witnesses-head">
          <span className="aml__label">all four witnesses, run against this subject</span>
          {r.has_competing_structure && (
            <span className="aml__competing">
              competing structure · {r.competing_typologies.join(" · ")}
            </span>
          )}
        </div>
        <ul className="aml__witness-list">
          {r.witnesses.map((w) => (
            <WitnessRow key={w.typology} w={w} r={r} />
          ))}
        </ul>
      </section>
    </>
  );
}

/** One census read-state. The four borders keep their idioms (MATCH solid --bone rule, INCONCLUSIVE
 *  dashed --ghost, self-loop dotted, closed-search solid ash) — never smoothed into one "value"
 *  style. The fill reveals via transform:scaleX (a tween); reduced-motion renders it already full. */
function CensusBar({ basis, active }: { basis: Basis; active: boolean }) {
  const reduced = useReducedMotion();
  const widthPct = (BASIS_COUNT[basis] / BASIS_TOTAL) * 100;
  return (
    <li
      className={`aml__census-row aml__census-row--${basis.toLowerCase()}${
        active ? " aml__census-row--active" : ""
      }`}
    >
      <div className="aml__census-head">
        <span className="aml__census-name">{BASIS_LABEL[basis]}</span>
        <span className="aml__census-count">
          {formatCount(BASIS_COUNT[basis])} · {basisPct(basis)}
        </span>
      </div>
      <div className="aml__census-track">
        <motion.div
          className="aml__census-fill"
          style={{ width: `${widthPct}%`, transformOrigin: "left" }}
          initial={reduced ? false : { scaleX: 0 }}
          animate={{ scaleX: 1 }}
          transition={reduced ? { duration: 0 } : { duration: DUR.sweep, ease: EASE.inOut }}
        />
      </div>
      <p className="aml__census-headline">{BASIS_HEADLINE[basis]}</p>
      {active && <p className="aml__census-detail">{BASIS_DETAIL[basis]}</p>}
    </li>
  );
}

/** Phase 2 — CENSUS. All 1,500 searches across the four legitimate read-states, counts from the
 *  vetted BASIS_COUNT and reasons from BASIS_HEADLINE/BASIS_DETAIL — NOT the DC's "why" strings,
 *  which invent a "12-hop budget" and "out-degree = 0" that contradict our real semantics. */
function CensusPhase({ r }: { r: AmlInterrogationResponse }) {
  const active = basisOf(r);
  const parts = BASIS_ORDER.map((b) => BASIS_COUNT[b]);
  const sum = parts.reduce((a, b) => a + b, 0);
  const reconciles = sum === BASIS_TOTAL;
  return (
    <>
      <p className="aml__phase-lead">
        Every AML search resolves into exactly one legitimate read-state.{" "}
        {active ? (
          <>
            This subject resolved to <strong>{BASIS_LABEL[active]}</strong>; here is how all{" "}
            {formatCount(BASIS_TOTAL)} distribute.
          </>
        ) : (
          <>Here is how all {formatCount(BASIS_TOTAL)} distribute.</>
        )}
      </p>
      <ul className="aml__census">
        {BASIS_ORDER.map((b) => (
          <CensusBar key={b} basis={b} active={b === active} />
        ))}
      </ul>
      {/* THE SELF-CHECK COLOUR FIX (design-port S8). The DC turns this total C.alert when it does not
          sum to 1,500 — --alert on the evidence surface, forbidden. It reconciles in --bone, and a
          mismatch would read --ghost: --alert never appears here, in any form, including as a failure
          state. (--alive is not used either — the world here stays cold.) */}
      <div className="aml__recon">
        <span className="aml__label">reconciles to</span>
        <span className={`aml__recon-sum${reconciles ? "" : " aml__recon-sum--off"}`}>
          {parts.join(" + ")} = {formatCount(sum)}
        </span>
      </div>
      <p className="aml__phase-foot">
        No population is hidden or discarded. INCONCLUSIVE is not a negative result — and the two
        CONCLUSIVE_NO states failed for materially different structural reasons.
      </p>
    </>
  );
}

/** One evidence-vacuum meter. Structural, label-free: how much of the extract the graph declines to
 *  answer. --ash fill, --ghost note, NO --alert. The DC's third "operational consequence" meter
 *  ("252 of 300 laundering silently approved") is CUT — it reads the ground-truth label. */
function VacuumMeter({
  label,
  n,
  pct,
  note,
}: {
  label: string;
  n: number;
  pct: string;
  note: string;
}) {
  const reduced = useReducedMotion();
  const widthPct = (n / BASIS_TOTAL) * 100;
  return (
    <div className="aml__vacuum">
      <div className="aml__vacuum-head">
        <span className="aml__vacuum-label">{label}</span>
        <span className="aml__vacuum-count">
          {formatCount(n)} / {formatCount(BASIS_TOTAL)} · {pct}
        </span>
      </div>
      <div className="aml__vacuum-track">
        <motion.div
          className="aml__vacuum-fill"
          style={{ width: `${widthPct}%`, transformOrigin: "left" }}
          initial={reduced ? false : { scaleX: 0 }}
          animate={{ scaleX: 1 }}
          transition={reduced ? { duration: 0 } : { duration: DUR.sweep, ease: EASE.inOut }}
        />
      </div>
      <p className="aml__vacuum-note">{note}</p>
    </div>
  );
}

/** Phase 3 — EVIDENCE VACUUM. Two structural, label-free meters: the could-not-determine majority
 *  (980/1,500) and the zero-witness prevalence (1,287/1,500 — our real, reviewed figure). Absence as
 *  a measured finding, not innocence. */
function VacuumPhase() {
  return (
    <>
      <p className="aml__phase-lead">
        Most searches never establish structure at all. This is not innocence — it is the graph
        declining to answer.
      </p>
      <VacuumMeter
        label="could-not-determine"
        n={BASIS_COUNT.INCONCLUSIVE}
        pct={basisPct("INCONCLUSIVE")}
        note="The search ran off the edge of the extract — unresolved, and distinct from a proven negative. It could not determine."
      />
      <VacuumMeter
        label="zero-witness"
        n={ZERO_WITNESS}
        pct={ZERO_WITNESS_PCT}
        note="No structure to draw at all — no typology witnessed anything. The single most common output of this surface."
      />
    </>
  );
}

interface FindingLine {
  obs: string;
  text: string;
  src: string;
}

/** Phase 4 — FINDING. Each line traces to the search population or structure that produced it. Line
 *  0 is THIS subject's own outcome (a ring closure, or its actual basis). The DC's "silently approved
 *  252 of 300 laundering rows" line is CUT — it reads the label. The closing card is our discipline
 *  verbatim: "no structure to draw" is the finding, not an accusation. */
function FindingPhase({ r }: { r: AmlInterrogationResponse }) {
  const reduced = useReducedMotion();
  const basis = basisOf(r);
  const hops = ringHops(r);

  const lines: FindingLine[] = [];
  if (basis === "MATCH" && hops) {
    lines.push({
      obs: "ring closure",
      text: `This subject closes a directed cycle of ${hops.length} transactions back to its origin account.`,
      src: "MATCH · witnessed structure",
    });
  } else if (basis) {
    lines.push({
      obs: "this subject",
      text: BASIS_HEADLINE[basis],
      src: `${BASIS_LABEL[basis]} · ${formatCount(BASIS_COUNT[basis])} of ${formatCount(BASIS_TOTAL)}`,
    });
  }
  lines.push({
    obs: "witness coverage",
    text: `Only ${formatCount(BASIS_COUNT.MATCH)} of ${formatCount(BASIS_TOTAL)} searches (${basisPct(
      "MATCH",
    )}) ever witness a closed structure at all.`,
    src: `MATCH · ${formatCount(BASIS_COUNT.MATCH)} / ${formatCount(BASIS_TOTAL)}`,
  });
  lines.push({
    obs: "unresolved majority",
    text: `${basisPct(
      "INCONCLUSIVE",
    )} of searches return could-not-determine — the largest of the four read-states.`,
    src: `INCONCLUSIVE · ${formatCount(BASIS_COUNT.INCONCLUSIVE)} / ${formatCount(BASIS_TOTAL)}`,
  });
  lines.push({
    obs: "zero-witness prevalence",
    text: `${formatCount(ZERO_WITNESS)} of ${formatCount(
      BASIS_TOTAL,
    )} subjects (${ZERO_WITNESS_PCT}) have no witnessable structure to draw at all.`,
    src: `zero matching witnesses · ${formatCount(ZERO_WITNESS)} / ${formatCount(BASIS_TOTAL)}`,
  });

  return (
    <>
      <p className="aml__phase-lead">
        The conclusion forms only from established evidence. Each line traces to the search population
        or structure that produced it.
      </p>
      <ol className="aml__finding">
        {lines.map((f, i) => (
          <motion.li
            key={f.obs}
            className={`aml__finding-line${i === lines.length - 1 ? " aml__finding-line--last" : ""}`}
            initial={reduced ? false : { opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={reduced ? { duration: 0 } : { duration: DUR.reveal, ease: EASE.out, delay: i * 0.08 }}
          >
            <span className="aml__finding-obs">{f.obs}</span>
            <p className="aml__finding-text">{f.text}</p>
            <span className="aml__finding-src">┗ traces to · {f.src}</span>
          </motion.li>
        ))}
      </ol>
      <div className="aml__finding-card">
        <p className="aml__finding-headline">
          “No structure to draw” is this surface’s most common output. That is the finding — not an
          empty state, and not an accusation.
        </p>
        <p className="aml__finding-note">
          The system concludes what the graph can and cannot witness. It makes no claim about what this
          transaction is.
        </p>
      </div>
    </>
  );
}

/** The step rail. Colourless props — a phase index and a callback. The active step is emphasised;
 *  reached steps mark done. Clicking a step scrolls its section into view. */
function ProcedureRail({
  phase,
  ivMax,
  onGo,
}: {
  phase: number;
  ivMax: number;
  onGo: (p: number) => void;
}) {
  return (
    <nav className="aml__rail" aria-label="Interrogation procedure">
      <ol className="aml__rail-list">
        {IV_PHASES.map((name, p) => {
          const active = p === phase;
          const done = p < ivMax;
          const reached = p <= ivMax;
          return (
            <li key={name}>
              <button
                type="button"
                className={`aml__rail-step${active ? " aml__rail-step--active" : ""}${
                  reached ? " aml__rail-step--reached" : ""
                }`}
                aria-current={active ? "step" : undefined}
                onClick={() => onGo(p)}
              >
                <span className="aml__rail-num" aria-hidden="true">
                  {done ? "✓" : p + 1}
                </span>
                <span className="aml__rail-text">
                  <span className="aml__rail-name">{name}</span>
                  <span className="aml__rail-sub">{IV_SUBTITLES[p]}</span>
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

/**
 * The evidence pane. Renders from an AmlInterrogationResponse and NOTHING ELSE — this is the
 * component the composition guard exists to protect. It has no access to a verdict, a belief, a
 * lineage, or a label, and it must never acquire one. The five phases are STACKED (all mounted); the
 * rail and the "next" affordance drive an active step and scroll — never an unmount, so the geometry
 * and the four witnesses are always in the DOM and under the oracle-boundary sweep.
 */
export function EvidencePane({ interrogation }: { interrogation: AmlInterrogationResponse }) {
  const reduced = useReducedMotion();
  const [phase, setPhase] = useState(0);
  const [ivMax, setIvMax] = useState(0);
  const sectionRefs = useRef<(HTMLElement | null)[]>([]);

  const go = useCallback(
    (p: number) => {
      setPhase(p);
      setIvMax((m) => Math.max(m, p));
      sectionRefs.current[p]?.scrollIntoView({
        behavior: reduced ? "auto" : "smooth",
        block: "start",
      });
    },
    [reduced],
  );

  const rise = reduced
    ? {}
    : {
        // OPACITY-ONLY reveal (was opacity+translateY). A lingering transform on this container would
        // become the containing block for the sticky procedure rail inside it and break its stick; a
        // fade sets no transform. The app uses NO springs (lib/motion.ts), and DUR.reveal is the same
        // tween every other surface reveal uses.
        initial: { opacity: 0 },
        animate: { opacity: 1 },
        transition: { duration: DUR.reveal, ease: EASE.out },
      };

  const r = interrogation;
  const phaseBody = (p: number) => {
    switch (p) {
      case 0:
        return <SubjectLockPhase r={r} />;
      case 1:
        return <WitnessInspectorPhase r={r} />;
      case 2:
        return <CensusPhase r={r} />;
      case 3:
        return <VacuumPhase />;
      default:
        return <FindingPhase r={r} />;
    }
  };

  return (
    <motion.div className="aml__evidence" {...rise}>
      <div className="aml__procedure">
        <ProcedureRail phase={phase} ivMax={ivMax} onGo={go} />
        <div className="aml__phases">
          {IV_PHASES.map((title, p) => (
            <section
              key={title}
              ref={(el) => {
                sectionRefs.current[p] = el;
              }}
              className={`aml__phase${p === phase ? " aml__phase--active" : ""}`}
              aria-label={`Phase ${p + 1} of 5: ${title}`}
            >
              <header className="aml__phase-head">
                <span className="aml__phase-step">
                  phase {p + 1} / 5
                </span>
                <h2 className="aml__phase-title">{title}</h2>
                <span className="aml__phase-hint">{IV_SUBTITLES[p]}</span>
              </header>
              {phaseBody(p)}
              {IV_NEXT[p] && (
                <div className="aml__phase-next">
                  <button type="button" className="aml__next-btn" onClick={() => go(p + 1)}>
                    {IV_NEXT[p]}
                  </button>
                </div>
              )}
            </section>
          ))}
        </div>
      </div>

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
  const headingRef = useRef<HTMLHeadingElement>(null);

  // THE SEAM'S FOCUS HANDOFF (forward leg). This view is reached by a VIEW TRANSITION: the feed —
  // and the "see why" button that triggered it — unmounts, so focus would otherwise fall to <body>
  // and strand a keyboard user (verified by a tab-walk: it did, on both legs). Move focus to this
  // view's heading, the evidence surface's logical entry, so the change is announced and the
  // surface is navigable. The heading takes tabIndex={-1} (programmatic focus only; not in the tab
  // order). The return leg — restoring focus to the "see why" row — is App's job (returnFocusTxn).
  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  return (
    <div className="aml">
      <header className="aml__head">
        <div>
          <h1 className="aml__title" tabIndex={-1} ref={headingRef}>
            INTERROGATION
          </h1>
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
