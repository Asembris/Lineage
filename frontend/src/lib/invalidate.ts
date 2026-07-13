/*
 * Pure predicates for the governed write. Kept out of the component file so it exports only
 * components (fast-refresh discipline, and the repo's oxlint baseline is zero warnings).
 */

import type { Belief } from "../api/types";
import type { InvalidateUi } from "../components/Invalidate";

/**
 * Is the Invalidate block currently offering the WRITE (a control), rather than reporting on it
 * (a receipt or a note)?
 *
 * CONTROLS ARE PINNED; EVIDENCE AND RECEIPTS SCROLL. The one irreversible action must be reachable
 * without a scroll at every viewport, so the control states live in the Investigation's pinned
 * footer. A receipt has nothing left to reach for — the certificate's sha256 / S3 key / HLC would
 * eat half the panel to no purpose — so it scrolls with the evidence that earned it.
 *
 *   control      idle | arming | confirming | invalidating | a RETRIABLE error (it offers Retry)
 *   not control  done (the certificate — a receipt); an already-invalidated belief (a note, via
 *                either the error branch or the catalog status) — neither offers an action.
 */
export function isInvalidateControl(ui: InvalidateUi, belief: Belief): boolean {
  if (ui.status === "done") return false;
  if (ui.status === "error") return !ui.alreadyInvalidated;
  // A belief invalidated before this session opened it renders a note, not a button.
  return belief.status === "active";
}
