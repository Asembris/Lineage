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
import {
  changeIndices,
  closureView,
  firstSplitIndex,
  splitSampleCount,
  witnessSeq,
  type ClosureView,
} from "../lib/closure";
import { runConsistencyStream, type ConsistencyStreamController } from "../lib/consistencyStream";
import { ConsistencyScene3D } from "./ConsistencyScene3D";
import { RestoreCommands } from "./RestoreHint";
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

/** The closure at rest, shown BEFORE a run has produced any sample (the idle preview, so the
 *  2D/3D toggle has a visible effect before the destructive gate is armed). Same derivation as the
 *  live views, with no sample to render — never a stand-in for a number we lack. */
const REST_VIEW = closureView(null);

const STATE_LABEL: Record<ConsistencyState, string> = {
  ALL_ACTIVE: "all holders active",
  SPLIT: "SPLIT — torn closure, externally visible",
  ALL_INVALIDATED: "all holders corrected",
};

/** The closure drain: `total` cells, `total - open` shown corrected (--alive); open cells read
 *  --alert while the closure is torn (a laggard still live on the dead belief), quiet otherwise.
 *  Renders the SAME derived ClosureView the 3D scene does — one derivation, two presentations.
 *
 *  HOLDER INSPECTION, AND WHY IT LANDS HERE FIRST (design-port S7). The DC's holder rail is
 *  hover-only (`onMouseEnter`/`onMouseLeave`, no click, no key handler) over eight hardcoded agent
 *  handles. Ours is the KEYBOARD-COMPLETE path, per FRONTEND.md: the 3D scene is the gated
 *  enhancement and stays pointer-driven, so the 2D meter is where a holder must be reachable by tab
 *  and enter. Cells become buttons only once `holders` has loaded, and identity is the same REAL
 *  binding the 3D scene uses — GET /beliefs/{id}/lineage ordered by `inherited_at`, which is
 *  load-bearing because the fan-out closes edges ORDER BY inherited_at. Until that lineage resolves
 *  the cells stay unlabelled and inert: no invented identity, ever. */
function DrainMeter({
  view,
  holders = null,
  selected = null,
  onSelect,
}: {
  view: ClosureView;
  holders?: LineageNode[] | null;
  selected?: number | null;
  onSelect?: (i: number | null) => void;
}) {
  const { open, total, state, closed } = view;
  const torn = state === "SPLIT";
  const seq = view.sample?.seq;
  const identified = holders != null && onSelect != null;
  const label = `${open} of ${total} closure edges open, ${state}`;

  return (
    <div
      className="cx-meter"
      {...(identified ? { role: "group", "aria-label": label } : { role: "img", "aria-label": label })}
    >
      {Array.from({ length: total }, (_, i) => {
        const isClosed = i < closed;
        const kind = isClosed ? "closed" : torn ? "split" : "rest";
        const holder = holders?.[i] ?? null;
        const cls = `cx-meter__cell cx-meter__cell--${kind}${selected === i ? " is-selected" : ""}`;

        if (!identified || !holder) return <span key={i} className={cls} />;

        return (
          <button
            key={i}
            type="button"
            className={cls}
            aria-pressed={selected === i}
            onClick={() => onSelect(selected === i ? null : i)}
            // The edge's open/closed state here is AS OF the inspected sample, and the label says
            // so — it is not the run-level "first observed closed at" fact HolderDetail also shows.
            aria-label={`Holder edge ${i + 1} of ${total}, agent ${fragId(holder.agent_id)}, generation ${
              holder.generation
            }, ${isClosed ? "invalidated" : "still live on the belief"}${
              seq !== undefined ? ` as of sample ${seq}` : ""
            }`}
          >
            <span className="cx-meter__id mono">{fragId(holder.agent_id)}</span>
            <span className="cx-meter__gen mono">gen {holder.generation}</span>
          </button>
        );
      })}
    </div>
  );
}

/** REPLAY, NOT SIMULATION — the scrubber over a run's recorded observer samples.
 *
 *  The DC spec (renderConsistency) scrubs `cT`, a requestAnimationFrame playhead over a hardcoded
 *  `cTimeline()` of fabricated milliseconds, deriving holder state from `t >= T.EVH[i]`. There is no
 *  stream behind it, so every position on its timeline is generated on demand.
 *
 *  Ours cannot work that way and must not look like it does. The control is a range over SAMPLE
 *  INDEX with step=1: every reachable position IS a recorded sample, rendered verbatim. There is no
 *  continuous domain, so there is no "between two samples" for the UI to invent a frame in — the
 *  no-synthesized-samples rule is enforced by the control's shape, not by remembering to obey it.
 *
 *  Real cadence is not lost to even index spacing: the readout carries each sample's own
 *  `elapsed_ms` off the wire, so the eventual path's multi-second gaps and the atomic path's
 *  millisecond ones are both visible as you step.
 *
 *  `◀ prev change / next change ▶` seek to samples where the reader observed the closure differ
 *  from the previous read. They are labelled CHANGES, never commits, and are deliberately never
 *  counted — see changeIndices() in lib/closure.ts for why a count would contradict the wire. */
