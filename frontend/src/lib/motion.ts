/*
 * Shared motion tokens — one source of timing + easing so the whole console reads with a
 * single, consistent feel (Frontend Phase 6 harmonization). Before this, five sessions each
 * hand-picked durations and eases, so the same gesture appeared at 0.25s here and 180ms there,
 * and four framer-motion animations silently inherited the library's default ease.
 *
 * Everything here is a framer-motion TWEEN. The app uses NO springs — a deliberate deviation
 * from FRONTEND.md's "genuine spring physics" language, recorded as a known gap in NOTES.md
 * ("Frontend Phase 6"). Harmonizing the tweens (not converting them to springs) was the scoped
 * choice for this polish pass.
 *
 * Two ease curves ONLY:
 *   - EASE.out   — directional draws, tints, blooms, and surface/readout reveals: motion that
 *                  decelerates into a resting final state (the common case).
 *   - EASE.inOut — self-contained sweeps (the staleness sparkline) and infinite "still
 *                  happening" pulses: symmetric, non-arriving motion.
 *
 * Durations are grouped by GESTURE, not by feature, so the same gesture reads identically
 * wherever it appears. The CSS-driven animations (Invalidate.css kill-fade, ConsistencyDemo.css
 * cx-fade / cx-pulse / cx-split-pulse) cannot import this module, so they carry the SAME literal
 * values with a "keep in sync with lib/motion.ts" comment.
 */

export const EASE = {
  out: "easeOut",
  inOut: "easeInOut",
} as const;

export const DUR = {
  /** A surface or readout appears: panels, the invalidation outcome, the time-travel readout
   *  swap. (Was split three ways — 0.25s / 180ms / 200ms — now one value.) */
  reveal: 0.2,
  /** The staleness sparkline's one self-contained left→right sweep. A one-off, but named so it
   *  is not a bare literal. */
  sweep: 0.9,
  /** The two orchestrated "atomic" blooms — Trace's origin ignite and Invalidate's corrected
   *  halo. The one place two features are deliberately in sync; this is the anchor everything
   *  else was tuned around. */
  bloom: 0.5,
  /** An infinite "this is live / still happening" pulse period: the armed-holder halo, the
   *  reseed dot, the split-cell meter. (Was 1.6s / 1.4s / 1.5s — now one value.) */
  pulse: 1.5,
} as const;

/** Keyframe timing for a symmetric [start, peak, settle] bloom. Plain number[] so framer-motion's
 *  `times` prop accepts it without a readonly-tuple cast. */
export const BLOOM_TIMES = [0, 0.5, 1];
