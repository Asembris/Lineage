/*
 * TimeTravel — the third supervisor interaction, inside the Investigation surface.
 *
 * It combines the project's TWO real time signals, honestly kept apart (they are
 * different clocks — see NOTES §"Time concepts"; conflating them is the trap):
 *
 *   1. Real MVCC deposition (GET /agents/{id}/beliefs?as_of=). A genuine AS OF
 *      SYSTEM TIME read proves time-travel is real AND that the belief ROW is
 *      immutable: deposed at a past instant and at present, its status is the same
 *      (ACTIVE). AOST depth is GC-bounded (~75 min), so this cannot reach the
 *      formation date — that historical truth lives in signal 2, not here.
 *
 *   2. Real measured staleness curve (GET /beliefs/{id}/performance). The ordered
 *      belief_performance windows: confidence high WHEN FORMED (first window,
 *      --alive) decaying to low PRESENT DAY (last window, --alert), frauds_approved
 *      rising. This is DERIVED from decisions, never asserted.
 *
 * The thesis this makes visible: the belief is the same immutable row — MVCC proves
 * it never changed — yet it rotted, because staleness is measured performance
 * against a drifting world, not a mutated field.
 *
 * No amber/orange here — that warmth is reserved for Trace. Healthy vs. stale is
 * --alive vs. --alert. Motion is opacity/pathLength only, reduced-motion respected.
 */

