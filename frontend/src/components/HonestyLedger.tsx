/*
 * HonestyLedger — a first-class, fleet-level credibility surface (Item 9).
 *
 * A standalone header-mode view (alongside Console and the Consistency demo), NOT a
 * region bolted onto a selected row: the ledger describes the WHOLE system's
 * provenance, not one decision/agent — the same "fleet-scoped ⇒ header mode"
 * reasoning Phase 4 used for the Consistency demo.
 *
 * Content mirrors README.md's honesty ledger (the source of truth), row for row, so
 * the doc and the console cannot diverge. The surface is deliberately MIXED-MODE:
 *  - LIVE rows read a real value from the cluster right now (genealogy counts;
 *    decisions/belief_performance populated-or-empty; the top-line provenance-audit
 *    verdict), so the ledger can't silently go stale when someone runs a backfill.
 *  - STATIC rows are permanent methodological facts (the GEval rubric is in-sample;
 *    MCP configured-not-exercised; …) that no endpoint can or should "answer".
 *
 * No new backend: it reuses GET /agents, /beliefs (already loaded by the console) plus
 * two existing reads for the one belief — GET /beliefs/{id}/performance and the
 * zero-argument GET /beliefs/{id}/provenance-audit (a data-point, not a per-edge UI).
 *
 * Discipline: clinical, cold, calm — a credibility surface, not a dashboard. The
 * LIVE/STATIC marker is a cold provenance tag (--bone / --ash), deliberately NOT the
 * --alive/--alert vocabulary the feed and closure-state use, so it never reads as a
 * second alert system. --alert appears on exactly one value and only when earned: a
 * genuinely ANOMALOUS provenance verdict (a real tamper signal). Every live number
 * degrades to "—" on a not-ready/error slot, matching the Inspector's per-slot idiom.
 */

import { useEffect, useState, type ReactNode } from "react";
import type { Loadable } from "../hooks/useConsoleData";
import type { AgentsData, BeliefsData, DecisionsData } from "../hooks/useConsoleData";
import type { ProvenanceAuditResponse, UUID, WitnessOutcome } from "../api/types";
import { countDecisions, getBeliefPerformance, getProvenanceAudit } from "../api/client";
import { formatCount } from "../lib/format";
import { RestoreCommands, RestoreHint } from "./RestoreHint";
import "./HonestyLedger.css";

const DASH = "—";

/* The restore procedure's ONE definition now lives in ./RestoreHint — it moved out of this
 * component when a SEVENTH instruction site turned up in DecisionFeed's empty state ("rerun the
 * backfill", singular: the destructive half-restore). The ledger's two empty states render the
 * same imported definition they always did; see RestoreHint.tsx for the full history. */

/** One ledger row. `note` is prose faithful to the README. `mode` is the provenance
 *  marker; `liveKey` (present only when mode==="live") selects which computed value the
 *  row surfaces under its name. */
type Mode = "live" | "static";
type LiveKey = "genealogy" | "decisions" | "seam" | "provenance";

interface RowSpec {
  item: string;
  label: string;
  note: ReactNode;
  mode: Mode;
  liveKey?: LiveKey;
}

