/*
 * ConsistencyDemo — Frontend Phase 4. A standalone, FLEET-LEVEL view (it takes over the whole
 * console body, reached by the header toggle) that consumes the REAL GET /demo/consistency/stream
 * SSE endpoint and renders the eventually-consistent fan-out's observer samples as they arrive:
 * the closure draining holder by holder, the SPLIT window opening in real time, and the structural
 * 1-vs-9 commit-point contrast stated plainly.
 *
 * This endpoint is DESTRUCTIVE — every run truncates + reseeds the cluster and runs a real
 * invalidation to completion. So the stream is opened ONLY behind an explicit two-step gate
 * (Run → Confirm), NEVER on mount / view entry, and NEVER auto-reconnects (see
 * lib/consistencyStream.ts). Stop and unmount both abort the fetch.
 *
 * Numbers shown: `open_edges`/`total_edges`/`state`/`elapsed_ms` come straight off each `sample`;
 * `commit_points` (9) and `split_samples` off the `summary`. The contrasting "1 commit" for the
 * atomic path is NOT on this wire — it is labeled as a property of the atomic transaction design,
 * cited to Phase 3's strong-path test, never as a live measurement.
 *
 * Colors: --alert / --alive ONLY, reused the way Invalidate did for split-vs-corrected holders —
 * a closed edge is --alive (corrected), an open edge while the closure is torn is --alert (a
 * laggard still live on the dead belief). No amber/orange (that stays Trace's). Motion is
 * opacity-only and collapses under prefers-reduced-motion.
 */

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useReducedMotion } from "framer-motion";
import type {
  ConsistencySampleEvent,
  ConsistencyStartEvent,
  ConsistencyState,
  ConsistencyStrategy,
  ConsistencySummaryEvent,
  LineageNode,
} from "../api/types";
import { getBeliefLineage } from "../api/client";
import { runConsistencyStream, type ConsistencyStreamController } from "../lib/consistencyStream";
import { ConsistencyScene3D } from "./ConsistencyScene3D";
import { fragId, formatDate } from "../lib/format";
import "./ConsistencyDemo.css";

/** How the live closure is drawn: the Phase-4 2D meter, or the Phase-5 r3f scene. Default 2d
 *  keeps the shipped fallback the default; the toggle only swaps the closure VISUAL — counts,
 *  sample log, summary and the whole destructive lifecycle are shared and unchanged. */
type RenderMode = "2d" | "3d";

/** The closure's real holders, ordered by `inherited_at`. This order is load-bearing and REAL:
 *  the eventual fan-out (app/services/consistency.py) closes edges `ORDER BY inherited_at`, so
 *  holder i (i-th by inherited_at) is exactly the i-th edge to close — which is why node i in the
 *  3D scene can be bound to holders[i] without inventing identity. All 8 inherited_at values are
 *  distinct in the seed, so the order is total and deterministic. */
function sortHolders(path: LineageNode[]): LineageNode[] {
  return path
    .filter((n) => n.from_agent_id !== null)
    .sort((a, b) => (a.inherited_at ?? "").localeCompare(b.inherited_at ?? ""));
}

/** The observer SAMPLE that witnessed holder `i`'s edge closing: the first sample whose
 *  `open_edges` dropped to at most `total-(i+1)` (i.e. holder i and everything earlier is closed).
 *  For strong, open jumps 8→0 in one sample, so every holder resolves to that single commit
 *  sample — honest: they all closed at one commit. Returns the sample's seq, or null if that
 *  holder hasn't closed in the samples seen so far. */
function witnessSeq(i: number, samples: ConsistencySampleEvent[], total: number): number | null {
  const threshold = total - (i + 1);
  const s = samples.find((x) => x.open_edges <= threshold);
  return s ? s.seq : null;
}

