"""Belief endpoints — lineage (Phase 1) + atomic invalidation (Phase 3)."""

import asyncio
import uuid

from fastapi import APIRouter, HTTPException, Query

from app.schemas import (
    BeliefListResponse,
    BeliefOut,
    InvalidateRequest,
    InvalidateResponse,
    LineageResponse,
)
from app.services import catalog, certificate, invalidation, lineage, s3_audit

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


@router.get("/beliefs/{belief_id}/lineage", response_model=LineageResponse)
async def get_belief_lineage(belief_id: uuid.UUID) -> LineageResponse:
    result = await lineage.belief_lineage(belief_id)
    if result is None:
        raise HTTPException(status_code=404, detail="belief not found")
    return LineageResponse(**result)


@router.post("/beliefs/{belief_id}/invalidate", response_model=InvalidateResponse)
async def invalidate(belief_id: uuid.UUID, body: InvalidateRequest) -> InvalidateResponse:
    """Atomically invalidate a belief and its full inherited closure, then certify to S3.

    The DB invalidation is one serializable CRDB transaction (all holders at once). The S3
    certificate is a POST-COMMIT side effect: if it fails, the invalidation still stands and
    the audit row is marked 'failed' for retry — correctness is never gated on S3.
    """
    try:
        inv = await invalidation.invalidate_belief(belief_id, body.actor_id)
    except invalidation.BeliefNotFound:
        raise HTTPException(status_code=404, detail="belief not found")
    except invalidation.AlreadyInvalidated:
        raise HTTPException(status_code=409, detail="belief is not active (already invalidated)")

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
        certificate_id=cert_id,
        certificate_s3_key=s3_key,
        certificate_status=cert_status,
        content_hash=cert["content_hash"],
    )