/** The rows, in README order. Notes are kept faithful to README.md's honesty ledger. */
const ROWS: RowSpec[] = [
  {
    item: "Agent genealogy",
    label: "synthetic",
    mode: "live",
    liveKey: "genealogy",
    note: (
      <>
        Deterministically seeded; the inheritance edges are real rows, the population is
        fabricated (2 bloodlines, 15 inheritance edges — 8 crimson + 7 azure).
      </>
    ),
  },
  {
    item: "AML transactions",
    label: "real + sampled",
    mode: "static",
    note: (
      <>
        Real IBM HI-Small AML data (648 accounts / 1,500 edges / 20 instances / 300
        members); benign negatives are <code>is_laundering=0</code> rows <em>anchored to
        the same accounts</em> as the fraud (deliberately adversarial), capped 4:1.
      </>
    ),
  },
  {
    item: "Grounding seam — the AML belief's decisions",
    label: "real evidence, disclosed coverage",
    mode: "live",
    liveKey: "seam",
    note: (
      <>
        1,500 decisions cite real IBM <code>aml_transactions</code> rows through a
        database-enforced FK, and their <code>is_fraud</code> is the real{" "}
        <code>is_laundering</code>. <b>Two witness outcomes both map to <code>approve</code>, and
        the verdict alone cannot tell them apart:</b> <code>CONCLUSIVE_NO</code> (there is no
        cycle) and <code>INCONCLUSIVE</code> (the search ran off the edge of the 1,500-edge slice
        and <em>could not determine</em>). <code>INCONCLUSIVE → approve</code> is a{" "}
        <b>disclosed modeling choice</b>, not a corner case: it is the majority of the extract, and
        it silently approves most of its laundering rows. <em>(Its share is an unrelated quantity
        from the structural detector's 65.3% hold-out recall — same number, different thing.)</em>{" "}
        <b>
          And <code>CONCLUSIVE_NO</code> is not one thing: only 16 of its 463 were actually
          searched.
        </b>{" "}
        The other 447 are <b>self-loops</b> — an account paying itself, which is not a transfer, so
        it is excluded from the graph's adjacency and <em>no search ever ran</em>. Its gloss said
        otherwise for the whole life of the seam. The count was never wrong; its description of
        itself was.{" "}
        The belief's honest failure mode is <b>constant, not stale</b> — its error rate is flat
        over time, so <code>belief_performance</code> is deliberately <em>not</em> computed for it
        (an AML rot curve would be a base-rate artifact of the ingestion sample).{" "}
        <b>The numbers to the left are COUNTED from the cluster, not quoted</b> — this census has
        now been corrupted by prose three times: once misstated, once falsely sourced, and once
        misdescribing its own complement. It is no longer entrusted to prose.
      </>
    ),
  },
  {
    item: "Ground-truth label (the oracle)",
    label: "never to the decider; served only as an audit fact",
    mode: "static",
    note: (
      <>
        Stated precisely, because the flat version — <em>“the label is read only by tests”</em> —{" "}
        <b>is false, and this document said it until the read surface was built.</b> The seam
        decided on all 1,500 edges 1:1, so <code>decisions.is_fraud</code> is a copy of{" "}
        <code>is_laundering</code> for the whole extract and <code>GET /decisions</code> serves it.
        Three things are true, and each is <em>enforced</em>, not asserted: the label is{" "}
        <b>never readable by the decider</b> (its entire input is a graph whose edge type has no
        label field; an AST tripwire pins this in Python <em>and</em> in raw SQL, because a label
        reaching the witness through a <code>SELECT</code> string edits no syntax node); it is{" "}
        <b>never served as evidence</b> (<code>/aml/transactions/{"{id}"}</code> and{" "}
        <code>/interrogate</code> project no label column, and a test asserts{" "}
        <code>is_laundering</code> is absent from the body); and it is served{" "}
        <b>only where it is an audit fact</b> — attached to a decision already made without it, by
        a two-phase backfill that computes every verdict before the label query runs at all.{" "}
        <code>is_fraud</code> is the <em>scorecard</em>; <code>is_laundering</code> on the
        interrogation surface would be the <em>answer key shown during the exam</em>.
      </>
    ),
  },
  {
    item: "decisions / belief_performance",
    label: "measured, reproducible",
    mode: "live",
    liveKey: "decisions",
    note: (
      <>
        This row reads live because whether the cluster is currently populated depends on
        demo activity — the destructive invalidation demo consumes it. Restoring it is a{" "}
        <b>two-command procedure, and the order is not free.</b>{" "}
        <b>
          <code>python -m seed.backfill_decisions</code> alone is NOT a restore:
        </b>{" "}
        it opens with a reseed, and <code>seed.seed()</code> <b>DELETEs every decision</b> —
        so on its own it repopulates the 4,000 card rows + 8 windows (curve conf 0.924 →
        0.528, byte-identical every run) <em>and silently destroys the 1,500 AML decisions
        in the grounding-seam row above.</em> Run <RestoreCommands />, in that order.
        (It was three commands until 2026-07-13; the third re-embedded the beliefs, and the
        seed now plants their real vectors itself.)
      </>
    ),
  },
  {
    item: "Belief embedding vector",
    label: "both real — a reseed can no longer undo it",
    mode: "static",
    note: (
      <>
        <strong>This row was FALSE until 2026-07-13, and how it went false is the point.</strong> It
        read <em>"crimson: placeholder · azure: real"</em> — but measured on the live cluster,{" "}
        <strong>both</strong> beliefs sat at cosine distance <strong>0.000000000</strong> from{" "}
        <code>seed.placeholder_embedding(1536)</code>. Both were placeholders. The cause was not
        carelessness: <code>seed.seed()</code> re-planted the placeholder on <em>every</em> reseed,
        and the fix was an <em>instruction</em> — "afterwards, run{" "}
        <code>scripts/embed_beliefs.py</code>" — sitting third in a three-command restore procedure.
        An instruction that must be remembered on every rebuild is not a fix, and this one failed
        exactly as you would predict. <strong>The seed now plants the real</strong>{" "}
        <code>text-embedding-3-small</code> <strong>vector itself</strong>, from a committed fixture
        (<code>seed/belief_embeddings.json</code>), and the seed stays OpenAI-free.{" "}
        <code>tests/test_belief_embeddings.py</code> reseeds the cluster and then <em>proves</em> the
        stored vectors are not the placeholder — the regression is unrepresentable rather than
        documented, and the restore procedure lost a command (three → two).
      </>
    ),
  },
  {
    item: "Detection eval — dev-set numbers",
    label: "in-sample",
    mode: "static",
    note: (
      <>
        Selection decisions (<code>FLAG_CAPABLE</code>, SG tightening) were made on this
        set; the hold-out is the never-tuned figure.
      </>
    ),
  },
  {
    item: "Faithfulness eval — GEval rubric",
    label: "partly in-sample",
    mode: "static",
    note: (
      <>
        Rubric iterated on 5 of the calibration examples; generalizes 5/5 on fresh authored
        negatives, but “never tuned” is false for the calibration subset.
      </>
    ),
  },
  {
    item: "Faithfulness eval — judge",
    label: "open-model",
    mode: "static",
    note: (
      <>
        Ollama gemma / NVIDIA nemotron — never OpenAI; unreliable on dense
        structural-reasoning prose (disclosed).
      </>
    ),
  },
  {
    item: "MCP Server / ccloud CLI",
    label: "configured, not exercised",
    mode: "static",
    note: (
      <>
        MCP Server declared in <code>.mcp.json</code>; verification done via direct SQL
        probes; ccloud CLI not used.
      </>
    ),
  },
  {
    item: "Vector indexes — 2 of the 3 could never be used by their own queries. Dropped, not repaired",
    label: "shipped defect · found, measured, corrected (migration 0010)",
    mode: "static",
    note: (
      <>
        The most damaging thing this project got wrong, and it survived every review because
        four documents agreed with each other and nobody ran the check.{" "}
        <b>
          <code>beliefs</code> (0002)
        </b>{" "}
        and{" "}
        <b>
          <code>typology_corpus</code> (0005)
        </b>{" "}
        declared a bare <code>CREATE VECTOR INDEX … (embedding)</code>; CockroachDB's default
        opclass is <code>vector_l2_ops</code>, which accelerates <code>&lt;-&gt;</code> <em>only</em>,
        while both queries rank with cosine <code>&lt;=&gt;</code>. Neither index had <b>ever</b>{" "}
        appeared in a query plan. The true observation ("the plan is a full scan") had been recorded
        with a <b>false cause</b> ("only 4 rows") — in NOTES, in README, in ARCHITECTURE, and in a{" "}
        <em>passing verification script</em>.{" "}
        <b>
          The obvious repair — flip the opclass — is INERT, and believing otherwise was the second
          false claim about this defect.
        </b>{" "}
        A CRDB vector index is selected only when every <em>prefix column</em> is constrained;{" "}
        <code>(embedding)</code> has none; and both real queries carry a <code>WHERE</code>. So the
        flip changes no plan — measured, <code>scripts/probe_vector_opclass.py</code>. The one repair
        that <em>would</em> have worked for <code>typology_corpus</code> — a{" "}
        <code>(source, embedding vector_cosine_ops)</code> prefix index — was <b>rejected</b>: it
        activates an <em>approximate</em> search, and the brake's Gate 0 is a set-membership test on
        exactly the top-3 of a <code>k=3</code> retrieval over a <em>four</em>-document corpus. It
        measures safe today (<b>0</b> top-3 changes over 1,572 adversarial near-ties <em>and</em>{" "}
        132 real agent queries; <b>0</b> Gate 0 flips) — but only because four rows sit in one
        C-SPANN partition. That is luck of scale, not a property of the design. <b>So both were
        dropped.</b> What remains is checkable and true: real <code>VECTOR(1536)</code> columns, real
        cosine search, <em>exact</em> by scan, and <b>one</b> genuinely-exercised distributed vector
        index (<code>regulatory_corpus</code>, 233 rows — <code>EXPLAIN</code> emits a real{" "}
        <code>vector search</code> node). Correctness was never affected.
      </>
    ),
  },
  {
    item: "Regulatory-corpus fidelity gate — the 0.95 floor",
    label: "chosen threshold, not derived",
    mode: "static",
    note: (
      <>
        Declared, because this project <b>rejected MARGIN_FLOOR</b> for being exactly this: a
        hand-picked constant. <code>0.95</code> is a round number chosen <em>a priori</em>, before
        any coverage was measured — not derived from the corpus, not re-tuned to fit the results,
        but not implied by anything about these five documents either. The measured margin is real
        and <b>asymmetric</b>: the worst <em>real</em> red-flag line scores <b>0.973</b> (only{" "}
        <b>+0.023</b> of headroom) while a plausible <em>fabricated</em> red flag scores{" "}
        <b>0.189</b>. So the gate is far likelier to <b>false-alarm on a legitimate re-parse</b>{" "}
        than to miss invented text — the right bias, since a false alarm sends a human to read a
        diff and a miss ships fabricated regulatory language that reads as authoritative. Unlike
        MARGIN_FLOOR it <b>authorizes nothing at runtime</b>: it gates whether a corpus may{" "}
        <em>ship</em>, on a quantity that <em>is</em> the thing being asserted — not a verdict, on
        a quantity measured to be irrelevant to it.
      </>
    ),
  },
  {
    item: "Regulatory corpus (FATF/FFIEC/FinCEN)",
    label: "real primary sources, curated extraction",
    mode: "static",
    note: (
      <>
        <b>233 red-flag entries</b> from five authoritative PDFs — FFIEC Appendix F (129), FATF
        Virtual Assets (59), FATF/Egmont TBML (33), FinCEN FIN-2014-A005 (5), FIN-2010-A001 (7) —
        parsed with <b>LlamaParse</b> (parser only), chunked structure-aware with each entry's
        section path prepended before embedding, and stored in <b>its own table</b> on the same
        cluster and MVCC timeline. Every entry is <b>verbatim regulator text</b>:{" "}
        <code>verify_regulatory.py</code> re-extracts each PDF independently and proves all 233
        trace back to it — and the gate is shown to <em>trip</em>, a plausible invented red flag
        scoring 0.189 against a floor of 0.95. It is a <b>separate table from</b>{" "}
        <code>typology_corpus</code> on purpose: that table's <code>typology</code> is a validated
        join key into <code>aml_pattern_instances</code>, and red flags do not map to IBM's four
        typologies. <b>The brake cannot see this corpus, structurally</b> —{" "}
        <code>retrieve_typology()</code> says <code>FROM typology_corpus</code> and physically
        cannot return a red flag. <b>A retrieved red flag is context, never evidence</b>: it can
        never authorize a FLAG. Only a structural witness over the money-flow graph can.
      </>
    ),
  },
  {
    item: "Certificate authorship",
    label: "integrity, not authorship",
    mode: "static",
    note: (
      <>
        <code>content_hash</code> is an unkeyed sha256 — it proves integrity + (within the
        GC window) AOST-reproducibility, not authorship; asymmetric signing is documented,
        not built.
      </>
    ),
  },
  {
    item: "Staleness curve — uncertainty",
    label: "measured, with its interval",
    mode: "static",
    note: (
      <>
        The certificate's <code>0.924 → 0.528</code> is no longer a bare point estimate:
        every window carries the sample size behind it and a 95% Wilson interval (present
        day is <code>0.528</code>, CI <code>[0.466, 0.589]</code>). <code>n</code> is{" "}
        <b>re-aggregated from <code>decisions</code></b>, not persisted —{" "}
        <code>belief_performance</code> still has no denominator column, and the five-table
        schema is unchanged. If a persisted confidence stops reproducing from the decisions
        it summarizes, the intervals are <b>withheld</b> rather than pairing a fresh
        denominator with a stale estimate. A measured decay is asserted only when a Fisher
        exact test supports it — there is <b>no minimum-sample gate</b>, so a one-decision
        window disqualifies itself on the evidence.
      </>
    ),
  },
  {
    item: "Staleness uncertainty — not cross-checked",
    label: "shared computation, not an independent check",
    mode: "static",
    note: (
      <>
        The certifier Lambda computes the intervals with the <i>same shared function</i>, but
        it does <b>not</b> re-derive and compare them the way it does the closure hash — and
        that is deliberate. A confidence interval is arithmetic over <code>(k, n)</code>, not
        a claim about the world, so there is no independent oracle to check it against; both
        halves read <code>belief_performance</code> at current committed state, so neither is
        a check on the other. A <code>staleness_verification: agreed</code> block would
        fabricate the <i>appearance</i> of the closure hash's guarantee while proving nothing.
      </>
    ),
  },
  {
    item: "Provenance-integrity audit",
    label: "verification, not a patch",
    mode: "live",
    liveKey: "provenance",
    note: (
      <>
        The two legitimate <code>belief_inheritance</code> writers preserve the A1–A4
        invariants by construction, so <b>no live vulnerability exists</b>; the audit is
        verification + out-of-band tamper detection. OWASP <code>ASI06</code>
        primary-verified; MITRE ATLAS <code>AML.T0080</code> <b>secondary-sourced</b>, not
        confirmed on the authoritative page.
      </>
    ),
  },
  {
    item: "Counterfactual invalidation query",
    label: "measured, exact — verdict-aware",
    mode: "static",
    note: (
      <>
        <code>GET /beliefs/{"{id}"}/counterfactual-invalidation?at=T</code> returns{" "}
        <b>exact</b> counts, not estimates, split by the verdict that actually happened:{" "}
        <b>N</b> = belief-driven <em>approvals</em> withdrawn, <b>M</b> = their real{" "}
        <code>is_fraud</code> subset (the harm), plus <code>withdrawn_blocks</code> and{" "}
        <code>frauds_caught_by_block</code> — what invalidating would <em>forfeit</em>. No
        fabricated “fraud we’d have caught”: no faithful per-row fallback verdict exists, so
        none is invented. <b>Correction:</b> this row previously asserted “the belief only
        ever approves”. That was true of the crimson card belief and is <b>false of the
        fleet</b> — the azure laundering belief <b>blocks</b> (57 of its 1,500 decisions).
        Under the old un-split aggregate the endpoint counted the 43 laundering rows it
        correctly <em>blocked</em> as auto-approved frauds, crediting its catches as its
        harms. Fixed, with a regression test that fails against the old aggregate. A belief
        with no measured windows returns <code>windows: null</code>, never a grid of zeros.
      </>
    ),
  },
  {
    item: "Explanation-faithfulness guard",
    label: "probabilistic guard",
    mode: "static",
    note: (
      <>
        Scores the agent’s <code>rationale</code> against the exact evidence it saw;{" "}
        <code>SUPPORTED</code> means “passed the check”, <b>not “proven faithful”</b>
        (documented false-negatives). Cites OWASP <code>LLM09:2025 Misinformation</code>;{" "}
        <b>explicitly not</b> a retrieval/memory-poisoning defense — it checks prose against
        retrieved rows, not whether those rows are poisoned.
      </>
    ),
  },
  {
    item: "Interrogate / provenance-audit / counterfactual endpoints",
    label: "one now has a console; two do not",
    mode: "static",
    note: (
      <>
        <code>GET /aml/transactions/{"{id}"}/interrogate</code> <b>now has a console surface</b> —
        the AML console renders it (the evidence pane, the witness geometry drawn from the cited
        rows, and the &ldquo;see why&rdquo; seam on every AML feed row). This row read{" "}
        <em>&ldquo;no UI yet&rdquo;</em> for the entire life of that console — the STATIC-prose rot
        this ledger exists to catch.{" "}
        <code>/beliefs/{"{id}"}/provenance-audit</code> is surfaced only as this ledger&rsquo;s own
        top-line verdict (a data-point, not a per-edge UI).{" "}
        <code>/beliefs/{"{id}"}/counterfactual-invalidation</code> still has <b>no</b> console
        surface — listed here rather than left undiscoverable.
      </>
    ),
  },
];

/** A live fetch slot for the two per-belief reads. */
type Slot<T> =
  | { status: "idle" } // no belief id yet (catalog not ready / empty)
  | { status: "loading" }
  | { status: "error" }
  | { status: "ready"; data: T };

/** The seam's census, COUNTED from the cluster — never quoted.
 *
 *  Seven `total`s at limit=1 (no rows fetched): the extract size, each witness outcome, and each
 *  outcome's laundering subset. This is the ledger's thesis applied to the one number that most
 *  needs it: the 65.3% INCONCLUSIVE→approve disclosure has been corrupted by prose TWICE — once
 *  misstated (the phantom "728 / 48.5%") and once falsely sourced (a `verify_seam` script that
 *  never existed). Prose has failed this number in both available ways. A number read from the
 *  cluster cannot be wrong about the cluster.
 *
 *  Degrades to "—" as one unit: a PARTIAL census would be worse than none, because a missing
 *  denominator turns a disclosure into a boast. */
interface SeamCensus {
  total: number;
  byOutcome: Record<WitnessOutcome, { n: number; laundering: number }>;
}

const OUTCOMES: WitnessOutcome[] = ["MATCH", "CONCLUSIVE_NO", "INCONCLUSIVE"];

function useSeamCensus(): Slot<SeamCensus> {
  const [slot, setSlot] = useState<Slot<SeamCensus>>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    setSlot({ status: "loading" });

    Promise.all([
      countDecisions({ kind: "aml" }),
      ...OUTCOMES.map((o) => countDecisions({ kind: "aml", witnessOutcome: o })),
      ...OUTCOMES.map((o) =>
        countDecisions({ kind: "aml", witnessOutcome: o, isFraud: true }),
      ),
    ])
      .then(([total, ...rest]) => {
        if (cancelled) return;
        const byOutcome = Object.fromEntries(
          OUTCOMES.map((o, i) => [o, { n: rest[i], laundering: rest[i + OUTCOMES.length] }]),
        ) as SeamCensus["byOutcome"];
        setSlot({ status: "ready", data: { total, byOutcome } });
      })
      .catch(() => !cancelled && setSlot({ status: "error" }));

    return () => {
      cancelled = true;
    };
  }, []);

  return slot;
}

