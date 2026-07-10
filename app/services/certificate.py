"""Audit certificate — the tamper-evident record of a belief invalidation (Phase 3).

A certificate is a self-contained JSON document proving WHAT was invalidated, WHY (the real
staleness evidence from belief_performance — "valid then / rotten now", never asserted), WHO
did it, WHEN, and the full affected closure. It is PUT to S3 by s3_audit.py.

Integrity model (no HMAC, per plan):
  1. SELF-CONTAINED pre-kill record. The certificate body carries pre_invalidation_state — the
     MEASURED "belief active, whole closure open" fact captured at issue-time (inside the
     invalidation txn, before the flip; or, for the certifier Lambda, from its independent AOST
     replay). It is hash-covered, so the document proves what the world was immediately before
     the kill WITHOUT depending on any external lookup. This is the primary integrity mechanism.
  2. content_hash = sha256 over the canonical (sorted-key) JSON of every field except the hash
     itself. The round-trip test re-reads the object from S3 and re-derives this hash — any
     tampering with the pre-kill record (or anything else) breaks it.
  3. db_snapshot_hlc pins the pre-invalidation MVCC version as a BONUS freshness cross-check:
     within CockroachDB's GC window (gc.ttlseconds, ~75 min) anyone can replay the cluster
     `AS OF SYSTEM TIME db_snapshot_hlc` and independently reproduce the same world. Once the
     snapshot ages past the GC TTL this replay no longer resolves — which is exactly why the
     self-contained record in (1) exists: the certificate does not silently lose its integrity
     guarantee after 75 minutes.
  4. pre_invalidation_state.closure_content_hash CONTENT-ADDRESSES the pre-kill world
     (Item 6). The counts in (1) say "8 of 8 edges were open"; this says WHICH world those
     counts summarize — sha256 over the canonically-serialized belief row plus every closure
     edge's revocation state, the same digest `GET /beliefs/{id}/replay` returns. The certifier
     Lambda reconstructs that world independently via its own AOST replay and compares hashes,
     so the pre-kill claim is re-derived on separate compute rather than taken on the app's
     word. See `closure_world` / `canonical_digest` below.

What this integrity model does NOT provide, stated so nobody mistakes it for more (Item 6):
  content_hash is an UNKEYED digest, so it proves nothing about AUTHORSHIP. Anyone can forge a
  certificate and compute a perfectly self-consistent hash for it; `verify()` would return True.
  What actually anchors authenticity is the DATABASE — audit_log.content_hash holds the expected
  digest, and within the GC window the AOST replay reproduces the claimed world from CRDB's own
  MVCC history. A forgery has neither. The residual gap is that an offline third party holding
  ONLY the JSON, with no cluster access, cannot verify authorship. Asymmetric signing would close
  it (HMAC would not — a shared secret lets the verifier forge too). Deliberately not built: it
  needs a new AWS service, which CLAUDE.md forbids adding unasked. See NOTES.md "Roadmap Item 6".
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid

# 1.1 (Item 6) adds pre_invalidation_state.closure_content_hash + the certifier's
# closure_verification stamp. Additive only: `_digest` hashes whatever keys are present and
# `verify()` re-derives over the same set, so 1.0 certificates still verify unchanged.
SCHEMA_VERSION = "1.1"

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
    # Self-contained pre-kill record (hash-covered). Callers supply it measured (endpoint:
    # issue-time read inside the txn; Lambda: AOST replay). Fall back to deriving it from the
    # affected counts so the field is always present and the document is never GC-dependent.
    pre_state = inv.get("pre_state") or {
        "belief_status": "active",
        "closure_edge_total": inv["affected_edge_count"],
        "closure_edge_open": inv["affected_edge_count"],
        "affected_agent_count": inv["affected_agent_count"],
        "living_holder_count": len(living),
        "snapshot_hlc": inv["snapshot_hlc"],
        # A derived pre_state has no reconstructed world behind it, so it cannot content-address
        # one. Present-and-null, never absent: a consumer distinguishes "no hash" from "field
        # missing" without knowing which caller built the certificate.
        "closure_content_hash": None,
        "source": "derived",
    }
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
        # The self-contained pre-kill world (see module docstring, integrity mechanism #1).
        "pre_invalidation_state": pre_state,
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


def canonical_json(obj) -> bytes:
    """The project's ONE canonical serialization: sorted keys, no incidental whitespace.

    Every content hash in this system — the certificate's, and the replay snapshot's — is a
    digest of this function's output. It lives here because this module is the import-safe one
    (see the note above): app-side code and the certifier Lambda can both reach it.
    """
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), default=_json_default
    ).encode("utf-8")


def canonical_digest(obj) -> str:
    """sha256 over canonical_json(obj), prefixed with its algorithm."""
    return "sha256:" + hashlib.sha256(canonical_json(obj)).hexdigest()


def closure_world(belief: dict, closure: list[dict]) -> dict:
    """The reconstructed world a closure hash covers: one belief row + its inheritance closure.

    Shared, not duplicated, and that is the whole point. Two parties hash this world: the app
    (app/services/replay.py, async SQLAlchemy) and the certifier Lambda (sync psycopg, its own
    independent AOST replay). If each built the dict and the digest with its own code, a
    hash-equality check between them would only ever prove the two implementations still agree
    — a guarantee that silently evaporates the day one of them drifts. Both call THIS, so the
    only thing that can differ between them is what they read from CockroachDB, which is
    precisely what the comparison is supposed to be testing.

    The two callers' SELECTs must therefore produce the same column sets: belief =
    (id, rule_text, status, originating_agent_id, formed_at, invalidated_at); each closure row =
    (depth, agent_id, generation, bloodline, status, from_agent_id, inherited_at,
    edge_invalidated_at), in the total order (depth, generation, agent_id).
    """
    return {
        "belief": dict(belief),
        "origin_agent_id": belief["originating_agent_id"],
        "closure": [dict(r) for r in closure],
    }


def _digest(cert: dict) -> str:
    """sha256 over the canonical JSON of every field except content_hash."""
    return canonical_digest({k: v for k, v in cert.items() if k != "content_hash"})


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