import { useEffect, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { DUR, EASE } from "../lib/motion";
import type { Investigation } from "../lib/investigation";
import type { BeliefPerformanceWindow, UUID } from "../api/types";
import { getAgentBeliefs, getBeliefPerformance } from "../api/client";
import { fragId, formatConfidence, formatCount, formatDate } from "../lib/format";
import "./TimeTravel.css";

/** One deposition: was the belief present, and with what status, at a given time. */
interface Depo {
  label: string; // the real timestamp read, or "present"
  held: boolean;
  status: string | null; // 'active' | 'invalidated' | null (not held)
}

type State =
  | { status: "closed" }
  | { status: "loading" }
  | { status: "error"; message: string }
  | {
      status: "ready";
      windows: BeliefPerformanceWindow[];
      depoPast: Depo;
      depoNow: Depo;
    };

/** Resolve one belief's presence/status within a deposition response. */
function depoFor(beliefs: { id: UUID; status: string }[], beliefId: UUID, label: string): Depo {
  const b = beliefs.find((x) => x.id === beliefId);
  return { label, held: !!b, status: b?.status ?? null };
}

/** SVG sparkline of the confidence curve — a fixed [0,1] domain so the decay is
 *  shown at true scale, never exaggerated by auto-fitting. Draws left→right with
 *  a --alive→--alert gradient (healthy early, stale late). */
function Curve({
  windows,
  activeIndex,
  reduce,
}: {
  windows: BeliefPerformanceWindow[];
  activeIndex: number;
  reduce: boolean;
}) {
  const W = 100;
  const H = 34;
  const padX = 3;
  const padY = 4;
  const n = windows.length;
  const x = (i: number) => (n <= 1 ? W / 2 : padX + (i / (n - 1)) * (W - 2 * padX));
  const y = (c: number) => padY + (1 - c) * (H - 2 * padY); // domain [0,1]
  const pts = windows.map((w, i) => `${x(i)},${y(w.confidence)}`);
  const d = `M ${pts.join(" L ")}`;

  return (
    <svg
      className="tt__curve"
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      role="img"
      aria-label="confidence across generation windows, healthy to stale"
    >
      <defs>
        <linearGradient id="tt-grad" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="var(--alive)" />
          <stop offset="100%" stopColor="var(--alert)" />
        </linearGradient>
      </defs>
      <motion.path
        d={d}
        className="tt__curve-line"
        stroke="url(#tt-grad)"
        initial={reduce ? { pathLength: 1 } : { pathLength: 0 }}
        animate={{ pathLength: 1 }}
        transition={{ duration: reduce ? 0 : DUR.sweep, ease: EASE.inOut }}
      />
      {windows.map((w, i) => (
        <circle
          key={i}
          cx={x(i)}
          cy={y(w.confidence)}
          r={i === activeIndex ? 2.6 : 1.4}
          className={
            "tt__dot" +
            (i === 0 ? " tt__dot--alive" : "") +
            (i === n - 1 ? " tt__dot--alert" : "") +
            (i === activeIndex ? " tt__dot--active" : "")
          }
        />
      ))}
    </svg>
  );
}

export function TimeTravel({ inv }: { inv: Investigation }) {
  const reduce = useReducedMotion() ?? false;
  const belief = inv.belief;
  const [state, setState] = useState<State>({ status: "closed" });
  const [toggle, setToggle] = useState<"formed" | "present">("formed");

  // Any change of investigated belief/agent resets the panel to closed so a stale
  // curve never lingers over a new investigation.
  const beliefId = belief?.id;
  const agentId = inv.decision.agent_id;
  useEffect(() => {
    setState({ status: "closed" });
    setToggle("formed");
  }, [beliefId, agentId]);

  if (!belief) return null;

  const open = async () => {
    setState({ status: "loading" });
    try {
      // A real past instant within the GC window (the deepest AOST can honestly
      // reach) + the present read. Two genuine AS OF SYSTEM TIME calls.
      const isoPast = new Date(Date.now() - 20_000).toISOString();
      const [perf, past, now] = await Promise.all([
        getBeliefPerformance(belief.id),
        getAgentBeliefs(agentId, isoPast),
        getAgentBeliefs(agentId),
      ]);
      setState({
        status: "ready",
        windows: perf.windows,
        depoPast: depoFor(past.beliefs, belief.id, isoPast),
        depoNow: depoFor(now.beliefs, belief.id, "present"),
      });
    } catch (err) {
      setState({ status: "error", message: err instanceof Error ? err.message : String(err) });
    }
  };

  if (state.status === "closed") {
    return (
      <button className="tt__open-btn" onClick={open}>
        Time-travel ⟲
      </button>
    );
  }
  if (state.status === "loading") {
    return (
      <button className="tt__open-btn" disabled>
        Deposing cluster…
      </button>
    );
  }
  if (state.status === "error") {
    return (
      <div className="tt">
        <p className="panel__note tt__note">Time-travel failed: {state.message}</p>
        <button className="tt__open-btn" onClick={open}>
          Retry
        </button>
      </div>
    );
  }

  const { windows, depoPast, depoNow } = state;
  if (windows.length === 0) {
    return (
      <div className="tt">
        <p className="panel__note tt__note">
          No measured performance windows for this belief yet — nothing to time-travel.
        </p>
      </div>
    );
  }

  const first = windows[0];
  const last = windows[windows.length - 1];
  const active = toggle === "formed" ? first : last;
  const activeIndex = toggle === "formed" ? 0 : windows.length - 1;
  const tone = toggle === "formed" ? "alive" : "alert";

  return (
    <div className="tt">
      {/* The toggle — WHEN FORMED (first window) vs PRESENT DAY (last window). */}
      <div className="tt__toggle" role="group" aria-label="time-travel point">
        <button
          className={"tt__toggle-btn" + (toggle === "formed" ? " tt__toggle-btn--on" : "")}
          aria-pressed={toggle === "formed"}
          onClick={() => setToggle("formed")}
        >
          when formed
        </button>
        <button
          className={"tt__toggle-btn" + (toggle === "present" ? " tt__toggle-btn--on" : "")}
          aria-pressed={toggle === "present"}
          onClick={() => setToggle("present")}
        >
          present day
        </button>
      </div>

      {/* Measured staleness readout for the selected end — the healthy→stale shift. */}
      <motion.div
        key={toggle}
        className={`tt__readout tt__readout--${tone}`}
        initial={reduce ? false : { opacity: 0, y: 4 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: reduce ? 0 : DUR.reveal, ease: EASE.out }}
      >
        <div className="tt__conf">
          <span className={`tt__conf-val tt__hot-${tone}`}>{formatConfidence(active.confidence)}</span>
          <span className="tt__conf-label">measured confidence</span>
        </div>
        <div className="tt__facts">
          <span className="tt__window">
            {formatDate(active.window_start)} → {formatDate(active.window_end)}
          </span>
          <span className="tt__frauds">
            <span className="tt__frauds-n">{formatCount(active.frauds_approved)}</span> frauds
            approved
          </span>
        </div>
      </motion.div>

      <Curve windows={windows} activeIndex={activeIndex} reduce={reduce} />

      <p className="tt__derivation">
        confidence {formatConfidence(first.confidence)} → {formatConfidence(last.confidence)} across{" "}
        {windows.length} measured windows — derived from decisions, not asserted.
      </p>

      {/* The MVCC deposition — proof time-travel is real, and the row is immutable. */}
      <div className="tt__depo">
        <div className="tt__depo-head">MVCC deposition · real AS OF SYSTEM TIME</div>
        <DepoRow label={`as of ${depoPast.label.slice(0, 19)}Z`} depo={depoPast} />
        <DepoRow label="present" depo={depoNow} />
        <p className="tt__depo-note">
          Same belief <span className="tt__mono">{fragId(belief.id)}</span>, both reads — the row is
          immutable across MVCC time (AOST reaches ~75 min; formation-era history is the measured
          curve above, a different clock).
        </p>
      </div>
    </div>
  );
}

function DepoRow({ label, depo }: { label: string; depo: Depo }) {
  return (
    <div className="tt__depo-row">
      <span className="tt__mono tt__depo-ts">{label}</span>
      <span className="tt__depo-arrow" aria-hidden="true">
        →
      </span>
      {depo.held ? (
        <span className="tt__depo-status">
          held · <span className={`tt__depo-badge tt__depo-badge--${depo.status}`}>{depo.status}</span>
        </span>
      ) : (
        <span className="tt__depo-status tt__depo-status--absent">not held at this time</span>
      )}
    </div>
  );
}
