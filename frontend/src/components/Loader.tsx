/*
 * Loader — the startup cinematic ("The Decision Autopsy", LINEAGE_LOADER_HANDOFF.md).
 *
 * A fully SELF-CONTAINED overlay mounted at app entry. It reads NO data and touches NO console
 * internals EXCEPT the one sanctioned §8 coupling: at the console-ownership beat it measures the
 * genealogy panel's DOM bounds ([data-lin-graph]) to place the transfer line, and pulses the living
 * node's brightness ([data-live-node]). It is position:fixed, aria-hidden, pointer-events:none — so
 * it never intercepts a click (the console mounts and is fully interactive underneath from the first
 * frame) and it mounts no audit/evidence component, which is why it stays invisible to the
 * geometry/composition guards.
 *
 * The ~4.2s timeline is driven by the Web Animations API (`element.animate`) — the reference IS a
 * WAAPI sequence and ports 1:1 — with every beat NAMED in lib/motion.ts (LOADER), never a bare ms
 * literal. Seven beats: (1) decision lock — four provenance strata register across shallow 3-D depth
 * (each keeps its own translate3d+rotateY base, only scaling in, so the FAN is preserved), the
 * subject diamond scales in; (2) provenance separation — three REAL labels rise + fade up; (3)
 * backward trace — an amber path draws to origin via strokeDashoffset −1→0; (4) origin resolved — the
 * origin diamond blooms; (5) evidence compresses away — each autopsy element fades + scale .85; (6)
 * identity seal + brand — the emblem, then the wordmark on its own beat; (7) console ownership — the
 * measured teal handoff line sweeps, the backdrop dissolves + the living node pulses (reveal), then
 * seal/brand/root fade and it disposes.
 *
 * Vocabulary (lib/motion.ts law): transform / opacity / strokeDashoffset / width only, cubic-bezier
 * tweens, no springs, NO colour interpolation. The loader palette is the scoped --loader-* set
 * (tokens.css); the emblem/seal keep the scoped --brand-* tokens. None of it bleeds into console
 * styling, and there is no red anywhere.
 *
 * Safety: click or key ANYWHERE skips (fast-fade to done); a watchdog guarantees it never traps the
 * user even if a beat stalls; disposeIntro cancels every animation and listener on unmount.
 *
 * Reduced motion (useReducedMotion): ~560ms — seal → brand → transfer line → reveal, no camera/depth
 * travel, collapsing to the same final frame (the console, revealed).
 */

import { useEffect, useRef } from "react";
import { useReducedMotion } from "framer-motion";
import { LOADER } from "../lib/motion";
import { LineageEmblem } from "./LineageEmblem";
import "./Loader.css";

/*
 * THE LABEL STRINGS ARE REAL — every id and number here resolves to our live system, not the DC's
 * demo data. The DC's beat-2 label appended an INVENTED transaction id to "SELECTED DECISION · …":
 * 898ad0e5 and 0.528 are real, but that txn id is not (the no-fabricated-data guard denies it, which
 * is why it is not even quoted here). Showing a fabricated id in the first four seconds a judge sees
 * — in the one project whose thesis is that it never fabricates — is a needless self-inflicted
 * wound, so it is DROPPED. What remains is verified against tests-e2e/fixtures/console.json + the
 * belief lineage, kept in the spec's two-tier (kicker / value) form and positioned beside its node:
 *   · ORIGIN / crimson gen 0  — the origin of belief 898ad0e5 is the crimson founding ancestor, gen 0.
 *   · INHERITED / gen 3 · belief 898ad0e5 — a real inheritance hop on the crimson spine; 898ad0e5 is
 *     the real crimson belief id (short form).
 *   · SELECTED DECISION / 0.528 — the belief's real present-day confidence (gen-7 window; CI
 *     [0.466, 0.589]). The fabricated txn id is deliberately absent.
 */
const LABELS = [
  { kicker: "ORIGIN", value: "crimson gen 0", left: "106px", top: "286px" },
  { kicker: "INHERITED", value: "gen 3 · belief 898ad0e5", left: "336px", top: "201px" },
  { kicker: "SELECTED DECISION", value: "0.528", left: "640px", top: "145px" },
] as const;

/* The four provenance planes' exact base transforms + graduated depth-opacities (spec §6/§7). Each
 * base is kept on the element (data-base) and the beat-1 animation only appends scale(.93)→scale(1),
 * so the 3-D fan is never overwritten. The nearest plane (0) also carries the teal edge (CSS). */