/** Reads the two per-belief live facts (performance window count + provenance verdict)
 *  for the one seeded belief. Both degrade to a slot that renders "—", never throwing. */
function useLedgerLive(beliefId: UUID | undefined): {
  perfWindows: Slot<number>;
  provenance: Slot<ProvenanceAuditResponse>;
} {
  const [perfWindows, setPerfWindows] = useState<Slot<number>>({ status: "idle" });
  const [provenance, setProvenance] = useState<Slot<ProvenanceAuditResponse>>({
    status: "idle",
  });

  useEffect(() => {
    if (!beliefId) {
      setPerfWindows({ status: "idle" });
      setProvenance({ status: "idle" });
      return;
    }
    let cancelled = false;
    setPerfWindows({ status: "loading" });
    setProvenance({ status: "loading" });

    getBeliefPerformance(beliefId)
      .then((res) => !cancelled && setPerfWindows({ status: "ready", data: res.count }))
      .catch(() => !cancelled && setPerfWindows({ status: "error" }));

    getProvenanceAudit(beliefId)
      .then((res) => !cancelled && setProvenance({ status: "ready", data: res }))
      .catch(() => !cancelled && setProvenance({ status: "error" }));

    return () => {
      cancelled = true;
    };
  }, [beliefId]);

  return { perfWindows, provenance };
}