type Phase =
  | { status: "idle" }
  | { status: "arming" } // confirm gate — nothing opened yet
  | { status: "reseeding" } // stream open, awaiting the first `start` (destructive reseed running)
  | { status: "streaming"; start: ConsistencyStartEvent; samples: ConsistencySampleEvent[] }
  | {
      status: "done";
      start: ConsistencyStartEvent;
      samples: ConsistencySampleEvent[];
      summary: ConsistencySummaryEvent;
    }
  | { status: "stopped"; samples: ConsistencySampleEvent[] } // user Stop mid-run
  | { status: "busy"; detail: string }
  | { status: "error"; message: string };

const STATE_LABEL: Record<ConsistencyState, string> = {
  ALL_ACTIVE: "all holders active",
  SPLIT: "SPLIT — torn closure, externally visible",
  ALL_INVALIDATED: "all holders corrected",
};

/** The closure drain: `total` cells, `total - open` shown corrected (--alive); open cells read
 *  --alert while the closure is torn (a laggard still live on the dead belief), quiet otherwise.
 *  The stream gives only COUNTS, not holder identities, so the fill order is presentational —
 *  we never label a specific cell as a specific agent. */
function DrainMeter({ open, total, state }: { open: number; total: number; state: ConsistencyState }) {
  const closed = total - open;
  const torn = state === "SPLIT";
  return (
    <div className="cx-meter" role="img" aria-label={`${open} of ${total} closure edges open, ${state}`}>
      {Array.from({ length: total }, (_, i) => {
        const isClosed = i < closed;
        const kind = isClosed ? "closed" : torn ? "split" : "rest";
        return <span key={i} className={`cx-meter__cell cx-meter__cell--${kind}`} />;
      })}
    </div>
  );
}

/** Real forensic detail for one clicked holder edge — every field straight off the loaded
 *  GET /beliefs/{id}/lineage node (no placeholder). Shown only in 3D, only for a selected node. */
function HolderDetail({
  holder,
  index,
  total,
  closed,
  samples,
  onClose,
}: {
  holder: LineageNode;
  index: number;
  total: number;
  closed: number;
  samples: ConsistencySampleEvent[];
  onClose: () => void;
}) {
  const isClosed = index < closed;
  const wseq = witnessSeq(index, samples, total);
  const wsample = wseq != null ? samples.find((s) => s.seq === wseq) : null;
  return (
    <div className="cx-detail">
      <div className="cx-detail__head">
        <span className="cx-detail__title">
          holder edge · <span className="mono">{fragId(holder.agent_id)}</span>
        </span>
        <button className="cx-detail__close" onClick={onClose} aria-label="Close holder detail">
          ✕
        </button>
      </div>
      <dl className="cx-detail__facts">
        <div>
          <dt>agent</dt>
          <dd className="mono">{fragId(holder.agent_id)}</dd>
        </div>
        <div>
          <dt>generation</dt>
          <dd className="mono">gen {holder.generation}</dd>
        </div>
        <div>
          <dt>bloodline</dt>
          <dd className="mono">{holder.bloodline}</dd>
        </div>
        <div>
          <dt>status</dt>
          <dd className={`mono cx-detail__status--${holder.status}`}>{holder.status}</dd>
        </div>
        <div>
          <dt>inherited from</dt>
          <dd className="mono">{holder.from_agent_id ? fragId(holder.from_agent_id) : "—"}</dd>
        </div>
        <div>
          <dt>inherited at</dt>
          <dd className="mono">{holder.inherited_at ? formatDate(holder.inherited_at) : "—"}</dd>
        </div>
        <div>
          <dt>closure edge</dt>
          <dd className={`mono ${isClosed ? "cx-detail__status--dead" : "cx-detail__status--alive"}`}>
            {isClosed ? "invalidated (corrected)" : "open (live on belief)"}
          </dd>
        </div>
        {isClosed && wsample && (
          <div>
            <dt>closed at sample</dt>
            <dd className="mono">
              #{wsample.seq} · {wsample.elapsed_ms} ms
            </dd>
          </div>
        )}
      </dl>
      <p className="cx-detail__src">
        Real closure edge from <code>GET /beliefs/{"{id}"}/lineage</code> · edge {index + 1} of{" "}
        {total} by inheritance order.
      </p>
    </div>
  );
}

