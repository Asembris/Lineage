"""Belief endpoints — lineage (Phase 1) + atomic invalidation (Phase 3)."""

import asyncio
import uuid

from fastapi import APIRouter, HTTPException, Query

from app.schemas import (
    BeliefListResponse,
    BeliefOut,
    BeliefPerformanceResponse,
    BeliefPerformanceWindow,
    InvalidateRequest,
    InvalidateResponse,
    LineageResponse,
    PreInvalidationState,
    ReplaySnapshotResponse,
)
from app.resilience import TransientRetryExhausted, run_with_retry
from app.services import catalog, certificate, invalidation, lineage, replay, s3_audit

router = APIRouter(tags=["beliefs"])


@router.get("/beliefs", response_model=BeliefListResponse)
async def list_beliefs(
    status: str | None = Query(
        None, description="Filter by status ('active' | 'invalidated')."
    ),
) -> BeliefListResponse:
    """The belief catalog for the investigate view (full list; founding beliefs are few)."""
    rows = await catalog.list_beliefs(status=status)
    beliefs = [BeliefOut(**r) for r in rows]
    return BeliefListResponse(beliefs=beliefs, count=len(beliefs))


@router.get("/beliefs/{belief_id}/performance", response_model=BeliefPerformanceResponse)
async def get_belief_performance(belief_id: uuid.UUID) -> BeliefPerformanceResponse:
    """The measured staleness curve for a belief — ordered belief_performance windows.

    Real measured data only (confidence = correct/total per window, never asserted). Unknown
    belief → 404; a known belief with no measured windows → 200 with an empty list.
    """
    rows = await catalog.list_belief_performance(belief_id)
    if rows is None:
        raise HTTPException(status_code=404, detail="belief not found")
    windows = [BeliefPerformanceWindow(**r) for r in rows]
    return BeliefPerformanceResponse(belief_id=belief_id, windows=windows, count=len(windows))


@router.get("/beliefs/{belief_id}/lineage", response_model=LineageResponse)
async def get_belief_lineage(belief_id: uuid.UUID) -> LineageResponse:
    result = await lineage.belief_lineage(belief_id)
    if result is None:
        raise HTTPException(status_code=404, detail="belief not found")
    return LineageResponse(**result)


@router.get("/beliefs/{belief_id}/replay", response_model=ReplaySnapshotResponse)
async def replay_belief_closure(
    belief_id: uuid.UUID,
    as_of: str | None = Query(
        None,
        description="Replay point: ISO-8601 timestamp or CRDB HLC decimal. Omit for a "
        "current-state snapshot. Bounded to the AOST window (~75 min); older -> 400.",
    ),
) -> ReplaySnapshotResponse:
    """Reconstruct this belief's inheritance closure AS OF a past time, canonically hashed.

    Real CockroachDB time-travel over the belief-closure recursive CTE. The `content_hash`
    makes replay byte-identical: two reads at the same timestamp reproduce the same world.
    A malformed / out-of-window `as_of` -> 400; an unknown belief -> 404. The foundation
    later items build counterfactual invalidation (B) and confidence propagation (D) on.
    """
    try:
        result = await replay.closure_snapshot(belief_id, as_of)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if result is None:
        raise HTTPException(status_code=404, detail="belief not found")
    return ReplaySnapshotResponse(**result)


@router.post("/beliefs/{belief_id}/invalidate", response_model=InvalidateResponse)
async def invalidate(belief_id: uuid.UUID, body: InvalidateRequest) -> InvalidateResponse:
    """Atomically invalidate a belief and its full inherited closure, then certify to S3.

    The DB invalidation is one serializable CRDB transaction (all holders at once). The S3
    certificate is a POST-COMMIT side effect: if it fails, the invalidation still stands and
    the audit row is marked 'failed' for retry — correctness is never gated on S3.
    """
    # The atomic invalidation is one serializable CRDB transaction; a contended commit can hit a
    # transient 40001 / a dropped Cloud connection. Retry the WHOLE transaction (the correct unit
    # for serialization retries) with bounded backoff. BeliefNotFound / AlreadyInvalidated are
    # deterministic, so run_with_retry re-raises them at once → 404 / 409 unchanged. A transient
    # that outlives the budget becomes a clean 503, never an opaque 500.
    try:
        inv = await run_with_retry(
            lambda: invalidation.invalidate_belief(belief_id, body.actor_id)
        )
    except invalidation.BeliefNotFound:
        raise HTTPException(status_code=404, detail="belief not found")
    except invalidation.AlreadyInvalidated:
        raise HTTPException(status_code=409, detail="belief is not active (already invalidated)")
    except TransientRetryExhausted:
        raise HTTPException(
            status_code=503,
            detail="database temporarily unavailable (transient); please retry",
        )

    # Post-commit: build + write the certificate. Failure does not undo the invalidation.
    staleness = await certificate.gather_staleness_evidence(belief_id)
    cert = certificate.build_certificate(inv, staleness)
    cert_id: uuid.UUID | None = uuid.UUID(cert["certificate_id"])
    cert_status = "written"
    s3_key: str | None = None
    try:
        put = await asyncio.to_thread(s3_audit.put_certificate, cert)
        s3_key = put["key"]
    except Exception:  # noqa: BLE001 — any boto3/network failure is retriable, not fatal
        cert_status = "failed"

    await invalidation.record_certificate_result(
        inv["audit_id"],
        status=cert_status,
        certificate_id=cert_id,
        s3_key=s3_key,
        content_hash=cert["content_hash"],
    )

    return InvalidateResponse(
        belief_id=belief_id,
        status="invalidated",
        actor_id=body.actor_id,
        invalidated_at=inv["invalidated_at"],
        affected_agent_count=inv["affected_agent_count"],
        affected_edge_count=inv["affected_edge_count"],
        living_holder_count=len(inv["living_holders"]),
        db_snapshot_hlc=inv["snapshot_hlc"],
        audit_id=inv["audit_id"],
        # Same dict that build_certificate embedded + hashed above (inv["pre_state"]) — one
        # source of truth, so the response and the S3 certificate can never disagree.
        pre_invalidation_state=PreInvalidationState(**inv["pre_state"]),
        certificate_id=cert_id,
        certificate_s3_key=s3_key,
        certificate_status=cert_status,
        content_hash=cert["content_hash"],
    )
