/*
 * CommandRail — the header's surface rail: four top-level surfaces (Console · Interrogation ·
 * Consistency · Ledger) with a traveling route indicator that eases beneath the active tab. Ported
 * from the navbar handoff §6, over NAVBAR_TRIAGE.md's rulings.
 *
 * COLOURLESS BY CONSTRUCTION. Its whole prop surface is a `Surface` string union, a callback, and two
 * boolean maps — no `Decision`, no witness type reaches it. The audit-reading that decides the
 * relation dots and the Interrogation tab's enabled-state happens in App (the composition root); the
 * rail only renders the result. So it adds no AUDIT/EVIDENCE colour to the persistent header, and the
 * one real oracle-boundary risk (a decision capsule beside the witness) lives in ContextCapsule, not
 * here — held by the render-time header guard (geometry.spec.ts "the header oracle boundary").
 *
 * THE INTERROGATION TAB REFLECTS, IT NEVER ORIGINATES. The handoff's `goSurface('interro')` would
 * synthesize "the first AML row" and open it — fabricated supervisor intent, a broken ordered reveal,
 * and a broken focus handoff (NAVBAR_TRIAGE.md Part 1). Cut. Instead the tab is ACTIVE only while the
 * witness is up (view.kind==='aml' — closing a real wayfinding gap: today no nav item is pressed
 * there) and ENABLED only when a real AML subject is already carried; App hands down that
 * already-chosen id. Disabled otherwise, with the descriptor pointing at the honest entry.
 *
 * A RELATION DOT ENCODES STATE AS GEOMETRY, NEVER AS TEXT. The 5px dot is a cross-surface link marker
 * with no fact-bearing text, so it is admissible on the persistent header even beside the witness (the
 * header guard's comment draws exactly this line). The capsule — which CAN carry audit text — is the
 * guarded surface; the dots are not.
 */

import { useLayoutEffect, useRef, useState } from "react";
import { useReducedMotion } from "framer-motion";
import { RAIL } from "../lib/motion";
import "./CommandRail.css";

/** The four top-level surfaces. Order is load-bearing: it is the arrow-nav order AND the relation
 *  order. `interro` maps to the evidence view (view.kind==='aml'); the other three map to view.kind. */
export type Surface = "console" | "interro" | "consistency" | "ledger";

interface SurfaceSpec {
  key: Surface;
  label: string;
  /** The hover descriptor dropped below the tab (mono, uppercase). */
  desc: string;
}

const SURFACES: SurfaceSpec[] = [
  { key: "console", label: "Console", desc: "decisions & belief lineage" },
  { key: "interro", label: "Interrogation", desc: "graph evidence · one edge" },
  { key: "consistency", label: "Consistency", desc: "atomic vs eventual" },
  { key: "ledger", label: "Ledger", desc: "claims & provenance record" },
];

const ORDER = SURFACES.map((s) => s.key);