function ready<T>(slot: Loadable<T>): T | undefined {
  return slot.status === "ready" ? slot.data : undefined;
}

/** A live value plus whether it carries an earned alert (a real ANOMALOUS verdict). */
interface LiveValue {
  node: ReactNode;
  alert?: boolean;
}

export function HonestyLedger(props: {
  agents: Loadable<AgentsData>;
  decisions: Loadable<DecisionsData>;
  beliefs: Loadable<BeliefsData>;
}) {
  const agents = ready(props.agents);
  const decisions = ready(props.decisions);
  const beliefs = ready(props.beliefs);

  // The one seeded belief — the subject of the two per-belief live reads. There is
  // exactly one belief in the data model; the genealogy row reports the live count.
  // The two per-belief live reads (performance windows, provenance verdict) describe ONE belief,
  // and the fleet now holds two. Picking `beliefs[0]` silently meant "crimson" only by UUID sort
  // luck (both beliefs share a formed_at, so list_beliefs falls through to an id tiebreak). A
  // credibility surface must not depend on that: the belief is chosen explicitly, and its label
  // is rendered next to the values so the row cannot silently come to mean a different belief.
  const ledgerBelief =
    beliefs?.beliefs.find((b) => b.rule_text.includes("merchant category")) ??
    beliefs?.beliefs[0];
  const beliefId = ledgerBelief?.id;
  const beliefTag = ledgerBelief
    ? ledgerBelief.rule_text.includes("merchant category")
      ? "crimson card belief"
      : "azure laundering belief"
    : undefined;
  const { perfWindows, provenance } = useLedgerLive(beliefId);
  const seam = useSeamCensus();

  // Per-slot degradation, identical idiom to the Inspector: a not-ready number is "—".
  const n = (v: number | undefined) => (v === undefined ? DASH : formatCount(v));

  const alive = agents?.agents.filter((a) => a.status === "alive").length;
  const decisionsTotal = decisions?.total;
  const perf = perfWindows.status === "ready" ? perfWindows.data : undefined;

  const live: Record<LiveKey, LiveValue> = {
    genealogy: {
      node: (
        <>
          {n(agents?.count)} agents · {n(alive)} alive ·{" "}
          {n(beliefs?.count)} {beliefs?.count === 1 ? "belief" : "beliefs"}
        </>
      ),
    },
    decisions: {
      // Populated-or-empty is the whole point of this row being live. Both counts
      // degrade to "—"; a real 0/0 reads as an honest empty state, not an error.
      node:
        decisionsTotal === undefined && perf === undefined ? (
          DASH
        ) : decisionsTotal === 0 && perf === 0 ? (
          <RestoreHint />
        ) : (
          <>
            {n(decisionsTotal)} decisions · {n(perf)} perf windows
            {beliefTag ? ` (${beliefTag})` : ""}
          </>
        ),
    },
    seam: seamValue(seam),
    provenance: provenanceValue(provenance, beliefTag),
  };

  return (
    <div className="ledger">
      <div className="ledger__intro">
        <h2 className="ledger__title">Honesty ledger</h2>
        <p className="ledger__lead">
          Every claim this system makes, labeled by provenance — real, synthetic,
          measured, assumption. <b>LIVE</b> rows read a value from the cluster right now;{" "}
          <b>STATIC</b> rows are permanent methodological facts. Mirrors the repository
          README so the doc and this console never disagree.
        </p>
        <div className="ledger__legend" aria-hidden="true">
          <span className="ledger__mode ledger__mode--live">LIVE</span>
          <span className="ledger__legend-txt">read from the cluster now</span>
          <span className="ledger__mode ledger__mode--static">STATIC</span>
          <span className="ledger__legend-txt">permanent methodological fact</span>
        </div>
      </div>

      <div
        className="ledger__scroll"
        tabIndex={0}
        role="region"
        aria-label="Honesty ledger — read-only provenance record"
      >
        <ol className="ledger__list">
          <li className="ledger-row ledger-row--head" aria-hidden="true">
            <span className="ledger-row__item">Item</span>
            <span className="ledger-row__label-col">Label</span>
            <span className="ledger-row__note">Note</span>
            <span className="ledger-row__mode-col">Provenance</span>
          </li>
          {ROWS.map((row) => {
            const value = row.liveKey ? live[row.liveKey] : undefined;
            return (
              <li key={row.item} className={`ledger-row ledger-row--${row.mode}`}>
                <div className="ledger-row__item">
                  <span className="ledger-row__name">{row.item}</span>
                  {value && (
                    <span
                      className={`ledger-row__value${value.alert ? " ledger-row__value--alert" : ""}`}
                    >
                      {value.node}
                    </span>
                  )}
                </div>
                <div className="ledger-row__label-col">
                  <span className="ledger-row__label">{row.label}</span>
                </div>
                <p className="ledger-row__note">{row.note}</p>
                <div className="ledger-row__mode-col">
                  <span className={`ledger__mode ledger__mode--${row.mode}`}>
                    {row.mode === "live" ? "LIVE" : "STATIC"}
                  </span>
                </div>
              </li>
            );
          })}
        </ol>
      </div>
    </div>
  );
}

