/*
 * FleetPopover — the far-right fleet-health control (navbar port §8). A steady teal live dot + the
 * alive count opens a focus-managed popover with the fleet metrics and the LIVING ROSTER.
 *
 * COLOURLESS / FLEET-SCOPED. Its inputs are agents, per-kind counts, and the belief catalog — fleet
 * facts, none of them a `Decision`. `--alive` here is the one legitimate signal on the header: it
 * means "a living agent", which is exactly the live dot and the alive count (fleet health), the same
 * use FleetSummary always had.
 *
 * THE ROSTER IS DERIVED, NEVER PASTED. The handoff names the living agents crimson-7 / crimson-5b /
 * an azure agent at gen 5 — and that last handle is WRONG: our only living azure agent is azure gen 7
 * (NAVBAR_TRIAGE.md Part 5; the invented gen-5 azure handle is denied by no-fabricated-data.mjs). Our
 * API exposes bloodline + generation, not the seed's handle, so the roster is rendered as
 * "<bloodline> · gen N" straight from /agents — the count and every name read live, never a literal.
 */

import { useEffect, useRef, useState } from "react";
import type { AgentsData, BeliefsData, KindCounts, Loadable } from "../hooks/useConsoleData";
import type { Surface } from "./CommandRail";
import { formatCount } from "../lib/format";
import "./FleetPopover.css";

const DASH = "—";

export function FleetPopover({
  agents,
  counts,
  beliefs,
  onGo,
}: {
  agents: Loadable<AgentsData>;
  counts: Loadable<KindCounts>;
  beliefs: Loadable<BeliefsData>;
  onGo: (s: Surface) => void;
}) {
  const [open, setOpen] = useState(false);
  const btnRef = useRef<HTMLButtonElement>(null);
  const popRef = useRef<HTMLDivElement>(null);

  // Escape closes and returns focus to the button; a mousedown outside the popover + button closes it.
  // Both listeners are registered only while open and removed on close/unmount.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        btnRef.current?.focus();
      }
    };
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (popRef.current?.contains(t) || btnRef.current?.contains(t)) return;
      setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, [open]);

  const agentsData = agents.status === "ready" ? agents.data : null;
  const total = agentsData?.count ?? null;
  const living = agentsData ? agentsData.agents.filter((a) => a.status === "alive") : [];
  const alive = agentsData ? living.length : null;
  const decisions = counts.status === "ready" ? counts.data.all : null;
  const beliefsTotal = beliefs.status === "ready" ? beliefs.data.count : null;
  const beliefsActive =
    beliefs.status === "ready"
      ? beliefs.data.beliefs.filter((b) => b.status === "active").length
      : null;

  const roster = [...living].sort(
    (a, b) => a.bloodline.localeCompare(b.bloodline) || a.generation - b.generation,
  );

  const n = (v: number | null) => (v === null ? DASH : formatCount(v));

  return (
    <div className="fleet">
      <button
        ref={btnRef}
        type="button"
        className={`fleet__btn${open ? " fleet__btn--open" : ""}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`Fleet health: ${alive === null ? "unknown" : alive} alive of ${
          total === null ? "unknown" : total
        } agents`}
        onClick={() => setOpen((o) => !o)}
      >
        <span className={`fleet__dot${alive ? " fleet__dot--healthy" : ""}`} aria-hidden="true" />
        <span className="fleet__alive">{n(alive)}</span>
        <span className="fleet__sep">/</span>
        <span className="fleet__total">{n(total)}</span>
        <span className="fleet__unit">agents</span>
        <span className="fleet__caret" aria-hidden="true">
          ▾
        </span>
      </button>

      {open && (
        <div className="fleet__pop" role="dialog" aria-label="Fleet summary" tabIndex={-1} ref={popRef}>
          <div className="fleet__pop-head">
            <span className="fleet__pop-title">Fleet</span>
            <span className="fleet__pop-sub">live · MVCC present</span>
          </div>

          <div className="fleet__grid">
            <div className="fleet__metric">
              <span className="fleet__metric-v fleet__metric-v--alive">{n(alive)}</span>
              <span className="fleet__metric-k">alive</span>
            </div>
            <div className="fleet__metric">
              <span className="fleet__metric-v">{n(total)}</span>
              <span className="fleet__metric-k">agents</span>
            </div>
            <div className="fleet__metric">
              <span className="fleet__metric-v">{n(decisions)}</span>
              <span className="fleet__metric-k">decisions</span>
            </div>
            <div className="fleet__metric">
              <span className="fleet__metric-v">
                {beliefsActive === null || beliefsTotal === null
                  ? DASH
                  : `${formatCount(beliefsActive)} / ${formatCount(beliefsTotal)}`}
              </span>
              <span className="fleet__metric-k">beliefs active</span>
            </div>
          </div>

          <div className="fleet__roster">
            <span className="fleet__roster-head">living agents</span>
            {roster.length === 0 ? (
              <span className="fleet__roster-empty">{DASH}</span>
            ) : (
              <ul className="fleet__roster-list">
                {roster.map((a) => (
                  <li key={a.id} className="fleet__roster-row">
                    <span className="fleet__dot fleet__dot--healthy" aria-hidden="true" />
                    <span className="fleet__roster-name">{a.bloodline}</span>
                    <span className="fleet__roster-gen">gen {a.generation}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <button
            type="button"
            className="fleet__foot"
            onClick={() => {
              setOpen(false);
              onGo("console");
            }}
          >
            view living holders in console <span aria-hidden="true">→</span>
          </button>
        </div>
      )}
    </div>
  );
}