function Scrubber({
  samples,
  index,
  following,
  live,
  onInspect,
  onFollow,
}: {
  samples: ConsistencySampleEvent[];
  index: number;
  following: boolean;
  live: boolean;
  onInspect: (i: number) => void;
  onFollow: () => void;
}) {
  const last = samples.length - 1;
  const current = samples[index];
  const changes = changeIndices(samples);
  const prev = [...changes].reverse().find((i) => i < index);
  const next = changes.find((i) => i > index);
  const splitIdx = firstSplitIndex(samples);
  const splits = splitSampleCount(samples);

  return (
    <div className="cx-scrub">
      <div className="cx-scrub__row">
        <button
          className="cx-scrub__step"
          onClick={() => onInspect(prev ?? 0)}
          disabled={prev === undefined}
          aria-label="Seek to the previous observed change"
        >
          ◀ prev change
        </button>
        <input
          className="cx-scrub__range"
          type="range"
          min={0}
          max={last}
          step={1}
          value={index}
          onChange={(e) => onInspect(Number(e.target.value))}
          aria-label="Observer sample to inspect"
          aria-valuetext={
            current
              ? `sample ${index + 1} of ${samples.length}, ${current.elapsed_ms} milliseconds, ${
                  current.state
                }, ${current.open_edges} of ${current.total_edges} edges open`
              : undefined
          }
        />
        <button
          className="cx-scrub__step"
          onClick={() => onInspect(next ?? last)}
          disabled={next === undefined}
          aria-label="Seek to the next observed change"
        >
          next change ▶
        </button>
      </div>

      <div className="cx-scrub__readout">
        <span className="cx-scrub__at mono">
          sample <b>#{current?.seq}</b> · {current?.elapsed_ms} ms
        </span>
        <span className="cx-scrub__of mono">
          {index + 1} of {samples.length} recorded
        </span>
        {live &&
          (following ? (
            <span className="cx-scrub__live mono">following live</span>
          ) : (
            <button className="cx-scrub__relive" onClick={onFollow}>
              return to live
            </button>
          ))}
      </div>

      {/* EVENTUAL-ONLY BY MEASUREMENT, NOT BY CONFIGURATION. Enabled iff a real SPLIT sample exists
          in what this run recorded — never `strategy === "strong"`. On the atomic path there is no
          torn state to seek to, and the disabled note reports that as the counted result it is. */}
      <div className="cx-scrub__torn-wrap">
        <button
          className="cx-scrub__torn"
          onClick={() => splitIdx !== null && onInspect(splitIdx)}
          disabled={splitIdx === null}
        >
          <span className="cx-scrub__torn-glyph" aria-hidden="true">
            ▰
          </span>
          seek to the torn window
          {splitIdx !== null && (
            <span className="cx-scrub__torn-at mono">
              #{samples[splitIdx].seq} · {samples[splitIdx].elapsed_ms} ms
            </span>
          )}
        </button>
        {splitIdx === null && (
          <p className="cx-scrub__no-torn">
            no torn sample exists in this run — <b className="mono">{splits}</b> of{" "}
            <b className="mono">{samples.length}</b> observer samples
            {live ? " so far" : ""} were <span className="mono">SPLIT</span>. That absence is the
            result, not a missing control.
          </p>
        )}
      </div>
    </div>
  );
}

/** Real forensic detail for one selected holder edge — every field straight off the loaded
 *  GET /beliefs/{id}/lineage node (no placeholder). Shown in BOTH renders (S7): selection is one
 *  shared state, so toggling 2D↔3D keeps the same holder open rather than dropping it.
 *
 *  TWO CLOCKS ON ONE CARD, LABELLED APART. "closure edge" is the edge's state AS OF the sample the
 *  scrubber is parked on; "first observed closed at" is a RUN-LEVEL fact that does not move when
 *  you scrub. They answer different questions and are captioned so they cannot be read as one. */