export function CommandRail({
  active,
  interroEnabled,
  relMarker,
  onGo,
}: {
  active: Surface;
  /** The Interrogation tab is inert unless a real AML subject is carried (App decides). */
  interroEnabled: boolean;
  /** Real cross-surface links (App computes from live state); true → a dot on that tab. */
  relMarker: Record<Surface, boolean>;
  onGo: (s: Surface) => void;
}) {
  const reduced = useReducedMotion();
  const navRef = useRef<HTMLElement>(null);
  const tabRefs = useRef<Partial<Record<Surface, HTMLButtonElement | null>>>({});
  const [hover, setHover] = useState<Surface | null>(null);
  const [ind, setInd] = useState<{ x: number; w: number } | null>(null);

  // THE TRAVELING INDICATOR. Measure the active tab's live geometry (labels differ in width, so
  // positions are never hard-coded), then either snap (reduced motion / first paint) or ease toward
  // it with the RAIL asymptotic lerp — a documented scoped exception (lib/motion.ts RAIL). The loop
  // stops within RAIL.snapPx on both axes. useLayoutEffect so the first frame is positioned before
  // paint (no flash) and reduced-motion lands on the exact settled frame.
  useLayoutEffect(() => {
    const el = tabRefs.current[active];
    if (!el) return;
    const target = { x: el.offsetLeft, w: el.offsetWidth };

    // Reduced motion (or the very first measurement) snaps: the settled frame is the geometry itself.
    setInd((cur) => {
      if (reduced || cur === null) return target;
      return cur; // keep the current tween origin; the rAF below drives it to target
    });
    if (reduced) return;

    let raf = 0;
    let settled = false;
    const step = () => {
      setInd((cur) => {
        if (!cur) return target;
        const dx = target.x - cur.x;
        const dw = target.w - cur.w;
        if (Math.abs(dx) < RAIL.snapPx && Math.abs(dw) < RAIL.snapPx) {
          settled = true;
          return target;
        }
        return { x: cur.x + dx * RAIL.ease, w: cur.w + dw * RAIL.ease };
      });
      if (!settled) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [active, reduced]);

  // Re-measure on resize: the rail is optically centered between two min-width regions, so a width
  // change moves every tab. Snap (no tween) — a resize is not a route change.
  useLayoutEffect(() => {
    const onResize = () => {
      const el = tabRefs.current[active];
      if (el) setInd({ x: el.offsetLeft, w: el.offsetWidth });
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [active]);

  // Roving tablist: ←/→ move focus by one, Home/End jump to the ends. Manual activation — focus moves,
  // the <button>'s own Enter/Space fires onGo. Only the active tab is in the tab order (tabIndex 0).
  const onKeyDown = (e: React.KeyboardEvent) => {
    const i = ORDER.indexOf(active);
    let next = -1;
    if (e.key === "ArrowRight") next = (i + 1) % ORDER.length;
    else if (e.key === "ArrowLeft") next = (i - 1 + ORDER.length) % ORDER.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = ORDER.length - 1;
    if (next === -1) return;
    e.preventDefault();
    tabRefs.current[ORDER[next]]?.focus();
  };

  return (
    <nav
      className="rail"
      role="tablist"
      aria-label="Primary surfaces"
      ref={navRef}
      onKeyDown={onKeyDown}
    >
      {SURFACES.map((s) => {
        const on = s.key === active;
        const disabled = s.key === "interro" && !interroEnabled;
        return (
          <button
            key={s.key}
            type="button"
            role="tab"
            data-surf={s.key}
            className={`rail__tab${on ? " rail__tab--on" : ""}`}
            aria-selected={on}
            aria-disabled={disabled || undefined}
            tabIndex={on ? 0 : -1}
            ref={(el) => {
              tabRefs.current[s.key] = el;
            }}
            onMouseEnter={() => setHover(s.key)}
            onMouseLeave={() => setHover((h) => (h === s.key ? null : h))}
            onFocus={() => setHover(s.key)}
            onBlur={() => setHover((h) => (h === s.key ? null : h))}
            onClick={() => {
              if (disabled) return;
              onGo(s.key);
            }}
          >
            <span className="rail__label">{s.label}</span>
            {relMarker[s.key] && (
              <span
                className={`rail__dot${on ? " rail__dot--on" : ""}`}
                role="img"
                aria-label="related evidence available"
              />
            )}
            {hover === s.key && !on && (
              <span className="rail__desc" aria-hidden="true">
                {disabled ? "open an AML decision's “see why” to interrogate" : s.desc}
              </span>
            )}
          </button>
        );
      })}

      {/* The assembled active state: a traveling bone underline + a faint ash top bracket. Never a
          filled pill. Both are absolutely positioned so travel causes no reflow. */}
      {ind && (
        <>
          <span className="rail__underline" style={{ left: ind.x, width: ind.w }} aria-hidden="true" />
          <span
            className="rail__bracket"
            style={{ left: ind.x + ind.w * 0.28, width: ind.w * 0.44 }}
            aria-hidden="true"
          />
        </>
      )}
    </nav>
  );
}
