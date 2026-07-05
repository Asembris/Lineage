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
import type {
  ConsistencySampleEvent,
  ConsistencyStartEvent,
  ConsistencyState,
  ConsistencySummaryEvent,
} from "../api/types";
import { runConsistencyStream, type ConsistencyStreamController } from "../lib/consistencyStream";
import "./ConsistencyDemo.css";

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

/** The observed closure: meter + live counts + the scrolling sample log. Shared by streaming,
 *  done and stopped so the record stays on screen. */
function Observation({
  samples,
  live,
}: {
  samples: ConsistencySampleEvent[];
  live: boolean;
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

  return (
    <div className="cx-obs">
      <DrainMeter open={open} total={total} state={state} />

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
            <li key={s.seq} className={`cx-obs__row cx-obs__row--${s.state}`}>
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

/** The measured contrast. This is the one place the "1" is easy to misrepresent: the 9 and the
 *  18 are real off THIS run's summary; the 1/0 for the atomic path are labeled as a property of
 *  the transaction design (cited to Phase 3's strong-path test), never as this run's measurement. */
function Summary({ summary }: { summary: ConsistencySummaryEvent }) {
  return (
    <div className="cx-sum">
      <h3 className="cx-sum__title">Measured contrast</h3>

      <div className="cx-sum__commit">
        <div className="cx-sum__col cx-sum__col--eventual">
          <span className="cx-sum__n mono">{summary.commit_points}</span>
          <span className="cx-sum__label">commit points · eventual</span>
          <span className="cx-sum__sub">
            measured this run — one commit per holder edge (8) plus the belief row, each externally
            visible
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
            serializable transaction. Phase 3's strong-path test measured it — 1 commit / 0 split
            reads.
          </span>
        </div>
      </div>

      <p className="cx-sum__takeaway">
        Eventual consistency needs one commit per holder, so an intermediate torn closure is
        committed and externally visible —{" "}
        <b className="cx-sum__alert mono">{summary.split_samples} split reads observed here</b>.
        CockroachDB closes the entire inherited closure at a single commit, so that torn state is{" "}
        <b className="cx-sum__alive">structurally unreachable</b>, not merely unlikely.
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
  const ctrl = useRef<ConsistencyStreamController | null>(null);

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
    ctrl.current = runConsistencyStream({
      onStart: (start) => setPhase({ status: "streaming", start, samples: [] }),
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
    });
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
        <h2 className="cx__title">Atomic vs eventual — the closure under observation</h2>
        <p className="cx__lead">
          Invalidating a belief must close its whole inherited closure — every holder edge. This
          streams the <b>real observer samples</b> of the eventually-consistent baseline: one commit
          per holder, so a torn closure is committed and externally visible for a real window.
          CockroachDB does it in a single transaction, so that split state never exists. Samples
          render as they arrive from the live cluster — the multi-second gaps are the real fan-out.
        </p>
      </header>

      <div className="cx__stage">
        {phase.status === "idle" && (
          <div className="cx__panel">
            <button className="cx__run" onClick={arm}>
              Run the consistency proof
            </button>
            <p className="cx__caution">Resets fleet state — details on the next step.</p>
          </div>
        )}

        {phase.status === "arming" && (
          <div className="cx__gate">
            <p className="cx__gate-warn">
              This runs the <b>real destructive demo</b>. It <b>truncates and reseeds the cluster</b>{" "}
              and runs a fleet-wide invalidation to completion. When it finishes the belief is left{" "}
              <b>invalidated</b> and <b>decisions / performance reset to empty</b> — Investigate,
              Trace, Time-travel and Invalidate will read empty until the fleet is re-backfilled (
              <code>python -m seed.backfill_decisions</code>). Only one stream can run at a time.
            </p>
            <div className="cx__gate-actions">
              <button className="cx__confirm" onClick={run}>
                Confirm &amp; run — reset and stream
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
            <Observation samples={phase.samples} live />
            <button className="cx__stop" onClick={stop}>
              Stop
            </button>
          </div>
        )}

        {phase.status === "done" && (
          <div className="cx__running">
            <Observation samples={phase.samples} live={false} />
            <Summary summary={phase.summary} />
            <button className="cx__run" onClick={arm}>
              Run again
            </button>
          </div>
        )}

        {phase.status === "stopped" && (
          <div className="cx__running">
            <p className="cx__note">
              Stopped. The fan-out was interrupted mid-flight, so the closure is left{" "}
              <b>partially invalidated</b> until the next run reseeds it — a real torn state, not a
              clean rollback. {phase.samples.length} samples were observed.
            </p>
            {phase.samples.length > 0 && <Observation samples={phase.samples} live={false} />}
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
