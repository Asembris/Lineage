"""Certifier Lambda — independent post-invalidation auditor (Phase 3, step 8).

Invoked with {"belief_id": "<uuid>"} (optionally "audit_id"). Real AWS compute doing real
CRDB + S3 work, NOT a local function wearing a Lambda costume:

  1. read the audit_log row  -> snapshot_hlc (the pre-kill MVCC version), actor, invalidated_at
  2. RE-VERIFY via AS OF SYSTEM TIME snapshot_hlc that the belief was 'active' and the whole
     closure was open BEFORE the kill — an independent time-travel audit on isolated compute,
     trusting CockroachDB's own history rather than any in-memory result
  2b. RE-DERIVE the closure content hash (Item 6): reconstruct the whole pre-kill world at the
     same instant and hash it with the SHARED canonicalizer (certificate.closure_world /
     canonical_digest), then compare against the hash the endpoint's certificate embedded. This
     is "recompute, don't trust the label" applied to the certificate itself. Hash-coverage
     proves a document has not CHANGED; it can never prove the document was TRUE. The only
     field this certifier would otherwise take on faith is the one it now re-derives.
  3. read current committed state + belief_performance (staleness evidence)
  4. build the certificate (+ aost_verification and closure_verification stamps, hash-covered)
     and WRITE it to S3
  5. stamp audit_log: cert_status='written', cert_s3_key, content_hash, certificate_id

Agreement is a TRI-STATE, never a silent pass. 'agreed' / 'disagreed' / 'unavailable' — the
last when the endpoint's certificate never reached S3 (cert_status='failed') or predates schema
1.1 and carries no closure hash. A missing counterparty must not read as a successful check.

Runtime notes (Linux, Lambda Python 3.12):
  * psycopg 3 SYNC — no asyncio, no Windows selector-loop dance.
  * boto3 comes from the Lambda runtime; default cred chain = the execution role (no keys, and
    no AVG-MITM CA hack — that was a Windows-only problem).
  * TLS verify-full: Linux libpq does not reliably locate a CA for CRDB Cloud, so we pass
    sslrootcert=certifi.where() (certifi is packaged in the zip) — the same class of fix as the
    app's Windows path, never sslmode=disable.
  * DATABASE_URL + S3_BUCKET come from encrypted Lambda env vars; DATABASE_URL is never logged.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import uuid

import boto3
import certifi
import psycopg
from psycopg.rows import dict_row

import certificate  # packaged flat alongside this handler

_HLC = re.compile(r"^\d+(\.\d+)?$")  # audit_log.commit_hlc is a numeric HLC decimal

# The SAME two SELECTs app/services/replay.py issues, in the SAME total order, returning the
# SAME column sets. They must stay in step: the hash comparison below is only meaningful if the
# two sides differ in nothing but WHERE the rows came from. certificate.closure_world documents
# the contract; this is the sync psycopg half of it.
_BELIEF_SQL = (
    "SELECT id, rule_text, status, originating_agent_id, formed_at, invalidated_at "
    "FROM beliefs WHERE id=%s"
)
_CLOSURE_SQL = """
    WITH RECURSIVE chain AS (
        SELECT a.id AS agent_id, a.generation, a.bloodline, a.status,
               CAST(NULL AS UUID) AS from_agent_id,
               CAST(NULL AS TIMESTAMPTZ) AS inherited_at,
               CAST(NULL AS TIMESTAMPTZ) AS edge_invalidated_at,
               0 AS depth
        FROM agents a
        WHERE a.id = %(origin_id)s
        UNION ALL
        SELECT a.id, a.generation, a.bloodline, a.status,
               bi.from_agent_id, bi.inherited_at, bi.invalidated_at, chain.depth + 1
        FROM belief_inheritance bi
        JOIN chain ON bi.from_agent_id = chain.agent_id
        JOIN agents a ON a.id = bi.to_agent_id
        WHERE bi.belief_id = %(belief_id)s
    )
    SELECT depth, agent_id, generation, bloodline, status,
           from_agent_id, inherited_at, edge_invalidated_at
    FROM chain
    ORDER BY depth, generation, agent_id
