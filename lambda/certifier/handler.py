"""Certifier Lambda — independent post-invalidation auditor (Phase 3, step 8).

Invoked with {"belief_id": "<uuid>"} (optionally "audit_id"). Real AWS compute doing real
CRDB + S3 work, NOT a local function wearing a Lambda costume:

  1. read the audit_log row  -> snapshot_hlc (the pre-kill MVCC version), actor, invalidated_at
  2. RE-VERIFY via AS OF SYSTEM TIME snapshot_hlc that the belief was 'active' and the whole
     closure was open BEFORE the kill — an independent time-travel audit on isolated compute,
     trusting CockroachDB's own history rather than any in-memory result
  3. read current committed state + belief_performance (staleness evidence)
  4. build the certificate (+ an aost_verification stamp, hash-covered) and WRITE it to S3
  5. stamp audit_log: cert_status='written', cert_s3_key, content_hash, certificate_id

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
import os
import re
import uuid

import boto3
import certifi
import psycopg
from psycopg.rows import dict_row

import certificate  # packaged flat alongside this handler

_HLC = re.compile(r"^\d+(\.\d+)?$")  # audit_log.commit_hlc is a numeric HLC decimal


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
                    "SELECT id, actor, commit_hlc, created_at FROM audit_log WHERE id=%s",
                    (audit_id,),
                )
            else:
                cur.execute(
                    "SELECT id, actor, commit_hlc, created_at FROM audit_log "
                    "WHERE belief_id=%s ORDER BY created_at DESC LIMIT 1",
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
            cur.execute("SELECT status FROM beliefs WHERE id=%s", (belief_id,))
            past_status = cur.fetchone()["status"]
            cur.execute(
                "SELECT count(*) - count(invalidated_at) AS open, count(*) AS tot "
                "FROM belief_inheritance WHERE belief_id=%s",
                (belief_id,),
            )
            r = cur.fetchone()
            past_open, past_total = r["open"], r["tot"]
        conn.rollback()
        conn.autocommit = True

        aost_verified = (
            past_status == "active" and past_open == past_total and past_total > 0
        )

        # 4. build + write the certificate
        staleness = _staleness(perf)
        inv = {
            "audit_id": audit["id"],
            "belief": belief,
            "actor_id": audit["actor"],
            "invalidated_at": belief["invalidated_at"],
            "snapshot_hlc": hlc,
            "affected_agents": agents,
            "living_holders": [a for a in agents if a["status"] == "alive"],
            "affected_agent_count": len(agents),
            "affected_edge_count": edge_count,
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