/** The seam's census, rendered from COUNTS — the disclosure, read live rather than retyped.
 *
 *  The share and the laundering total are DERIVED from the counts here (not carried as constants),
 *  so the rendered "65.3%" is arithmetic over live numbers and cannot drift from them. If the
 *  cluster has no AML decisions the row says so plainly — an honest empty state, not a "0.0%". */
function seamValue(slot: Slot<SeamCensus>): LiveValue {
  if (slot.status !== "ready") return { node: DASH };
  const { total, byOutcome } = slot.data;
  if (total === 0) {
    return { node: <RestoreHint /> };
  }
  const inc = byOutcome.INCONCLUSIVE;
  const share = ((inc.n / total) * 100).toFixed(1);
  const laundering = OUTCOMES.reduce((s, o) => s + byOutcome[o].laundering, 0);
  return {
    node: (
      <>
        {formatCount(total)} decisions · {formatCount(byOutcome.MATCH.n)} MATCH ·{" "}
        {formatCount(byOutcome.CONCLUSIVE_NO.n)} CONCLUSIVE_NO ·{" "}
        {formatCount(inc.n)} INCONCLUSIVE → <b>{share}% could not determine</b>, silently approving{" "}
        <b>{formatCount(inc.laundering)}</b> of {formatCount(laundering)} laundering rows
      </>
    ),
  };
}

/** The provenance-audit live fact. CLEAN/INCONCLUSIVE stay cold; a genuinely ANOMALOUS
 *  verdict is the one earned --alert on the whole surface (a real tamper signal). */
function provenanceValue(
  slot: Slot<ProvenanceAuditResponse>,
  beliefTag?: string,
): LiveValue {
  if (slot.status !== "ready") return { node: DASH };
  const { status, edge_count, anomaly_count } = slot.data;
  return {
    node: (
      <>
        {status} · {formatCount(edge_count)} edges · {formatCount(anomaly_count)} anomalies
        {beliefTag ? ` (${beliefTag})` : ""}
      </>
    ),
    alert: status === "ANOMALOUS",
  };
}
