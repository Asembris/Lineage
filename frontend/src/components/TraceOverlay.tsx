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
import { BLOOM_TIMES, DUR, EASE } from "../lib/motion";

// Trace-local cadence knobs (the warmth-walk feel; NOTES.md calls these out for live tuning).
const STAGGER = 0.15; // per-hop delay
const EDGE_DUR = 0.16; // one edge's pathLength draw
const EDGE_FADE = 0.04; // the near-instant opacity flash alongside the draw
const NODE_DUR = 0.14; // a chain node tinting to --trace
const IGNITE_DUR = DUR.bloom; // the origin bloom — shared with Invalidate's corrected halo

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
            <motion.circle
              className="tree__trace-glow"
              r={22}
              initial={{ opacity: 0, scale: 0.65 }}
              // reduced-motion settles on the SAME resting opacity as the full-motion end
              // keyframe (0.55), so both paths land on one identical final state.
              animate={{ opacity: reduce ? 0.55 : [0, 0.8, 0.55], scale: 1 }}
              transition={
                reduce
                  ? { duration: 0 }
                  : { delay: originDelay, duration: IGNITE_DUR, ease: EASE.out, times: BLOOM_TIMES }
              }
            />
            <motion.g
              initial={{ opacity: 0, scale: 0.9 }}
              animate={{ opacity: 1, scale: reduce ? 1 : [1, 1.12, 1] }}
              transition={
                reduce
                  ? { duration: 0 }
                  : { delay: originDelay, duration: IGNITE_DUR, ease: EASE.out, times: BLOOM_TIMES }
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
