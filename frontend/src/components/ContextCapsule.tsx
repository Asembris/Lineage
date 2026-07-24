/*
 * ContextCapsule — the header's carried-investigation indicator (navbar port §7).
 *
 * ============================ THE ONE INVARIANT THIS FILE SERVES ============================
 * This component is COLOURLESS by construction: it takes a pre-computed descriptor of STRINGS, never a
 * `Decision`. The audit-reading that decides WHAT the capsule says happens in App (the composition
 * root, which legitimately holds both layers). That matters because the header is PERSISTENT across
 * every view — including the evidence surface — and neither static guard can see an audit leak here
 * (composition-guard check C stops at the aml arm; the .aml sweep stops at .aml). NAVBAR_TRIAGE.md
 * Part 2 has the full proof.
 *
 * So the rule that keeps the answer key off the witness lives in App's branch selection, and it is
 * asserted at render time by geometry.spec.ts "the header oracle boundary": while view.kind==='aml'
 * App emits ONLY the evidence branch (`data-capsule="evidence"`, label-free of any audit fact); the
 * decision branch (`data-capsule="decision"`) is emitted only on the console. `selectedId` is retained
 * across the seam, so a capsule keyed on selection instead of the active view would carry
 * "decision · txn_… · ✓ invalidated" onto the witness — exactly what that guard forbids.
 *
 * The `data-capsule` attribute is not decoration: it is the marker the header guard keys on. Keep it.
 */

/** The capsule's rendered content — computed in App from live state, handed here as plain strings.
 *  `kind` drives the `data-capsule` marker the header oracle guard asserts on. */
export interface CapsuleDescriptor {
  /** "decision" = a console-selected decision (AUDIT — console surface only). "evidence" = the open
   *  interrogation (label-free — the only branch permitted while the witness is up). */
  kind: "decision" | "evidence";
  label: string;
  sub: string | null;
  /** The accessible name for the clear (×) control — "close investigation" / "return to console". */
  clearLabel: string;
}

export function ContextCapsule({
  descriptor,
  onClear,
}: {
  descriptor: CapsuleDescriptor | null;
  onClear: () => void;
}) {
  if (!descriptor) return null;
  return (
    <div className="rail__capsule" data-capsule={descriptor.kind}>
      {/* A single --bone tone dot. There is deliberately no --alert tone: the handoff's two alert
          triggers (an anomalous ledger claim, a torn consistency fleet) are both CUT/unlifted, so an
          --alert dot here would be decorative — which the handoff itself forbids. */}
      <span className="rail__capsule-dot" aria-hidden="true" />
      <span className="rail__capsule-text">
        <span className="rail__capsule-label">{descriptor.label}</span>
        {descriptor.sub && <span className="rail__capsule-sub">{descriptor.sub}</span>}
      </span>
      <button
        type="button"
        className="rail__capsule-clear"
        aria-label={descriptor.clearLabel}
        onClick={onClear}
      >
        <span aria-hidden="true">×</span>
      </button>
    </div>
  );
}