const PLANES = [
  { base: "translate(-50%, -50%) translate3d(-165px, -30px, -190px) rotateY(16deg)", opacity: 0.26 },
  { base: "translate(-50%, -50%) translate3d(-75px, 18px, -110px) rotateY(9deg)", opacity: 0.32 },
  { base: "translate(-50%, -50%) translate3d(35px, -12px, -30px) rotateY(2deg)", opacity: 0.38 },
  { base: "translate(-50%, -50%) translate3d(150px, 20px, 65px) rotateY(-7deg)", opacity: 0.42 },
] as const;

/* The backward causal trace, authored origin(0,0) → decision(670,84). Drawn offset −1→0 so it
 * reveals from the decision end BACKWARD to the origin (spec §note-B: the sign matches the authoring). */
const TRACE_D = "M0 0 C112 34 190 16 274 52 S454 116 670 84";

export function Loader({ onComplete }: { onComplete: () => void }) {
  const rootRef = useRef<HTMLDivElement>(null);
  const reduce = useReducedMotion() ?? false;

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;

    const anims: Animation[] = [];
    const timers: number[] = [];
    let done = false;

    /** Animate `el` and register it for disposal. No-op (returns null) if `el` is missing. */
    const A = (
      el: Element | null | undefined,
      keyframes: Keyframe[],
      opts: KeyframeAnimationOptions,
    ): Animation | null => {
      if (!el) return null;
      const a = el.animate(keyframes, { fill: "forwards", ...opts });
      anims.push(a);
      return a;
    };

    const q = (sel: string) => root.querySelector(sel);
    const qa = (sel: string) => Array.from(root.querySelectorAll(sel));

    const dispose = () => {
      for (const a of anims) {
        try {
          a.cancel();
        } catch {
          /* an already-finished animation throws on cancel; ignore */
        }
      }
      for (const t of timers) clearTimeout(t);
      window.removeEventListener("pointerdown", onSkip);
      window.removeEventListener("keydown", onSkip);
    };

    const finish = () => {
      if (done) return;
      done = true;
      window.dispatchEvent(new CustomEvent("lineage-loader-complete"));
      onComplete();
    };

    const onSkip = () => {
      if (done) return;
      for (const a of anims) {
        try {
          a.cancel();
        } catch {
          /* ignore */
        }
      }
      const fade = A(root, [{ opacity: 1 }, { opacity: 0 }], {
        duration: LOADER.reducedHandoffDur,
        easing: LOADER.ease,
      });
      if (fade) fade.onfinish = finish;
      else finish();
    };

    /* §8 — measure the REAL genealogy panel and centre the fixed transfer line over it. Falls back to
     * a 760px centred line when the panel is absent (e.g. a non-console view is mounted). Returns the
     * line's target full width. */
    const alignHandoff = (): number => {
      const line = q(".loader__handoff") as HTMLElement | null;
      let top = "50%";
      let left = "50%";
      let width = 760;
      const g = document.querySelector("[data-lin-graph]");
      if (g) {
        const r = g.getBoundingClientRect();
        if (r.width > 0) {
          top = Math.round(r.top + r.height * 0.5) + "px";
          left = Math.round(r.left + r.width / 2) + "px";
          width = Math.round(r.width * 0.92);
        }
      }
      if (line) {
        line.style.top = top;
        line.style.left = left;
      }
      return width;
    };

    /* §8 — hand the console over: dissolve the backdrop (revealing the live console beneath) and pulse
     * the living node's brightness. Both are no-ops if their targets are absent. */
    const reveal = () => {
      const backdrop = q(".loader__backdrop") as HTMLElement | null;
      if (backdrop) {
        backdrop.style.transition = "opacity 0.42s ease";
        backdrop.style.opacity = "0";
      }
      const live = document.querySelector(
        "[data-lin-graph] [data-live-node], [data-lin-graph] .live",
      );
      const pulse = (live as HTMLElement | null)?.animate?.(
        [
          { filter: "brightness(1)" },
          { filter: "brightness(1.9)" },
          { filter: "brightness(1)" },
        ],
        { duration: 900, easing: "ease-out" },
      );
      if (pulse) anims.push(pulse);
    };

    window.addEventListener("pointerdown", onSkip);
    window.addEventListener("keydown", onSkip);

    if (reduce) {
      // Reduced path: seal → brand → transfer line → reveal, ~560ms, same final frame. No depth motion.
      A(
        q(".loader__seal"),
        [
          { opacity: 0, transform: "translate(-50%, -50%) scale(1)" },
          { opacity: 1, transform: "translate(-50%, -50%) scale(1)" },
        ],
        { duration: LOADER.reducedSealDur, easing: LOADER.ease },
      );
      A(
        q(".loader__brand"),
        [
          { opacity: 0, transform: "translate(-50%, 0)" },
          { opacity: 1, transform: "translate(-50%, 0)" },
        ],
        { delay: LOADER.reducedBrandAt, duration: LOADER.reducedBrandDur, easing: LOADER.ease },
      );
      const rw = alignHandoff();
      A(
        q(".loader__handoff"),
        [
          { opacity: 0, width: "0px" },
          { opacity: 1, width: Math.round(rw * 0.7) + "px", offset: 0.6 },
          { opacity: 0.9, width: rw + "px" },
        ],
        { delay: LOADER.reducedHandoffAt, duration: LOADER.reducedHandoffDur, easing: LOADER.ease },
      );
      timers.push(window.setTimeout(reveal, LOADER.reducedRevealAt));
      const fade = A(root, [{ opacity: 1 }, { opacity: 0 }], {
        delay: LOADER.reducedRevealAt + 40,
        duration: LOADER.reducedCompleteAt - (LOADER.reducedRevealAt + 40),
        easing: LOADER.ease,
      });
      if (fade) fade.onfinish = finish;
      timers.push(window.setTimeout(finish, LOADER.reducedCompleteAt + 200));
      return dispose;
    }

    // Beat 1 — decision lock: each plane keeps its 3-D base and only scales in; the subject scales in.
    qa(".loader__plane").forEach((el, i) => {
      const base = (el as HTMLElement).dataset.base ?? "";
      A(
        el,
        [
          { opacity: 0, transform: `${base} scale(0.93)` },
          { opacity: PLANES[i].opacity, transform: `${base} scale(1)` },
        ],
        { delay: i * LOADER.strataStagger, duration: LOADER.strataDur, easing: LOADER.ease },
      );
    });
    A(
      q(".loader__subject"),
      [
        { opacity: 0, transform: "translate(-50%, -50%) rotate(45deg) scale(0.55)" },
        { opacity: 1, transform: "translate(-50%, -50%) rotate(45deg) scale(0.74)" },
      ],
      { delay: LOADER.subjectDelay, duration: LOADER.subjectDur, easing: LOADER.ease },
    );

    // Beat 2 — provenance separation: the three real labels rise (translateY 5px→0) and fade up.
    qa(".loader__label").forEach((el, i) =>
      A(
        el,
        [
          { opacity: 0, transform: "translateY(5px)" },
          { opacity: 1, transform: "translateY(0)" },
        ],
        { delay: LOADER.labelStart + i * LOADER.labelStagger, duration: LOADER.labelDur, easing: LOADER.ease },
      ),
    );

    // Beat 3 — backward trace: the svg flashes in, then the amber active path draws −1→0 (backward).
    A(q(".loader__trace"), [{ opacity: 0 }, { opacity: 1 }], {
      delay: LOADER.traceSvgStart,
      duration: LOADER.traceSvgDur,
      easing: LOADER.ease,
    });
    A(q(".loader__trace-active"), [{ strokeDashoffset: -1 }, { strokeDashoffset: 0 }], {
      delay: LOADER.tracePathStart,
      duration: LOADER.tracePathDur,
      easing: LOADER.traceEase,
    });

    // Beat 4 — origin resolved: the origin diamond blooms (scale .4→1).
    A(
      q(".loader__origin"),
      [
        { opacity: 0, transform: "translate(-50%, -50%) rotate(45deg) scale(0.4)" },
        { opacity: 1, transform: "translate(-50%, -50%) rotate(45deg) scale(1)" },
      ],
      { delay: LOADER.originAt, duration: LOADER.originDur, easing: LOADER.ease },
    );

    // Beat 5 — evidence compresses away: each autopsy element fades to 0 and scales .85 (staggered
    // i*8). The scale is COMPOSITED onto each element's live transform (composite:'add') so a plane's
    // 3-D base, the subject's rotate, and the origin's rotate are all preserved as they shrink.
    const evidence = [
      ...qa(".loader__plane"),
      ...qa(".loader__label"),
      q(".loader__trace"),
      q(".loader__subject"),
      q(".loader__origin"),
    ];
    evidence.forEach((el, i) => {
      if (!el) return;
      const delay = LOADER.compressAt + i * LOADER.compressStagger;
      A(el, [{ opacity: 1 }, { opacity: 0 }], {
        delay,
        duration: LOADER.compressDur,
        easing: LOADER.ease,
      });
      A(
        el,
        [{ transform: "scale(1)" }, { transform: "scale(0.85)" }],
        { delay, duration: LOADER.compressDur, easing: LOADER.ease, composite: "add" },
      );
    });

    // Beat 6 — identity seal + brand: the emblem scales .88→1; the wordmark rises on its OWN beat.
    A(
      q(".loader__seal"),
      [
        { opacity: 0, transform: "translate(-50%, -50%) scale(0.88)" },
        { opacity: 1, transform: "translate(-50%, -50%) scale(1)" },
      ],
      { delay: LOADER.sealAt, duration: LOADER.sealDur, easing: LOADER.ease },
    );
    A(
      q(".loader__brand"),
      [
        { opacity: 0, transform: "translate(-50%, 8px)" },
        { opacity: 1, transform: "translate(-50%, 0)" },
      ],
      { delay: LOADER.brandAt, duration: LOADER.brandDur, easing: LOADER.ease },
    );

    // Beat 7 — console ownership: the measured line sweeps (width pulse), the backdrop dissolves + the
    // living node pulses (reveal), then seal/brand/root fade out and it disposes.
    const hw = alignHandoff();
    A(
      q(".loader__handoff"),
      [
        { opacity: 0, width: "0px" },
        { opacity: 1, width: Math.round(hw * 0.68) + "px", offset: 0.65 },
        { opacity: 0, width: hw + "px" },
      ],
      { delay: LOADER.handoffAt, duration: LOADER.handoffDur, easing: LOADER.ease },
    );
    timers.push(window.setTimeout(reveal, LOADER.revealAt));
    A(q(".loader__brand"), [{ opacity: 1 }, { opacity: 0 }], {
      delay: LOADER.brandOutAt,
      duration: LOADER.brandOutDur,
      easing: LOADER.ease,
    });
    A(q(".loader__seal"), [{ opacity: 1 }, { opacity: 0 }], {
      delay: LOADER.sealOutAt,
      duration: LOADER.sealOutDur,
      easing: LOADER.ease,
    });
    const fade = A(root, [{ opacity: 1 }, { opacity: 0 }], {
      delay: LOADER.fadeAt,
      duration: LOADER.fadeDur,
      easing: LOADER.ease,
    });
    if (fade) fade.onfinish = finish;

    // Safety net — the loader disposes even if a beat stalls or onfinish never fires.
    timers.push(window.setTimeout(finish, LOADER.watchdog));

    return dispose;
  }, [reduce, onComplete]);

  return (
    <div ref={rootRef} className="loader" aria-hidden="true">
      {/* The opaque backdrop — dissolved at the reveal to hand the console over. */}
      <div className="loader__backdrop" />

      {/* The autopsy stage: a shallow-3-D HUD holding the evidence, the seal, and the brand. */}
      <div className="loader__stage">
        <div className="loader__hud">
          {/* Four provenance strata, each with its own 3-D base kept on data-base (see beat 1). */}
          {PLANES.map((p, i) => (
            <div key={i} className="loader__plane" data-base={p.base} style={{ transform: p.base }} />
          ))}

          {/* The selected decision — rimmed teal diamond with an inner core. */}
          <div className="loader__subject">
            <div className="loader__subject-core" />
          </div>

          {/* The backward causal trace: dim companion base + amber active path (drawn to origin). */}
          <svg className="loader__trace" viewBox="0 0 670 150" role="presentation" aria-hidden="true">
            <path className="loader__trace-base" d={TRACE_D} />
            <path className="loader__trace-active" d={TRACE_D} pathLength={1} />
          </svg>

          {/* Three real fact labels, two-tier, pinned beside their nodes. */}
          {LABELS.map((l) => (
            <div key={l.kicker} className="loader__label" style={{ left: l.left, top: l.top }}>
              {l.kicker}
              <strong>{l.value}</strong>
            </div>
          ))}

          {/* The origin ancestor — rimmed amber diamond with an inner core. */}
          <div className="loader__origin">
            <div className="loader__origin-core" />
          </div>

          {/* The identity seal (the exact emblem) and, on its own beat, the wordmark. */}
          <div className="loader__seal">
            <LineageEmblem className="loader__emblem" />
          </div>
          <div className="loader__brand">
            <div className="loader__brand-name">LINEAGE</div>
            <div className="loader__brand-sub">SUPERVISOR CONSOLE</div>
          </div>
        </div>
      </div>

      {/* The console-ownership transfer line (beat 7), measured over the real genealogy panel. */}
      <div className="loader__handoff" />
    </div>
  );
}
