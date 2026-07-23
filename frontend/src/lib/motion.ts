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

/** Keyframe timing for a SETTLE — the DC's `lin-settle` (design-port S6): an element arrives large
 *  and shrinks into place while fading in, reaching full opacity at 60% and finishing the scale
 *  travel at 100%. Distinct from BLOOM_TIMES: a bloom is symmetric (peaks mid-way and eases back),
 *  a settle is monotone and ARRIVES. Used by Trace's origin ignite, where the origin must land and
 *  STAY lit rather than flash. */
export const SETTLE_TIMES = [0, 0.6, 1];

/*
 * LOADER — the startup cinematic's self-contained WAAPI timeline (design-port S3,
 * DESIGN_PORT_DIAGNOSIS.md §6). This is a DELIBERATE, DOCUMENTED EXCEPTION to the DUR/EASE
 * vocabulary above, exactly like the 3D scene's rAF loop: the loader is a one-shot orchestrated
 * sequence of ~15 beats driven by `element.animate()` (ms + cubic-bezier), not the framer-motion
 * tween set the console body uses. Its beats are NAMED HERE rather than left as bare literals inside
 * Loader.tsx — the precise drift Frontend Phase 6 fixed — so the whole timeline reads in one place.
 *
 * Vocabulary is still honoured where it matters: every animation is transform / opacity /
 * strokeDashoffset only, every easing is a tween (no springs), and NO colour is interpolated. The
 * two eases below are the cubic-bezier equivalents of EASE.out / EASE.inOut.
 *
 * All values in MILLISECONDS (WAAPI's unit), unlike DUR above (framer-motion seconds).
 */
export const LOADER = {
  // Beat 1 — decision lock: the four provenance strata register in 3-D; the subject diamond scales in.
  strataDur: 700,
  strataStagger: 90,
  diamondInDur: 500,
  // Beat 2 — provenance separation: the three real labels fade up.
  labelStart: 680,
  labelStagger: 150,
  labelDur: 360,
  // Beat 3 — backward trace to origin: an amber path draws via strokeDashoffset.
  traceStart: 1200,
  traceDur: 1200,
  // Beat 4 — origin resolved: the origin diamond blooms in.
  originAt: 2340,
  originDur: 460,
  // Beat 5 — evidence compresses away: the HUD fades and scales to 0.85.
  compressAt: 2550,
  compressDur: 500,
  // Beat 6 — identity seal: the emblem scales in, the wordmark fades up.
  sealAt: 2820,
  sealDur: 620,
  // Beat 7 — console ownership: the handoff line sweeps, then the overlay fades and disposes.
  handoffAt: 3460,
  handoffDur: 620,
  fadeAt: 3900,
  fadeDur: 330,
  completeAt: 4230,
  // Safety — a watchdog that guarantees the loader never traps the user even if a beat stalls.
  watchdog: 5200,
  // Reduced-motion path (~500 ms): logo → transfer line → ownership, no camera travel, same final frame.
  reducedSealDur: 260,
  reducedHandoffAt: 220,
  reducedHandoffDur: 200,
  reducedFadeAt: 380,
  reducedFadeDur: 160,
  reducedCompleteAt: 520,
  // Tween eases — cubic-bezier equivalents of EASE.out / EASE.inOut (no springs, ever).
  ease: "cubic-bezier(0.22, 1, 0.36, 1)",
  easeInOut: "cubic-bezier(0.65, 0, 0.35, 1)",
} as const;
