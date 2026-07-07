/*
 * InvalidateOverlay — the tree half of the Invalidate interaction, and the moment the
 * closure (not just Trace's single chain) becomes visible. An additive overlay on the cold
 * tree that marks EVERY living holder of the belief — here there are two: the spine tip AND
 * the branch holder crimson-5b, which Trace never lit. Two phases:
 *
 *   "armed"     — while the supervisor is at the confirm gate: both holders pulse --alert,
 *                 "these living agents are acting on the stale belief right now".
 *   "corrected" — on a successful atomic invalidation: both holders flip --alert → --alive
 *                 TOGETHER, one synchronized transition (no per-node stagger) — the single
 *                 commit made visible. The simultaneity IS the CockroachDB message.
 *
 * Motion is transform/opacity only (CLAUDE.md). prefers-reduced-motion collapses each phase
 * to its final static state (steady alert halo / steady alive correction), no pulse, no crawl.
 * Keyed by phase+playToken upstream so the "corrected" flip mounts fresh and plays once.
 *
 * Colors: --alert (the consequential action) and --alive (the correction) ONLY. No amber/
 * orange — that warmth stays reserved for the Trace overlay so the two moments never blur.
 */

import { motion, useReducedMotion } from "framer-motion";
import { BLOOM_TIMES, DUR, EASE } from "../lib/motion";

export interface InvalHolderGeo {
  id: string;
  x: number;
  y: number;
  gen: number;
}
export interface InvalGeo {
  holders: InvalHolderGeo[];
}

export function InvalidateOverlay({
  geo,
  phase,
  playToken,
}: {
  geo: InvalGeo;
  phase: "armed" | "corrected";
  playToken: number;
}) {
  const reduce = useReducedMotion() ?? false;
  const corrected = phase === "corrected";

  return (
    <g className={`tree__inval tree__inval--${phase}`} key={`${phase}-${playToken}`} aria-hidden="true">
      {geo.holders.map((h) => (
        <g key={h.id} transform={`translate(${h.x} ${h.y})`}>
          {/* Halo: pulsing --alert while armed; a single --alive bloom on correction.
              Every holder shares ONE transition (no index delay) so they move as one. */}
          <motion.circle
            className={`tree__inval-halo tree__inval-halo--${corrected ? "alive" : "alert"}`}
            r={21}
            initial={corrected ? { opacity: 0, scale: 0.7 } : { opacity: 0.35, scale: 1 }}
            animate={
              corrected
                // reduced-motion settles on the SAME resting opacity as the full-motion end
                // keyframe (0.6), so both paths land on one identical final state.
                ? { opacity: reduce ? 0.6 : [0, 0.9, 0.6], scale: 1 }
                : reduce
                  ? { opacity: 0.5, scale: 1 }
                  : { opacity: [0.3, 0.6, 0.3], scale: [1, 1.08, 1] }
            }
            transition={
              corrected
                ? reduce
                  ? { duration: 0 }
                  : { duration: DUR.bloom, ease: EASE.out, times: BLOOM_TIMES } // shared with Trace's origin ignite
                : reduce
                  ? { duration: 0 }
                  : { duration: DUR.pulse, repeat: Infinity, ease: EASE.inOut }
            }
          />
          {/* Recolored dot + generation drawn over the cold node. */}
          <motion.g
            initial={corrected ? { opacity: 0 } : { opacity: 1 }}
            animate={{ opacity: 1 }}
            transition={corrected ? (reduce ? { duration: 0 } : { duration: DUR.reveal, delay: 0.12, ease: EASE.out }) : { duration: 0 }}
          >
            <circle className={`tree__inval-dot tree__inval-dot--${corrected ? "alive" : "alert"}`} r={13} />
            <text className={`tree__inval-gen tree__inval-gen--${corrected ? "alive" : "alert"}`} dominantBaseline="central">
              {h.gen}
            </text>
          </motion.g>
        </g>
      ))}
    </g>
  );
}
