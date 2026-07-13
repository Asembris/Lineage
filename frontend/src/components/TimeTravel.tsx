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
import type { BeliefPerformanceWindow, StalenessUncertainty, UUID } from "../api/types";
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
      uncertainty: StalenessUncertainty | null;
      depoPast: Depo;
      depoNow: Depo;
    };

/** Resolve one belief's presence/status within a deposition response. */
function depoFor(beliefs: { id: UUID; status: string }[], beliefId: UUID, label: string): Depo {
  const b = beliefs.find((x) => x.id === beliefId);
  return { label, held: !!b, status: b?.status ?? null };
}

/** A window whose Wilson bounds are both present. The backend withholds them together (agreement
 *  is a property of the whole curve), but this is checked per-window rather than assumed. */
function hasBand(
  w: BeliefPerformanceWindow,
): w is BeliefPerformanceWindow & { confidence_ci_low: number; confidence_ci_high: number } {
  return w.confidence_ci_low !== null && w.confidence_ci_high !== null;
}

/** Fisher's p, at the precision it is worth reading. Below 0.001 the exact digits are noise —
 *  what matters is the order of magnitude — so it goes exponential. */
function formatPValue(p: number): string {
  return p < 0.001 ? p.toExponential(1) : p.toFixed(3);
}

/** SVG sparkline of the confidence curve — a fixed [0,1] domain so the decay is
 *  shown at true scale, never exaggerated by auto-fitting. Draws left→right with
 *  a --alive→--alert gradient (healthy early, stale late).
 *
 *  THE CONFIDENCE RIBBON IS ACHROMATIC, AND THAT IS THE LOAD-BEARING DECISION. The line's
 *  --alive→--alert gradient already means HEALTH. If the ribbon inherited it, the widest bands
 *  (the late, stale windows) would render as a spreading red haze and WIDTH WOULD MASQUERADE AS
 *  SEVERITY — a second alert channel, invented by accident. Uncertainty is not a state; it is a
 *  lack of resolution. So it gets the cold provenance-grey vocabulary the honesty ledger chose
 *  for exactly this reason (--ash, "deliberately NOT the --alive/--alert vocabulary, so it can
 *  never read as a second alert system"). The coloured line is the MEANING; the grey corridor is
 *  the PRECISION.
 *
 *  There is NO clamp and NO minimum-n gate on the band's height. A thin window (n=1, k=0) has a
 *  Wilson interval of [0.000, 0.793] and its ribbon really does cover four-fifths of this chart.
 *  That IS the truth, and a chart that hid it would be lying — this project has refused
 *  hand-picked thresholds before (MARGIN_FLOOR; the disjoint-intervals rule that failed open).
 *  Because the corridor is grey, a band that tall reads as "we do not know here" rather than
 *  "danger here", which is the honest reading of a wide interval. */
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

  // The corridor: upper bounds left→right, then lower bounds right→left, closed. Drawn only when
  // EVERY window carries its bounds — a partial ribbon would imply the missing windows are
  // certain, which is the one thing a withheld interval must never be able to say.
  const banded = windows.every(hasBand);
  const ribbon = banded
    ? "M " +
      windows.map((w, i) => `${x(i)},${y(w.confidence_ci_high as number)}`).join(" L ") +
      " L " +
      windows
        .map((w, i) => `${x(i)},${y(w.confidence_ci_low as number)}`)
        .reverse()
        .join(" L ") +
      " Z"
    : null;

  const label = banded
    ? "confidence across generation windows, healthy to stale, with 95% Wilson confidence intervals"
    : "confidence across generation windows, healthy to stale; confidence intervals withheld";

  return (
    <svg
      className="tt__curve"
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={label}
    >
      <defs>
        <linearGradient id="tt-grad" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="var(--alive)" />
          <stop offset="100%" stopColor="var(--alert)" />
        </linearGradient>
      </defs>
      {ribbon && (
        <motion.path
          d={ribbon}
          className="tt__band"
          initial={reduce ? { opacity: 1 } : { opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: reduce ? 0 : DUR.sweep, ease: EASE.inOut }}
        />
      )}
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

/** What the evidence will and will not support — stated in the NEUTRAL register, never in
 *  --alive/--alert.
 *
 *  Colouring "decay supported" green would INVERT it: a supported decay is bad news for the belief,
 *  and it is the finding that justifies the single irreversible governed write. Colouring it red
 *  would make it a second alert channel next to the curve it qualifies. So it is achromatic, like
 *  the ledger's provenance tags — a statement of evidentiary standing, not an alarm. */
function Support({ u }: { u: StalenessUncertainty }) {
  if (u.sample_agreement !== "agreed") {
    const why =
      u.sample_agreement === "disagreed"
        ? "belief_performance no longer reproduces from the decisions it summarizes"
        : "a window has no decisions to aggregate";
    return (
      <p className="tt__support">
        <span className="tt__support-verdict">intervals withheld</span> — {why}. The point estimates
        and counts above still stand; no interval is derivable, so none is shown.
      </p>
    );
  }
  if (u.decay_supported === null) {
    return (
      <p className="tt__support">
        <span className="tt__support-verdict">decay not assertable</span> — a single measured window
        cannot be compared against anything.
      </p>
    );
  }
  return (
    <p className="tt__support">
      <span className="tt__support-verdict">
        {u.decay_supported ? "measured decay supported" : "decay not distinguishable at this sample size"}
      </span>
      {u.decay_p_value !== null && (
        <>
          {" — Fisher exact, two-sided, "}
          <span className="tt__mono">p&nbsp;=&nbsp;{formatPValue(u.decay_p_value)}</span>
        </>
      )}
      {u.decay_supported
        ? ". The drop is real and downward — this is the evidence an invalidation rests on."
        : ". The curve moved, but “same rate” cannot be rejected — the evidence does not support killing this belief."}
    </p>
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
        uncertainty: perf.uncertainty,
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

  const { windows, uncertainty, depoPast, depoNow } = state;
  const hasWindows = windows.length > 0;

  const first = hasWindows ? windows[0] : null;
  const last = hasWindows ? windows[windows.length - 1] : null;
  const active = toggle === "formed" ? first : last;
  const activeIndex = toggle === "formed" ? 0 : windows.length - 1;
  const tone = toggle === "formed" ? "alive" : "alert";

  return (
    <div className="tt">
      {/* SIGNAL 2 — the measured staleness curve. A belief with no measured windows has no curve,
          and says so; it does NOT lose signal 1 below, which is true for it and works. */}
      {!hasWindows || !active || !first || !last ? (
        <p className="panel__note tt__note">
          No measured performance windows for this belief — it has no measured time structure, so
          there is no staleness curve to travel. The MVCC deposition below is unaffected.
        </p>
      ) : (
        <>
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

          {/* Measured staleness readout for the selected end — the healthy→stale shift.
              The hero number keeps its scale and its tone; the interval sits UNDER it, in the cold
              register, and is written [lo, hi] rather than ±. A Wilson interval is ASYMMETRIC
              (0.924 → [0.884, 0.951] is −0.040/+0.027), so "0.92 ± 0.03" would assert a symmetry
              the statistic does not have. n is shown because it is the whole point: without it a
              reader cannot tell 250 samples from 5. */}
          <motion.div
            key={toggle}
            className={`tt__readout tt__readout--${tone}`}
            initial={reduce ? false : { opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: reduce ? 0 : DUR.reveal, ease: EASE.out }}
          >
            <div className="tt__conf">
              <span className={`tt__conf-val tt__hot-${tone}`}>
                {formatConfidence(active.confidence)}
              </span>
              <span className="tt__conf-label">measured confidence</span>
              <span className="tt__ci">
                {hasBand(active) ? (
                  <>
                    95% CI [{formatConfidence(active.confidence_ci_low)},{" "}
                    {formatConfidence(active.confidence_ci_high)}]
                  </>
                ) : (
                  <>95% CI withheld</>
                )}
                <span className="tt__ci-sep">·</span>n = {formatCount(active.sample_size)}
              </span>
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
            confidence {formatConfidence(first.confidence)} → {formatConfidence(last.confidence)}{" "}
            across {windows.length} measured windows — derived from decisions, not asserted.
          </p>

          {uncertainty && <Support u={uncertainty} />}
        </>
      )}

      {/* SIGNAL 1 — the MVCC deposition. Proof time-travel is real, and the row is immutable.
          It is rendered for EVERY belief, including one with no measured curve: discarding a
          working, honest proof to show a dead end is the opposite of this project's discipline. */}
      <div className="tt__depo">
        <div className="tt__depo-head">MVCC deposition · real AS OF SYSTEM TIME</div>
        <DepoRow label={`as of ${depoPast.label.slice(0, 19)}Z`} depo={depoPast} />
        <DepoRow label="present" depo={depoNow} />
        <p className="tt__depo-note">
          Same belief <span className="tt__mono">{fragId(belief.id)}</span>, both reads — the row is
          immutable across MVCC time (AOST reaches ~75 min
          {hasWindows
            ? "; formation-era history is the measured curve above, a different clock"
            : ", and this belief has no measured curve on the other clock"}
          ).
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