function HolderDetail({
  holder,
  index,
  view,
  samples,
  onClose,
}: {
  holder: LineageNode;
  index: number;
  view: ClosureView;
  samples: ConsistencySampleEvent[];
  onClose: () => void;
}) {
  const { total, closed } = view;
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
          <dt>
            closure edge
            {view.sample && <span className="cx-detail__asof mono">as of #{view.sample.seq}</span>}
          </dt>
          <dd className={`mono ${isClosed ? "cx-detail__status--dead" : "cx-detail__status--alive"}`}>
            {isClosed ? "invalidated (corrected)" : "open (live on belief)"}
          </dd>
        </div>
        {wsample && (
          <div>
            <dt>
              first observed closed at
              <span className="cx-detail__asof mono">whole run</span>
            </dt>
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
 *  Interaction: `holders` (from the lineage fetch) binds cell/node i to a REAL holder in BOTH
 *  renders. 2D cells are buttons (the keyboard-complete path); the 3D scene stays pointer-driven
 *  (hover a node → the sample that witnessed its edge closing lights up; click → detail). Selection
 *  is ONE shared state, so the toggle preserves it and both renders show the same open holder. */
function Observation({
  samples,
  live,
  render,
  reducedMotion,
  holders,
  hoveredHolder,
  selectedHolder,
  inspected,
  onHover,
  onSelect,
  onInspect,
}: {
  samples: ConsistencySampleEvent[];
  live: boolean;
  render: RenderMode;
  reducedMotion: boolean;
  holders: LineageNode[] | null;
  hoveredHolder: number | null;
  selectedHolder: number | null;
  /** Which recorded sample is being inspected; null = follow the live tail. */
  inspected: number | null;
  onHover: (i: number | null) => void;
  onSelect: (i: number | null) => void;
  onInspect: (i: number | null) => void;
}) {
  const logRef = useRef<HTMLOListElement>(null);
  const following = inspected === null;
  // Keep the newest sample in view as they arrive — but NOT while the user has scrubbed back, or
  // the log would yank itself away from the row they parked on.
  useLayoutEffect(() => {
    if (live && following && logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [samples.length, live, following]);

  // THE single derivation of the closure state on screen (lib/closure.ts). Both the 2D meter and
  // the 3D scene are handed THIS value, so they render one state and cannot drift apart. The
  // scrubber only chooses WHICH recorded sample it derives from — never what is in it.
  const index = following
    ? samples.length - 1
    : Math.min(Math.max(inspected, 0), samples.length - 1);
  const view = closureView(samples[index] ?? null);
  const { total, open, state } = view;

  // Identity binding goes live in BOTH renders once the lineage has loaded. The row a hovered node
  // lights is the sample that witnessed THAT holder's edge closing — a real temporal event.
  const identified = holders !== null;
  const interactive = render === "3d" && identified;
  const witnessOfHovered =
    interactive && hoveredHolder !== null ? witnessSeq(hoveredHolder, samples, total) : null;
  // Holder → sample, the reverse of clicking a row: the sample that recorded THIS holder's edge
  // closing. Works in both renders, since selection is shared.
  const witnessOfSelected =
    identified && selectedHolder !== null ? witnessSeq(selectedHolder, samples, total) : null;
  const selected = selectedHolder !== null ? (holders?.[selectedHolder] ?? null) : null;

  return (
    <div className="cx-obs">
      {render === "3d" ? (
        <ConsistencyScene3D
          view={view}
          reducedMotion={reducedMotion}
          interactive={interactive}
          hoveredIndex={hoveredHolder}
          selectedIndex={selectedHolder}
          onHover={onHover}
          onSelect={onSelect}
        />
      ) : (
        <DrainMeter
          view={view}
          holders={holders}
          selected={selectedHolder}
          onSelect={onSelect}
        />
      )}

      {render === "3d" && (
        <p className="cx3d-hint mono">
          {interactive
            ? "drag to orbit · scroll to zoom · hover a holder to trace its closure · click for detail · pointer-driven — use the 2D view for keyboard access"
            : "drag to orbit · scroll to zoom · holder identity loads on run · pointer-driven — use the 2D view for keyboard access"}
        </p>
      )}

      {samples.length > 0 && (
        <Scrubber
          samples={samples}
          index={index}
          following={following}
          live={live}
          onInspect={onInspect}
          onFollow={() => onInspect(null)}
        />
      )}

      {selected && (
        <HolderDetail
          holder={selected}
          index={selectedHolder as number}
          view={view}
          samples={samples}
          onClose={() => onSelect(null)}
        />
      )}

      <div className="cx-obs__counts" aria-live="polite">
        <span className="cx-obs__count">
          closure <b className="mono">{open}/{total}</b> open
        </span>
        <span className={`cx-obs__state cx-obs__state--${state}`}>
          {state === "SPLIT" && (
            <span className="cx-obs__state-glyph" aria-hidden="true">
              ▰
            </span>
          )}
          {STATE_LABEL[state]}
        </span>
        {state === "SPLIT" && (
          <span className="cx-obs__leak mono">{open} still live on the invalidated belief</span>
        )}
      </div>

      {/* THE SAMPLE LOG IS THE OTHER HALF OF THE SCRUBBER, NOT A SECOND CONTROL. Selecting a row
          writes the SAME `inspected` index the range writes, so the log, the meter/scene and the
          counts cannot drift out of step — there is one value, addressed two ways. (The DC needs
          two fields, `cT` and `cObsSel`, because its playhead is continuous fake time and its
          twelve observer marks are separate fake events; a real sample is both at once.)
          The reverse direction is the witness link: a selected holder lights the row that recorded
          its edge closing — a real temporal event, not a decoration. */}
      <div className="cx-obs__log-wrap">
        <span className="cx-obs__log-head">observer samples · real timing{live ? " · live" : ""}</span>
        <ol className="cx-obs__log" ref={logRef}>
          {samples.map((s, i) => (
            <li key={s.seq}>
              <button
                type="button"
                className={`cx-obs__row cx-obs__row--${s.state}${
                  witnessOfHovered === s.seq || witnessOfSelected === s.seq ? " is-witness" : ""
                }${i === index ? " is-inspected" : ""}`}
                aria-current={i === index ? "true" : undefined}
                onClick={() => onInspect(i)}
              >
                <span className="cx-obs__seq mono">#{s.seq}</span>
                <span className="cx-obs__t mono">{s.elapsed_ms} ms</span>
                <span className="cx-obs__st mono">{s.state}</span>
                <span className="cx-obs__oe mono">
                  {s.open_edges}/{s.total_edges} open
                </span>
              </button>
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

      {/* The DC's per-column verdict line, ported to the MEASURED column only. Rendering it under
          the cited column too would assert a result this run did not measure. Derived from the
          wire's own split_samples, so a strong run earns "no torn frame observed" by measuring 0 —
          it is never printed because the strategy was strong. */}
      <p
        className={`cx-sum__verdict cx-sum__verdict--${
          summary.split_samples > 0 ? "mixed" : "clean"
        }`}
      >
        <span className="cx-sum__verdict-glyph" aria-hidden="true">
          {summary.split_samples > 0 ? "▰" : "✓"}
        </span>
        {summary.split_samples > 0
          ? "mixed state was externally visible"
          : "no torn frame observed"}
      </p>

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

  // Which RECORDED sample the surface is parked on. null = follow the live tail (and, once a run
  // ends, the final sample) — the Phase-4 behaviour. Scrubbing pins an index into `samples`; there
  // is no other addressable position, so there is nothing between two samples to render.
  const [inspected, setInspected] = useState<number | null>(null);

  // S7: leaving 3D no longer drops the SELECTION — the 2D meter inspects holders too, so the
  // selected holder survives the toggle and both renders show the same open detail card. Only the
  // 3D-only HOVER is cleared, since nothing in 2D can clear it once the pointer leaves the canvas.
  useEffect(() => {
    if (render !== "3d") setHoveredHolder(null);
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
    setInspected(null); // a new run replaces the recorded samples the old index pointed into
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
              {/* Pre-run preview: the closure at rest, from the same derivation with no sample yet. */}
              {render === "3d" ? (
                <ConsistencyScene3D view={REST_VIEW} reducedMotion={reducedMotion} />
              ) : (
                <DrainMeter view={REST_VIEW} />
              )}
              <span className="cx__preview-cap mono">
                closure at rest · {REST_VIEW.open}/{REST_VIEW.total} holders live
                {render === "3d" && " · drag to orbit · run the proof to inspect holders"}
              </span>
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
                Time-travel and Invalidate read empty until the fleet is re-backfilled with{" "}
                <RestoreCommands />, <b>in that order</b> (the reseed destroys the 1,500 AML
                decisions too, so the first command alone leaves the grounding seam dead). Only one
                stream can run at a time.
              </p>
            ) : (
              <p className="cx__gate-warn">
                This runs the <b>real destructive demo</b>. It{" "}
                <b>truncates and reseeds the cluster</b> and runs a fleet-wide invalidation to
                completion. When it finishes the belief is left <b>invalidated</b> and{" "}
                <b>decisions / performance reset to empty</b> — Investigate, Trace, Time-travel and
                Invalidate will read empty until the fleet is re-backfilled with <RestoreCommands />,{" "}
                <b>in that order</b> (the reseed destroys the 1,500 AML decisions too, so the first
                command alone leaves the grounding seam dead). Only one stream can run at a time.
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
              inspected={inspected}
              onHover={setHoveredHolder}
              onSelect={setSelectedHolder}
              onInspect={setInspected}
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
              inspected={inspected}
              onHover={setHoveredHolder}
              onSelect={setSelectedHolder}
              onInspect={setInspected}
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
                inspected={inspected}
                onHover={setHoveredHolder}
                onSelect={setSelectedHolder}
                onInspect={setInspected}
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
