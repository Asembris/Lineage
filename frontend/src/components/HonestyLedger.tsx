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
import type { ProvenanceAuditResponse, UUID } from "../api/types";
import { getBeliefPerformance, getProvenanceAudit } from "../api/client";
import { formatCount } from "../lib/format";
import "./HonestyLedger.css";

const DASH = "—";

/** One ledger row. `note` is prose faithful to the README. `mode` is the provenance
 *  marker; `liveKey` (present only when mode==="live") selects which computed value the
 *  row surfaces under its name. */
type Mode = "live" | "static";
type LiveKey = "genealogy" | "decisions" | "provenance";

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
        fabricated (2 bloodlines, 8 inheritance edges).
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
    item: "decisions / belief_performance",
    label: "measured, reproducible",
    mode: "live",
    liveKey: "decisions",
    note: (
      <>
        A deterministic <code>python -m seed.backfill_decisions</code> repopulates 4,000
        rows + 8 windows (curve conf 0.924 → 0.528, byte-identical every run). This row
        reads live because whether the cluster is currently populated depends on demo
        activity.
      </>
    ),
  },
  {
    item: "Belief embedding vector",
    label: "placeholder → real",
    mode: "static",
    note: (
      <>
        Phase-1 seed uses a deterministic placeholder; real{" "}
        <code>text-embedding-3-small</code> vectors via <code>scripts/embed_beliefs.py</code>.
      </>
    ),
  },
  {
    item: "Item 7 dev-set numbers",
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
    item: "Item 8 GEval rubric",
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
    item: "Item 8 judge",
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
    item: "Regulatory corpus (FATF/FFIEC/FinCEN)",
    label: "not built",
    mode: "static",
    note: (
      <>
        Gated on a <code>data/raw/</code> drop (sources block automated fetch);{" "}
        <code>typology_corpus</code> holds the 4 IBM typology definitions only.
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
    item: "Provenance audit (Item A)",
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
    item: "Counterfactual invalidation (Item B)",
    label: "measured, exact",
    mode: "static",
    note: (
      <>
        <code>GET /beliefs/{"{id}"}/counterfactual-invalidation?at=T</code> returns{" "}
        <b>exact</b> counts (each generation window is exactly 250 rows): N = belief-driven
        approvals withdrawn, M = their real <code>is_fraud</code> subset — reported as
        approvals-withdrawn, never a fabricated “fraud we’d have caught” (the belief only
        ever approves; no faithful per-row fallback verdict exists, so none is invented).
      </>
    ),
  },
  {
    item: "Explanation-faithfulness guard (Item E)",
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
    label: "built, no UI yet",
    mode: "static",
    note: (
      <>
        <code>GET /aml/transactions/{"{id}"}/interrogate</code> (Item 5),{" "}
        <code>/beliefs/{"{id}"}/provenance-audit</code> (Item A),{" "}
        <code>/beliefs/{"{id}"}/counterfactual-invalidation</code> (Item B) are built,
        tested, and verified against real cluster data but have no console surface yet —
        each is a separate plan-gated frontend session; listed here rather than left
        undiscoverable.
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
  const beliefId = beliefs && beliefs.beliefs.length > 0 ? beliefs.beliefs[0].id : undefined;
  const { perfWindows, provenance } = useLedgerLive(beliefId);

  // Per-slot degradation, identical idiom to the Inspector: a not-ready number is "—".
  const n = (v: number | undefined) => (v === undefined ? DASH : formatCount(v));

  const alive = agents?.agents.filter((a) => a.status === "alive").length;
  const decisionsTotal = decisions?.total;
  const perf = perfWindows.status === "ready" ? perfWindows.data : undefined;

  const live: Record<LiveKey, LiveValue> = {
    genealogy: {
      node: (
        <>
          {n(agents?.count)} agents · {n(alive)} alive · {n(beliefs?.count)} belief
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
          <>empty — run seed.backfill_decisions</>
        ) : (
          <>
            {n(decisionsTotal)} decisions · {n(perf)} perf windows
          </>
        ),
    },
    provenance: provenanceValue(provenance),
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

/** The provenance-audit live fact. CLEAN/INCONCLUSIVE stay cold; a genuinely ANOMALOUS
 *  verdict is the one earned --alert on the whole surface (a real tamper signal). */
function provenanceValue(slot: Slot<ProvenanceAuditResponse>): LiveValue {
  if (slot.status !== "ready") return { node: DASH };
  const { status, edge_count, anomaly_count } = slot.data;
  return {
    node: (
      <>
        {status} · {formatCount(edge_count)} edges · {formatCount(anomaly_count)} anomalies
      </>
    ),
    alert: status === "ANOMALOUS",
  };
}
