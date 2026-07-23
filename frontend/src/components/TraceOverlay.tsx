/*
 * TraceOverlay — the signature animation. A purely ADDITIVE warm overlay layered
 * on top of the untouched cold genealogy tree: warmth spreading backward through
 * the dead tree, edge by edge, igniting the origin ancestor.
 *
 * Geometry (edge paths reversed to draw child→parent, node positions) is computed
 * by GenealogyTree from its own layout and passed in — this component only owns
 * playback. Warmth is drawn with pathLength / opacity / transform only (no JS color
 * interpolation): each --trace edge is drawn OVER its cold counterpart, so the edge
 * visibly turns warm as it draws. Keyed by playToken upstream, so Replay remounts
 * it from the cold initial state — that remount IS the reset-before-replay.
 *
 * prefers-reduced-motion collapses the whole sequence to its final state instantly
 * (no crawl) and reports completion on mount.
 */

import { motion, useReducedMotion } from "framer-motion";
import { DUR, EASE, SETTLE_TIMES } from "../lib/motion";

// Trace-local cadence knobs (the warmth-walk feel; NOTES.md calls these out for live tuning).
// The walk cadence already matched the DC exactly (its hopStart i*150ms / hopDur 160ms); only the
// origin's FINISH differed, and that is what design-port S6 replaced.
const STAGGER = 0.15; // per-hop delay
const EDGE_DUR = 0.16; // one edge's pathLength draw
const EDGE_FADE = 0.04; // the near-instant opacity flash alongside the draw
const NODE_DUR = 0.14; // a chain node tinting to --trace
const IGNITE_DUR = DUR.bloom; // the origin bloom — shared with Invalidate's corrected halo

/*
 * THE ORIGIN'S FINISH (design-port S6, DC `lin-settle` + its bloom disc).
 *
 * The origin used to flash: it scaled UP through an overshoot (1 → 1.12 → 1) while a fixed-radius
 * glow peaked at 0.8 and then DIMMED BACK to 0.55. Read literally, that says the origin ignited and
 * then partly went out — the opposite of the claim the moment makes. The DC's is monotone: the disc
 * grows outward and STAYS lit, and the node arrives LARGE and settles down into place.
 *
 * Both are transform/opacity only, and both are monotone, so prefers-reduced-motion collapses to a
 * byte-identical final frame by construction — there is no peak to miss, only the resting state.
 */
const SETTLE_FROM = 1.9; // DC lin-settle: the origin node arrives at 1.9x and shrinks into place
const GLOW_FROM = 0.42; // the bloom disc's start radius as a fraction of its final (DC 16 → 50)
const GLOW_OPACITY = 0.78; // DC bloom disc opacity at full ignite — reached, then HELD

export interface TraceEdgeGeo {
  key: string;
  d: string; // path drawn child→parent (the backward direction)
  index: number; // hop index, 0 = nearest the investigated agent
}
export interface TraceNodeGeo {
  id: string;
  x: number;
  y: number;
  gen: number;
  index: number; // 0 = investigated agent (leaf), last = origin
  isOrigin: boolean;
}
export interface TraceGeo {
  edges: TraceEdgeGeo[];
  nodes: TraceNodeGeo[];
}

export function TraceOverlay({
  geo,
  playToken,
  onComplete,
}: {
  geo: TraceGeo;
  playToken: number;
  onComplete: () => void;
}) {
  const reduce = useReducedMotion() ?? false;
  const hops = geo.edges.length;
  const originDelay = reduce ? 0 : hops * STAGGER;

  let completed = false;
  const fireOnce = () => {
    if (completed) return;
    completed = true;
    onComplete();
  };

  return (
    <g className="tree__trace" key={playToken} aria-hidden="true">
      {/* Edges draw child→parent — warmth walking backward toward the origin. */}
      {geo.edges.map((e) => (
        <motion.path
          key={e.key}
          className="tree__trace-edge"
          d={e.d}
          initial={{ pathLength: 0, opacity: 0 }}
          animate={{ pathLength: 1, opacity: 1 }}
          transition={
            reduce
              ? { duration: 0 }
              : {
                  pathLength: { delay: e.index * STAGGER, duration: EDGE_DUR, ease: EASE.out },
                  opacity: { delay: e.index * STAGGER, duration: EDGE_FADE, ease: EASE.out },
                }
          }
        />
      ))}

      {/* Non-origin chain nodes tint to --trace as their incoming edge lands. */}
      {geo.nodes.map((n) => {
        if (n.isOrigin) return null;
        return (
          <g key={n.id} transform={`translate(${n.x} ${n.y})`}>
            <motion.g
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={reduce ? { duration: 0 } : { delay: n.index * STAGGER, duration: NODE_DUR, ease: EASE.out }}
            >
              <circle className="tree__trace-dot" r={13} />
              <text className="tree__trace-gen" dominantBaseline="central">
                {n.gen}
              </text>
            </motion.g>
          </g>
        );
      })}

      {/* The origin ignites: --origin recolor, a glow ramp, one scale pulse. */}
      {geo.nodes
        .filter((n) => n.isOrigin)
        .map((o) => (
          <g key={o.id} transform={`translate(${o.x} ${o.y})`}>
            {/* The bloom disc grows outward and HOLDS at full ignite — monotone, so the resting
                state IS the end state under either motion preference. */}
            <motion.circle
              className="tree__trace-glow"
              r={26}
              initial={{ opacity: 0, scale: GLOW_FROM }}
              animate={{ opacity: GLOW_OPACITY, scale: 1 }}
              transition={
                reduce ? { duration: 0 } : { delay: originDelay, duration: IGNITE_DUR, ease: EASE.out }
              }
            />
            {/* lin-settle: arrives at 1.9x and shrinks in, opacity full at 60% of the travel. */}
            <motion.g
              initial={{ opacity: 0, scale: SETTLE_FROM }}
              animate={{ opacity: reduce ? 1 : [0, 1, 1], scale: 1 }}
              transition={
                reduce
                  ? { duration: 0 }
                  : {
                      opacity: {
                        delay: originDelay,
                        duration: IGNITE_DUR,
                        ease: EASE.out,
                        times: SETTLE_TIMES,
                      },
                      scale: { delay: originDelay, duration: IGNITE_DUR, ease: EASE.out },
                    }
              }
              onAnimationComplete={fireOnce}
            >
              <circle className="tree__trace-origin-dot" r={14} />
              <text className="tree__trace-origin-gen" dominantBaseline="central">
                {o.gen}
              </text>
            </motion.g>
          </g>
        ))}
    </g>
  );
}
