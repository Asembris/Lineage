"""Audit certificate — the tamper-evident record of a belief invalidation (Phase 3).

A certificate is a self-contained JSON document proving WHAT was invalidated, WHY (the real
staleness evidence from belief_performance — "valid then / rotten now", never asserted), WHO
did it, WHEN, and the full affected closure. It is PUT to S3 by s3_audit.py.

Two realness proofs, no HMAC (per plan):
  1. content_hash = sha256 over the canonical (sorted-key) JSON of every field except the hash
     itself. The round-trip test re-reads the object from S3 and re-derives this hash.
  2. db_snapshot_hlc pins the pre-invalidation MVCC version. Anyone can replay the cluster
     `AS OF SYSTEM TIME db_snapshot_hlc` and independently reproduce the exact world the
     certificate claims (belief active, this closure, this staleness). The certificate cannot
     lie about history — CockroachDB's own time-travel is the oracle. This is why HMAC is
     unnecessary here: the claim is externally reproducible, not merely signed.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid

SCHEMA_VERSION = "1.0"

# NOTE: this module is import-safe with ZERO app/SQLAlchemy dependencies at load time, so the
# certifier Lambda can `import certificate` and reuse build_certificate/verify/storage_bytes
# without dragging in app.config (which requires DATABASE_URL/OPENAI_API_KEY). The only DB
# helper, gather_staleness_evidence, imports engine/text lazily inside the function; the Lambda
# does its own sync staleness query and never calls it.


async def gather_staleness_evidence(belief_id: uuid.UUID) -> dict:
    """Pull the belief's measured performance (the invalidation's data justification).

    Returns first-vs-last window confidence + last-window frauds_approved + the full series,
    straight from belief_performance. If the table has no rows for this belief, returns
    {available: False} — the certificate is still valid, just without a quantified curve.
    """
    from sqlalchemy import text

    from app.db import engine

    async with engine.connect() as c:
        rows = (
            await c.execute(
                text(
                    "SELECT window_start, window_end, confidence, false_positive_rate, "
                    "frauds_approved FROM belief_performance WHERE belief_id = :b "
                    "ORDER BY window_start"
                ),
                {"b": belief_id},
            )
        ).mappings().all()
    if not rows:
        return {"available": False, "window_count": 0, "windows": []}
    windows = [
        {
            "window_start": r["window_start"],
            "window_end": r["window_end"],
            "confidence": r["confidence"],
            "false_positive_rate": r["false_positive_rate"],
            "frauds_approved": int(r["frauds_approved"]),
        }
        for r in rows
    ]
    return {
        "available": True,
        "confidence_when_formed": windows[0]["confidence"],
        "confidence_now": windows[-1]["confidence"],
        "frauds_approved_last_window": windows[-1]["frauds_approved"],
        "window_count": len(windows),
        "windows": windows,
    }


def build_certificate(inv: dict, staleness: dict, extra: dict | None = None) -> dict:
    """Assemble the certificate dict (with content_hash) from an invalidation result.

    Pure function of its inputs — no I/O — so it is trivially testable. `extra` merges extra
    top-level fields (e.g. the certifier Lambda's aost_verification stamp) BEFORE hashing, so
    they are covered by content_hash.
    """
    belief = inv["belief"]
    agents = inv["affected_agents"]
    living = inv["living_holders"]
    cert = {
        "schema_version": SCHEMA_VERSION,
        "certificate_id": str(uuid.uuid4()),
        "issued_at": dt.datetime.now(dt.timezone.utc),
        "action": "belief_invalidation",
        "actor": str(inv["actor_id"]),
        "audit_id": str(inv["audit_id"]),
        "belief": {
            "id": str(belief["id"]),
            "rule_text": belief["rule_text"],
            "originating_agent_id": str(belief["originating_agent_id"]),
            "formed_at": belief["formed_at"],
            "status_before": "active",
            "status_after": "invalidated",
            "invalidated_at": inv["invalidated_at"],
        },
        "staleness_evidence": staleness,
        "affected_closure": {
            "agent_count": inv["affected_agent_count"],
            "edge_count": inv["affected_edge_count"],
            "living_holder_count": len(living),
            "agent_ids": [str(a["id"]) for a in agents],
            "living_holder_ids": [str(a["id"]) for a in living],
            "bloodlines": sorted({a["bloodline"] for a in agents}),
        },
        # Pre-invalidation MVCC version — the AOST cross-check oracle.
        "db_snapshot_hlc": inv["snapshot_hlc"],
    }
    if extra:
        cert.update(extra)
    cert["content_hash"] = _digest(cert)
    return cert


def _digest(cert: dict) -> str:
    """sha256 over the canonical JSON of every field except content_hash."""
    payload = {k: v for k, v in cert.items() if k != "content_hash"}
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_json_default)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def verify(cert: dict) -> bool:
    """Re-derive the content_hash and compare — detects any field tampering."""
    return cert.get("content_hash") == _digest(cert)


def storage_bytes(cert: dict) -> bytes:
    """Human-readable canonical JSON for the S3 object body (stable key order)."""
    return json.dumps(
        cert, sort_keys=True, indent=2, default=_json_default
    ).encode("utf-8")


def _json_default(o):
    if isinstance(o, dt.datetime):
        return o.isoformat()
    if isinstance(o, uuid.UUID):
        return str(o)
    raise TypeError(f"not JSON-serializable: {type(o)}")
