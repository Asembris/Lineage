/*
 * Invalidate — the fourth and final supervisor interaction, and the ONE governed write.
 *
 * It lives at the bottom of the Investigation take-over (after Trace + Time-travel), so the
 * console's one evolving surface ends on the consequence. Unlike the three reads, it is
 * confirmation-gated: an armed --alert button opens a confirm panel stating the real scope
 * (living holders + inheritance edges, fleet-wide, one commit) with an explicit Confirm/Cancel
 * — a deliberate act, not a casual click. App owns the state + the tree animation (two regions
 * coordinate, exactly like Trace); this component is the Inspector-side gate and result.
 *
 * On success it shows the REAL certificate outcome returned by POST /beliefs/{id}/invalidate —
 * never a generic toast: the sealed, hash-covered pre_invalidation_state (the same value the
 * backend embeds in the S3 certificate — a round-trip test proves byte-identity), the sha256
 * content hash, the S3 key, the MVCC snapshot HLC, and the certificate status. A 'failed'
 * certificate is surfaced honestly: the invalidation is durable regardless of the S3 write.
 *
 * Colors: --alert (the consequential action) and --alive (the correction) only.
 */

import type { Belief, InvalidateResponse, UUID } from "../api/types";
import { fragId, formatDate } from "../lib/format";
import { SUPERVISOR_ACTOR } from "../lib/supervisor";
import "./Invalidate.css";

/** UI-facing view of App's invalidate state (App owns the real state + tree animation). */
export type InvalidateUi =
  | { status: "idle" }
  | { status: "arming" } // loading the closure to populate the confirm scope
  | { status: "confirming"; livingHolderCount: number; edgeCount: number }
  | { status: "invalidating"; livingHolderCount: number; edgeCount: number }
  | { status: "done"; result: InvalidateResponse }
  | { status: "error"; message: string; alreadyInvalidated: boolean };

export interface InvalidateHandlers {
  ui: InvalidateUi;
  onArm: (beliefId: UUID) => void;
  onConfirm: () => void;
  onCancel: () => void;
}

/* `isInvalidateControl` — which states are pinned controls vs. scrolling receipts — lives in
   lib/invalidate.ts (this file exports only components). */

/* The real certificate outcome — the honest end of the write. */
function Outcome({ result }: { result: InvalidateResponse }) {
  const pre = result.pre_invalidation_state;
  const failed = result.certificate_status === "failed";
  return (
    <div className="kill-done">
      <div className="kill-done__head">
        <span className="kill-done__badge">belief invalidated</span>
        <span className="kill-done__scope">
          {result.living_holder_count} holders · {result.affected_edge_count} edges · one commit
        </span>
      </div>

      {/* The self-contained pre-kill proof — the value sealed AND hash-covered in the S3
          certificate, served straight from the invalidation response (one source of truth). */}
      <div className="kill-done__pre">
        <span className="kill-done__pre-label">pre-kill state · sealed in certificate</span>
        <p className="kill-done__pre-body">
          belief <b>{pre.belief_status}</b> · closure {pre.closure_edge_open}/{pre.closure_edge_total} open ·{" "}
          {pre.living_holder_count} living holders
          <span className="kill-done__pre-src"> ({pre.source})</span>
        </p>
      </div>

      {/* Certificate outcome. */}
      <dl className="kill-done__cert">
        <div>
          <dt>certificate</dt>
          <dd className={`mono ${failed ? "kill-done__cert-failed" : "kill-done__cert-ok"}`}>
            {result.certificate_status}
          </dd>
        </div>
        <div>
          <dt>sha256</dt>
          <dd className="mono kill-done__hash">{result.content_hash ?? "—"}</dd>
        </div>
        {result.certificate_s3_key && (
          <div>
            <dt>s3 key</dt>
            <dd className="mono kill-done__hash">{result.certificate_s3_key}</dd>
          </div>
        )}
        <div>
          <dt>snapshot hlc</dt>
          <dd className="mono kill-done__hash">{result.db_snapshot_hlc}</dd>
        </div>
        <div>
          <dt>audit</dt>
          <dd className="mono">{fragId(result.audit_id)}</dd>
        </div>
        <div>
          <dt>at</dt>
          <dd className="mono">{formatDate(result.invalidated_at)}</dd>
        </div>
      </dl>

      {failed && (
        <p className="panel__note kill-done__note">
          The invalidation is durable and committed; only the S3 certificate write failed (marked
          for retry). Correctness never gates on the external write.
        </p>
      )}
    </div>
  );
}

export function InvalidateBlock({
  belief,
  handlers,
}: {
  belief: Belief;
  handlers: InvalidateHandlers;
}) {
  const { ui, onArm, onConfirm, onCancel } = handlers;

  // Success this session — the real certificate outcome. Shown regardless of the (now stale)
  // catalog status, because `done` carries the authoritative response.
  if (ui.status === "done") return <Outcome result={ui.result} />;

  if (ui.status === "error") {
    if (ui.alreadyInvalidated) {
      return (
        <p className="panel__note kill__note">
          This belief was already invalidated fleet-wide (by another supervisor or a prior run) —
          nothing left to correct.
        </p>
      );
    }
    return (
      <div className="kill">
        <p className="panel__note kill__note kill__note--err">Invalidation failed: {ui.message}</p>
        <button className="kill__arm" onClick={() => onArm(belief.id)}>
          Retry invalidation
        </button>
      </div>
    );
  }

  // Belief already invalidated before this session opened it — no action to offer.
  if (belief.status !== "active") {
    return (
      <p className="panel__note kill__note">
        This belief is already invalidated — fleet-wide, nothing to correct.
      </p>
    );
  }

  if (ui.status === "idle") {
    return (
      <button className="kill__arm" onClick={() => onArm(belief.id)}>
        Invalidate belief fleet-wide
      </button>
    );
  }

  if (ui.status === "arming") {
    return (
      <button className="kill__arm" disabled>
        Loading closure…
      </button>
    );
  }

  // confirming | invalidating — the confirmation gate.
  const busy = ui.status === "invalidating";
  return (
    <div className="kill kill--armed">
      <p className="kill__warn">
        Invalidate belief <span className="mono">{fragId(belief.id)}</span> and its full inherited
        closure — <b>{ui.livingHolderCount} living holders</b> across{" "}
        <b>{ui.edgeCount} inheritance edges</b> — fleet-wide, in one atomic commit. This corrects
        every living holder at once and cannot be undone from here.
      </p>
      <p className="kill__actor">
        acting as supervisor <span className="mono">{fragId(SUPERVISOR_ACTOR)}</span>
      </p>
      {/* CONFIRM IS DELIBERATELY NOT WHERE THE ARM BUTTON WAS. With the controls pinned, the arm
          button and the confirm button would both be bottom-anchored in the same footer — and two
          clicks of muscle memory in one screen position would be an irreversible fleet-wide write.
          A confirm gate protects against not KNOWING; it cannot protect against not MOVING YOUR
          HAND. So the actions stack, and CANCEL takes the arm button's exact footprint: a repeated
          click at the remembered position now CANCELS. The geometry is asserted, not assumed —
          see the disjoint-rect check in the verification. DOM order matches visual order, so tab
          order is unchanged. */}
      <div className="kill__actions">
        <button className="kill__confirm" onClick={onConfirm} disabled={busy}>
          {busy ? "Invalidating…" : "Confirm invalidation"}
        </button>
        <button className="kill__cancel" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
    </div>
  );
}