"""


def _issue_time_closure_hash(bucket: str, key: str | None, status: str | None) -> tuple:
    """Fetch the endpoint's certificate from S3 and read the closure hash it committed to.

    Returns (hash_or_None, compared_against_source). The endpoint writes its certificate
    post-commit; if that PUT failed there is nothing to compare against, and a certificate
    written before schema 1.1 carries no closure hash. Both cases are 'unavailable', never a
    pass. The source of the fetched pre_state is reported too, because a SECOND certifier run
    would find a previous LAMBDA certificate at this key rather than the endpoint's — the
    comparison is still real, but the reader deserves to know whose word was checked.
    """
    if not key or status != "written":
        return None, None
    try:
        body = boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
        pre = json.loads(body).get("pre_invalidation_state") or {}
        return pre.get("closure_content_hash"), pre.get("source")
    except Exception:  # noqa: BLE001 — an unreadable counterparty is 'unavailable', not a crash
        return None, None


def _connect() -> psycopg.Connection:
    url = os.environ["DATABASE_URL"]  # raw postgresql://...  (never logged)
    return psycopg.connect(
        url, sslrootcert=certifi.where(), connect_timeout=15, row_factory=dict_row
    )


def lambda_handler(event: dict, context) -> dict:
    belief_id = event["belief_id"]
    audit_id = event.get("audit_id")
    bucket = os.environ["S3_BUCKET"]

    conn = _connect()
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            # 1. the audit record (latest for the belief unless a specific audit_id is given)
            if audit_id:
                cur.execute(
                    "SELECT id, actor, commit_hlc, created_at, cert_s3_key, cert_status "
                    "FROM audit_log WHERE id=%s",
                    (audit_id,),
                )
            else:
                cur.execute(
                    "SELECT id, actor, commit_hlc, created_at, cert_s3_key, cert_status "
                    "FROM audit_log WHERE belief_id=%s ORDER BY created_at DESC LIMIT 1",
                    (belief_id,),
                )
            audit = cur.fetchone()
            if audit is None:
                raise RuntimeError(f"no audit_log row for belief {belief_id}")
            hlc = str(audit["commit_hlc"])
            if not _HLC.match(hlc):
                raise RuntimeError(f"audit_log.commit_hlc is not a valid HLC: {hlc!r}")

            # 2. current committed state
            cur.execute(
                "SELECT id, rule_text, status, originating_agent_id, formed_at, "
                "invalidated_at FROM beliefs WHERE id=%s",
                (belief_id,),
            )
            belief = cur.fetchone()
            if belief is None:
                raise RuntimeError(f"belief {belief_id} not found")

            cur.execute(
                "SELECT a.id, a.generation, a.bloodline, a.status FROM agents a "
                "WHERE a.id=(SELECT originating_agent_id FROM beliefs WHERE id=%s) "
                "OR a.id IN (SELECT to_agent_id FROM belief_inheritance WHERE belief_id=%s) "
                "ORDER BY a.generation, a.id",
                (belief_id, belief_id),
            )
            agents = cur.fetchall()

            cur.execute(
                "SELECT count(*) AS n FROM belief_inheritance WHERE belief_id=%s",
                (belief_id,),
            )
            edge_count = cur.fetchone()["n"]

            cur.execute(
                "SELECT window_start, window_end, confidence, false_positive_rate, "
                "frauds_approved FROM belief_performance WHERE belief_id=%s "
                "ORDER BY window_start",
                (belief_id,),
            )
            perf = cur.fetchall()

        # 3. INDEPENDENT AOST re-verification — a separate txn whose FIRST statement is the
        #    time-travel set. The HLC is inlined (never a bind param); it is our own validated
        #    numeric audit value, so there is no injection surface.
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute(f"SET TRANSACTION AS OF SYSTEM TIME {hlc}")
            cur.execute(_BELIEF_SQL, (belief_id,))
            past_belief = cur.fetchone()
            past_status = past_belief["status"]
            cur.execute(
                "SELECT count(*) - count(invalidated_at) AS open, count(*) AS tot "
                "FROM belief_inheritance WHERE belief_id=%s",
                (belief_id,),
            )
            r = cur.fetchone()
            past_open, past_total = r["open"], r["tot"]
            # 2b. The whole pre-kill world, reconstructed here rather than accepted from the
            #     certificate. Same txn, so it is the same MVCC snapshot as the counts above:
            #     the hash and the counts can never describe different instants.
            cur.execute(
                _CLOSURE_SQL,
                {"origin_id": past_belief["originating_agent_id"], "belief_id": belief_id},
            )
            past_closure = cur.fetchall()
        conn.rollback()
        conn.autocommit = True

        aost_verified = (
            past_status == "active" and past_open == past_total and past_total > 0
        )

        # Hashed with the SHARED canonicalizer, then compared against what the endpoint
        # committed to. Disagreement is reported, not raised: this certifier's job is to state
        # what it independently found, and a certificate that records a mismatch is far more
        # useful to an auditor than a Lambda that crashed.
        derived_hash = certificate.canonical_digest(
            certificate.closure_world(past_belief, past_closure)
        )
        issue_hash, compared_source = _issue_time_closure_hash(
            bucket, audit.get("cert_s3_key"), audit.get("cert_status")
        )
        if issue_hash is None:
            agreement = "unavailable"
        elif issue_hash == derived_hash:
            agreement = "agreed"
        else:
            agreement = "disagreed"

        # 4. build + write the certificate
        staleness = _staleness(perf)
        living = [a for a in agents if a["status"] == "alive"]
        inv = {
            "audit_id": audit["id"],
            "belief": belief,
            "actor_id": audit["actor"],
            "invalidated_at": belief["invalidated_at"],
            "snapshot_hlc": hlc,
            "affected_agents": agents,
            "living_holders": living,
            "affected_agent_count": len(agents),
            "affected_edge_count": edge_count,
            # Self-contained pre-kill record, measured INDEPENDENTLY via this Lambda's AOST
            # replay (source='aost-replay') — the certifier's own oracle, not the app's word.
            "pre_state": {
                "belief_status": past_status,
                "closure_edge_total": int(past_total),
                "closure_edge_open": int(past_open),
                "affected_agent_count": len(agents),
                "living_holder_count": len(living),
                "snapshot_hlc": hlc,
                # This certifier's OWN content-address of the pre-kill world, from its own
                # replay — never copied from the certificate it is checking.
                "closure_content_hash": derived_hash,
                "source": "aost-replay",
            },
        }
        extra = {
            "issued_by": "aws-lambda:lineage-certifier",
            "aost_verification": {
                "snapshot_hlc": hlc,
                "belief_active_before": past_status == "active",
                "closure_open_before": int(past_open),
                "closure_total_before": int(past_total),
                "verified": bool(aost_verified),
            },
            # What this certifier re-derived, what it was checked against, and whether they
            # matched. Hash-covered like everything else, so the verdict cannot be edited out.
            "closure_verification": {
                "snapshot_hlc": hlc,
                "rederived_closure_hash": derived_hash,
                "issue_time_closure_hash": issue_hash,
                "compared_against_source": compared_source,
                "compared_against_s3_key": audit.get("cert_s3_key") if issue_hash else None,
                "agreement": agreement,
            },
        }
        cert = certificate.build_certificate(inv, staleness, extra=extra)
        cert_id = cert["certificate_id"]
        key = f"certificates/{belief_id}/{cert_id}.json"

        boto3.client("s3").put_object(
            Bucket=bucket,
            Key=key,
            Body=certificate.storage_bytes(cert),
            ContentType="application/json",
        )

        # 5. close the audit loop
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE audit_log SET cert_status='written', certificate_id=%s, "
                "cert_s3_key=%s, content_hash=%s WHERE id=%s",
                (cert_id, key, cert["content_hash"], audit["id"]),
            )

        return {
            "ok": True,
            "certificate_id": cert_id,
            "s3_bucket": bucket,
            "s3_key": key,
            "content_hash": cert["content_hash"],
            "aost_verified": bool(aost_verified),
            "closure_hash_agreement": agreement,
            "rederived_closure_hash": derived_hash,
            "affected_agent_count": len(agents),
            "affected_edge_count": int(edge_count),
        }
    finally:
        conn.close()


def _staleness(perf: list[dict]) -> dict:
    if not perf:
        return {"available": False, "window_count": 0, "windows": []}
    windows = [
        {
            "window_start": r["window_start"],
            "window_end": r["window_end"],
            "confidence": r["confidence"],
            "false_positive_rate": r["false_positive_rate"],
            "frauds_approved": int(r["frauds_approved"]),
        }
        for r in perf
    ]
    return {
        "available": True,
        "confidence_when_formed": windows[0]["confidence"],
        "confidence_now": windows[-1]["confidence"],
        "frauds_approved_last_window": windows[-1]["frauds_approved"],
        "window_count": len(windows),
        "windows": windows,
    }
