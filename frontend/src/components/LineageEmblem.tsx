/*
 * LineageEmblem — the real Lineage crest, ported from the DC bundle's hand-authored SVG
 * (design-assets/lineage-logo.svg; DESIGN_PORT_DIAGNOSIS.md §1, "the single most portable asset").
 *
 * A shield with inherited channels and a central "attestation spine" — a teal diamond over a
 * vertical spine — which is exactly the project's thesis in one mark: provenance flowing down,
 * attested at the centre. Inlined (not an <img>) for ONE reason: so its two brand hues are driven by
 * the scoped --brand-* tokens instead of hardcoded hex, which keeps them out of the signal palette
 * (see tokens.css BRAND block). This component and the Loader are the ONLY places those tokens are
 * allowed. The mark is decorative chrome: aria-hidden, since the adjacent wordmark already names it.
 */

export function LineageEmblem({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 120 150"
      role="presentation"
      aria-hidden="true"
      focusable="false"
    >
      <g
        fill="none"
        stroke="var(--brand-parchment)"
        strokeLinecap="square"
        strokeLinejoin="miter"
      >
        <path
          strokeWidth="7"
          d="M18 16H50 M70 16H102 M18 16V91 C18 111 29 119 43 126 C50 130 56 136 60 142 C64 136 70 130 77 126 C91 119 102 111 102 91V16"
        />
        <path
          strokeWidth="6"
          d="M35 36V91 C35 103 42 108 51 113 L60 121L69 113 C78 108 85 103 85 91V36"
        />
        <path strokeWidth="6" d="M60 72L43 61V50 M60 72L77 61V50" />
      </g>
      <path
        fill="none"
        stroke="var(--brand-teal)"
        strokeWidth="5"
        strokeLinecap="square"
        d="M60 121V70"
      />
      <path fill="var(--brand-teal)" d="M60 28L69 40L60 52L51 40Z" />
      <path fill="var(--brand-teal)" d="M60 51L66 61L60 71L54 61Z" />
    </svg>
  );
}