/** The observed closure: the closure VISUAL (2D meter or 3D scene) + live counts + the scrolling
 *  sample log. Shared by streaming, done and stopped so the record stays on screen. The visual is
 *  the only thing the render toggle swaps; counts + log are identical in both modes.
 *
 *  Interaction (3D only): `holders` (from the lineage fetch) binds node i to a REAL holder. Hover
 *  a node → the observer-sample row that witnessed that holder's edge closing lights up; click →
 *  its forensic detail. The 2D meter is untouched — it never takes these props. */
function Observation({
  samples,
  live,
  render,
  reducedMotion,
  holders,
  hoveredHolder,
  selectedHolder,
  onHover,
  onSelect,
}: {
  samples: ConsistencySampleEvent[];
  live: boolean;
  render: RenderMode;
  reducedMotion: boolean;
  holders: LineageNode[] | null;
  hoveredHolder: number | null;
  selectedHolder: number | null;
  onHover: (i: number | null) => void;
  onSelect: (i: number | null) => void;
}) {
  const logRef = useRef<HTMLOListElement>(null);
  // Keep the newest sample in view as they arrive. useLayoutEffect so the scroll lands before paint.
  useLayoutEffect(() => {
    if (live && logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [samples.length, live]);

  const latest = samples[samples.length - 1];
  const total = latest?.total_edges ?? 8;
  const open = latest?.open_edges ?? total;
  const state: ConsistencyState = latest?.state ?? "ALL_ACTIVE";
  const closed = total - open;

  // Identity binding is live only in 3D once the lineage has loaded. The row a hovered node
  // lights is the sample that witnessed THAT holder's edge closing — a real temporal event.
  const interactive = render === "3d" && holders !== null;
  const witnessOfHovered =
    interactive && hoveredHolder !== null ? witnessSeq(hoveredHolder, samples, total) : null;
  const selected =
    render === "3d" && selectedHolder !== null ? (holders?.[selectedHolder] ?? null) : null;

  return (
    <div className="cx-obs">
      {render === "3d" ? (
        <ConsistencyScene3D
          samples={samples}
          reducedMotion={reducedMotion}
          interactive={interactive}
          hoveredIndex={hoveredHolder}
          selectedIndex={selectedHolder}
          onHover={onHover}
          onSelect={onSelect}
        />
      ) : (
        <DrainMeter open={open} total={total} state={state} />
      )}

      {render === "3d" && (
        <p className="cx3d-hint mono">
          {interactive
            ? "drag to orbit · scroll to zoom · hover a holder to trace its closure · click for detail"
            : "drag to orbit · scroll to zoom · holder identity loads on run"}
        </p>
      )}

      {selected && (
        <HolderDetail
          holder={selected}
          index={selectedHolder as number}
          total={total}
          closed={closed}
          samples={samples}
          onClose={() => onSelect(null)}
        />
      )}

      <div className="cx-obs__counts" aria-live="polite">
        <span className="cx-obs__count">
          closure <b className="mono">{open}/{total}</b> open
        </span>
        <span className={`cx-obs__state cx-obs__state--${state}`}>{STATE_LABEL[state]}</span>
        {state === "SPLIT" && (
          <span className="cx-obs__leak mono">{open} still live on the invalidated belief</span>
        )}
      </div>

      <div className="cx-obs__log-wrap">
        <span className="cx-obs__log-head">observer samples · real timing{live ? " · live" : ""}</span>
        <ol className="cx-obs__log" ref={logRef}>
          {samples.map((s) => (
            <li
              key={s.seq}
              className={`cx-obs__row cx-obs__row--${s.state}${
                witnessOfHovered === s.seq ? " is-witness" : ""
              }`}
            >
              <span className="cx-obs__seq mono">#{s.seq}</span>
              <span className="cx-obs__t mono">{s.elapsed_ms} ms</span>
              <span className="cx-obs__st mono">{s.state}</span>
              <span className="cx-obs__oe mono">
                {s.open_edges}/{s.total_edges} open
              </span>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}

/** The measured contrast — strategy-aware. Whichever strategy actually ran is labelled
 *  "measured this run" (its numbers come straight off THIS run's `summary`); the OTHER strategy
 *  is the cited structural contrast, never presented as this run's measurement. So:
 *   - eventual run → 9 / N-split measured here, atomic 1 / 0 cited (Phase-4 wording, unchanged);
 *   - strong run   → 1 / 0 measured here (the real atomic endpoint), eventual 9 / split cited.
 *  This keeps the honesty rule symmetric: the atomic "1 / 0" is only ever a live measurement when
 *  the strong strategy was the one that ran. */
function Summary({
  summary,
  strategy,
}: {
  summary: ConsistencySummaryEvent;
  strategy: ConsistencyStrategy;
}) {
  const strong = strategy === "strong";
  return (
    <div className="cx-sum">
      <h3 className="cx-sum__title">Measured contrast</h3>

      <div className="cx-sum__commit">
        {strong ? (
          <>
            <div className="cx-sum__col cx-sum__col--atomic">
              <span className="cx-sum__n mono">{summary.commit_points}</span>
              <span className="cx-sum__label">commit point · atomic (CockroachDB)</span>
              <span className="cx-sum__sub">
                measured this run — the real endpoint path{" "}
                <code>POST /beliefs/{"{id}"}/invalidate</code> closed the whole inherited closure in
                one serializable transaction.
              </span>
            </div>
            <div className="cx-sum__vs" aria-hidden="true">
              vs
            </div>
            <div className="cx-sum__col cx-sum__col--eventual">
              <span className="cx-sum__n mono">9</span>
              <span className="cx-sum__label">commit points · eventual</span>
              <span className="cx-sum__sub">
                the per-holder baseline: one commit per holder edge (8) plus the belief row, each
                externally visible — a real SPLIT window. Switch strategy to <b>Eventual</b> to
                measure it live.
              </span>
            </div>
          </>
        ) : (
          <>
            <div className="cx-sum__col cx-sum__col--eventual">
              <span className="cx-sum__n mono">{summary.commit_points}</span>
              <span className="cx-sum__label">commit points · eventual</span>
              <span className="cx-sum__sub">
                measured this run — one commit per holder edge (8) plus the belief row, each
                externally visible
              </span>
            </div>
            <div className="cx-sum__vs" aria-hidden="true">
              vs
            </div>
            <div className="cx-sum__col cx-sum__col--atomic">
              <span className="cx-sum__n mono">1</span>
              <span className="cx-sum__label">commit point · atomic (CockroachDB)</span>
              <span className="cx-sum__sub">
                a property of the atomic transaction design, not a number off this stream:{" "}
                <code>POST /beliefs/{"{id}"}/invalidate</code> closes the whole closure in one
                serializable transaction. Phase 3's strong-path test measured it — 1 commit / 0
                split reads. Switch strategy to <b>Strong</b> to measure it live.
              </span>
            </div>
          </>
        )}
      </div>

      <p className="cx-sum__takeaway">
        {strong ? (
          <>
            This run closed the whole closure at a single commit —{" "}
            <b className="cx-sum__alive mono">{summary.split_samples} split reads observed</b>. The
            torn state is <b className="cx-sum__alive">structurally unreachable</b>, not merely
            unlikely: snapshot isolation forbids a reader from ever seeing a half-invalidated
            closure. The eventual baseline commits that torn closure and exposes it.
          </>
        ) : (
          <>
            Eventual consistency needs one commit per holder, so an intermediate torn closure is
            committed and externally visible —{" "}
            <b className="cx-sum__alert mono">{summary.split_samples} split reads observed here</b>.
            CockroachDB closes the entire inherited closure at a single commit, so that torn state
            is <b className="cx-sum__alive">structurally unreachable</b>, not merely unlikely.
          </>
        )}
      </p>

      <dl className="cx-sum__facts">
        <div>
          <dt>observer samples</dt>
          <dd className="mono">{summary.total_samples}</dd>
        </div>
        <div>
          <dt>saw ACTIVE → INVALIDATED transition</dt>
          <dd className="mono">{String(summary.saw_transition)}</dd>
        </div>
        <div>
          <dt>run duration</dt>
          <dd className="mono">{summary.elapsed_ms} ms</dd>
        </div>
      </dl>
    </div>
  );
}

export function ConsistencyDemo() {
  const [phase, setPhase] = useState<Phase>({ status: "idle" });
  // The invalidation strategy the NEXT run will use (chosen while idle, echoed by the server's
  // `start`). Default "eventual" keeps the no-arg Phase-4 behavior. The render mode swaps only the
  // closure visual and can flip any time — even on a finished run's stored samples.
  const [strategy, setStrategy] = useState<ConsistencyStrategy>("eventual");
  const [render, setRender] = useState<RenderMode>("2d");
  const reducedMotion = useReducedMotion() ?? false;
  const ctrl = useRef<ConsistencyStreamController | null>(null);

  // Real per-holder identity for the 3D scene: the closure from GET /beliefs/{id}/lineage, ordered
  // by inherited_at (= the backend's fan-out close order). Fetched on `start` (post-reseed, so the
  // edges are this run's). hovered/selected index INTO holders[]; node i in the scene == holders[i].
  const [holders, setHolders] = useState<LineageNode[] | null>(null);
  const [hoveredHolder, setHoveredHolder] = useState<number | null>(null);
  const [selectedHolder, setSelectedHolder] = useState<number | null>(null);

  // Leaving 3D drops the selection so a stale detail panel can't linger over the 2D meter.
  useEffect(() => {
    if (render !== "3d") {
      setSelectedHolder(null);
      setHoveredHolder(null);
    }
  }, [render]);

  // Abort the stream on unmount (navigating back to the console). This is one of the two
  // lifecycle guarantees the destructive endpoint requires — the other is the Stop button.
  useEffect(
    () => () => {
      ctrl.current?.stop();
      ctrl.current = null;
    },
    [],
  );

  const arm = () => setPhase({ status: "arming" });
  const cancel = () => setPhase({ status: "idle" });

  const run = () => {
    ctrl.current?.stop(); // paranoia: never leave a prior reader running
    setPhase({ status: "reseeding" });
    // Fresh run → drop any prior run's identity + selection until this run's lineage loads.
    setHolders(null);
    setHoveredHolder(null);
    setSelectedHolder(null);
    ctrl.current = runConsistencyStream(
      {
        onStart: (start) => {
          setPhase({ status: "streaming", start, samples: [] });
          // Bind real identity: the closure edges for THIS run's belief, ordered by inherited_at.
          // Additive read — does not touch the observer-sample pipeline. Failure leaves the scene
          // non-interactive (holders stays null) rather than blocking the demo.
          getBeliefLineage(start.belief_id)
            .then((lin) => setHolders(sortHolders(lin.path)))
            .catch(() => setHolders(null));
        },
        onSample: (s) =>
          setPhase((prev) =>
            prev.status === "streaming" ? { ...prev, samples: [...prev.samples, s] } : prev,
          ),
        onSummary: (summary) =>
          setPhase((prev) =>
            prev.status === "streaming"
              ? { status: "done", start: prev.start, samples: prev.samples, summary }
              : prev,
          ),
        onBusy: (b) => setPhase({ status: "busy", detail: b.detail }),
        onError: (e) => setPhase({ status: "error", message: e.message }),
      },
      strategy,
    );
  };

  const stop = () => {
    ctrl.current?.stop();
    ctrl.current = null;
    setPhase((prev) =>
      prev.status === "streaming"
        ? { status: "stopped", samples: prev.samples }
        : { status: "stopped", samples: [] },
    );
  };

  return (
    <section className="cx">
      <header className="cx__intro">
        <div className="cx__intro-head">
          <h2 className="cx__title">Atomic vs eventual — the closure under observation</h2>
          <div
            className="cx__render-toggle"
            role="group"
            aria-label="Closure render mode"
          >
            <button
              className="cx__seg"
              aria-pressed={render === "2d"}
              onClick={() => setRender("2d")}
            >
              2D
            </button>
            <button
              className="cx__seg"
              aria-pressed={render === "3d"}
              onClick={() => setRender("3d")}
            >
              3D
            </button>
          </div>
        </div>
        <p className="cx__lead">
          Invalidating a belief must close its whole inherited closure — every holder edge. This
          streams the <b>real observer samples</b> of a live invalidation. The{" "}
          <b>eventual</b> baseline commits one update per holder, so a torn closure is externally
          visible for a real window; the <b>strong</b> path is CockroachDB's real endpoint — one
          serializable transaction, so that split state never exists. Samples render as they arrive
          from the live cluster; the multi-second gaps on the eventual path are the real fan-out.
        </p>
      </header>

      <div className="cx__stage">
        {phase.status === "idle" && (
          <div className="cx__panel">
            <div className="cx__preview" aria-hidden="true">
              {render === "3d" ? (
                <ConsistencyScene3D samples={[]} reducedMotion={reducedMotion} />
              ) : (
                <DrainMeter open={8} total={8} state="ALL_ACTIVE" />
              )}
              <span className="cx__preview-cap mono">closure at rest · 8/8 holders live</span>
            </div>

            <fieldset className="cx__strat">
              <legend className="cx__strat-legend">Strategy to run</legend>
              <label className={`cx__strat-opt${strategy === "eventual" ? " is-on" : ""}`}>
                <input
                  type="radio"
                  name="cx-strategy"
                  checked={strategy === "eventual"}
                  onChange={() => setStrategy("eventual")}
                />
                <span className="cx__strat-name">Eventual baseline</span>
                <span className="cx__strat-desc">
                  per-holder fan-out — opens a real, externally-visible SPLIT window
                </span>
              </label>
              <label className={`cx__strat-opt${strategy === "strong" ? " is-on" : ""}`}>
                <input
                  type="radio"
                  name="cx-strategy"
                  checked={strategy === "strong"}
                  onChange={() => setStrategy("strong")}
                />
                <span className="cx__strat-name">Strong · atomic (CockroachDB)</span>
                <span className="cx__strat-desc">
                  the real <code>POST&nbsp;/beliefs/&#123;id&#125;/invalidate</code> — one
                  serializable commit, no split
                </span>
              </label>
            </fieldset>

            <button className="cx__run" onClick={arm}>
              Run the consistency proof
            </button>
            <p className="cx__caution">
              {strategy === "strong"
                ? "Executes the real fleet-wide invalidation — irreversible. Details on the next step."
                : "Resets fleet state — details on the next step."}
            </p>
          </div>
        )}

        {phase.status === "arming" && (
          <div className={`cx__gate${strategy === "strong" ? " cx__gate--strong" : ""}`}>
            {strategy === "strong" ? (
              <p className="cx__gate-warn">
                <b className="cx__gate-flag">This is the real governed write, not a preview.</b>{" "}
                Confirm executes <code>POST /beliefs/&#123;id&#125;/invalidate</code> — the same
                atomic, fleet-wide invalidation the supervisor Invalidate action performs — against
                the live cluster. The belief is <b>genuinely invalidated across every holder in one
                irreversible commit</b>; there is no rollback. It also <b>truncates and reseeds the
                cluster</b>, leaving <b>decisions / performance empty</b>, so Investigate, Trace,
                Time-travel and Invalidate read empty until the fleet is re-backfilled (
                <code>python -m seed.backfill_decisions</code>). Only one stream can run at a time.
              </p>
            ) : (
              <p className="cx__gate-warn">
                This runs the <b>real destructive demo</b>. It{" "}
                <b>truncates and reseeds the cluster</b> and runs a fleet-wide invalidation to
                completion. When it finishes the belief is left <b>invalidated</b> and{" "}
                <b>decisions / performance reset to empty</b> — Investigate, Trace, Time-travel and
                Invalidate will read empty until the fleet is re-backfilled (
                <code>python -m seed.backfill_decisions</code>). Only one stream can run at a time.
              </p>
            )}
            <div className="cx__gate-actions">
              <button className="cx__confirm" onClick={run}>
                {strategy === "strong"
                  ? "Confirm — invalidate fleet-wide for real"
                  : "Confirm & run — reset and stream"}
              </button>
              <button className="cx__cancel" onClick={cancel}>
                Cancel
              </button>
            </div>
          </div>
        )}

        {phase.status === "reseeding" && (
          <div className="cx__panel">
            <div className="cx__reseed">
              <span className="cx__pulse" aria-hidden="true" />
              <span>reseeding cluster &amp; connecting…</span>
            </div>
            <p className="cx__hint">
              The stream truncates + reseeds before it observes; this can take a few seconds (longer
              if a prior run's schema-change jobs are still settling). No samples until the reseed
              commits.
            </p>
            <button className="cx__stop" onClick={stop}>
              Stop
            </button>
          </div>
        )}

        {phase.status === "streaming" && (
          <div className="cx__running">
            <Observation
              samples={phase.samples}
              live
              render={render}
              reducedMotion={reducedMotion}
              holders={holders}
              hoveredHolder={hoveredHolder}
              selectedHolder={selectedHolder}
              onHover={setHoveredHolder}
              onSelect={setSelectedHolder}
            />
            <button className="cx__stop" onClick={stop}>
              Stop
            </button>
          </div>
        )}

        {phase.status === "done" && (
          <div className="cx__running">
            <Observation
              samples={phase.samples}
              live={false}
              render={render}
              reducedMotion={reducedMotion}
              holders={holders}
              hoveredHolder={hoveredHolder}
              selectedHolder={selectedHolder}
              onHover={setHoveredHolder}
              onSelect={setSelectedHolder}
            />
            <Summary summary={phase.summary} strategy={phase.start.strategy as ConsistencyStrategy} />
            <button className="cx__run" onClick={arm}>
              Run again
            </button>
          </div>
        )}

        {phase.status === "stopped" && (
          <div className="cx__running">
            <p className="cx__note">
              Stopped. The invalidation was interrupted mid-flight, so the closure is left{" "}
              <b>partially invalidated</b> until the next run reseeds it — a real torn state, not a
              clean rollback. {phase.samples.length} samples were observed.
            </p>
            {phase.samples.length > 0 && (
              <Observation
                samples={phase.samples}
                live={false}
                render={render}
                reducedMotion={reducedMotion}
                holders={holders}
                hoveredHolder={hoveredHolder}
                selectedHolder={selectedHolder}
                onHover={setHoveredHolder}
                onSelect={setSelectedHolder}
              />
            )}
            <button className="cx__run" onClick={arm}>
              Run again
            </button>
          </div>
        )}

        {phase.status === "busy" && (
          <div className="cx__panel">
            <p className="cx__note cx__note--busy">
              A consistency stream is already running (another tab or client). Nothing was reseeded.
            </p>
            <p className="cx__hint mono">{phase.detail}</p>
            <button className="cx__run" onClick={arm}>
              Retry
            </button>
          </div>
        )}

        {phase.status === "error" && (
          <div className="cx__panel">
            <p className="cx__note cx__note--err">Stream error: {phase.message}</p>
            <button className="cx__run" onClick={arm}>
              Retry
            </button>
          </div>
        )}
      </div>
    </section>
  );
}
