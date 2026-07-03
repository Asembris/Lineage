"""Phase-3 done-tests: atomic closure invalidation + a REAL, cross-checkable certificate.

Four properties, each proven against the live cluster / real S3 (the certificate realness is
the point — nothing here is mocked):

  1. ATOMIC CLOSURE — one invalidation flips the belief AND every belief_inheritance edge; no
     edge is left open. The audit_log row records the exact affected counts.
  2. IDEMPOTENT — a second invalidation raises AlreadyInvalidated and writes no second effect.
  3. CERTIFICATE ROUND-TRIP — the POST path writes a certificate to S3 whose sha256 content
     hash re-verifies after a fresh GET, and whose staleness evidence is the real
     belief_performance curve (valid-then/rotten-now), not asserted.
  4. AOST REPRODUCIBILITY (the anti-decoration proof) — the certificate's db_snapshot_hlc
     replays the cluster AS OF SYSTEM TIME to independently reproduce the pre-invalidation
     world: belief active, every closure edge open, same affected agents. The certificate
     cannot lie about history because CockroachDB's own time-travel is the oracle.
"""

import asyncio
import datetime as dt
import uuid

import httpx
from sqlalchemy import insert, text

from app.db import engine
from app.main import app
from app.models import Decision
from app.services import certificate, s3_audit
from app.services.invalidation import (
    AlreadyInvalidated,
    invalidate_belief,
)
from app.services.performance import recompute_belief_performance
from seed.seed import aid, bid
from seed.seed import seed as run_seed

ORIGIN = bid("origin")
ACTOR = aid("crimson-7")  # a living holder standing in as the invalidating supervisor

BASE = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)
EARLY = (BASE - dt.timedelta(days=400), BASE - dt.timedelta(days=390))
LATE = (BASE - dt.timedelta(days=100), BASE - dt.timedelta(days=90))


def _decisions(at, n, frauds):
    """n belief-driven APPROVE rows, `frauds` of them fraudulent (deterministic)."""
    return [
        {
            "id": uuid.uuid4(),
            "agent_id": ACTOR,
            "txn_ref": f"inv-{at.date()}-{i:03d}",
            "merchant": "Grocery Mart #500",
            "amount": 99.00,
            "verdict": "approve",
            "driving_belief_id": ORIGIN,
            "confidence": 0.9,
            "decided_at": at,
            "is_fraud": i < frauds,
        }
        for i in range(n)
    ]


async def _seed_with_performance():
    """Seed genealogy + a real belief_performance curve (0.95 early -> 0.45 late)."""
    await run_seed()
    async with engine.begin() as c:
        await c.execute(insert(Decision), _decisions(EARLY[0] + dt.timedelta(days=5), 200, 10))
        await c.execute(insert(Decision), _decisions(LATE[0] + dt.timedelta(days=5), 200, 110))
    await recompute_belief_performance(ORIGIN, [EARLY, LATE])


async def _closure_state():
    """(belief status, total edges, open edges, affected agent ids) at current time."""
    async with engine.connect() as c:
        status = (
            await c.execute(text("SELECT status FROM beliefs WHERE id=:b"), {"b": ORIGIN})
        ).scalar_one()
        edges = (
            await c.execute(
                text(
                    "SELECT count(*) tot, count(invalidated_at) inv, "
                    "count(*) - count(invalidated_at) open FROM belief_inheritance "
                    "WHERE belief_id=:b"
                ),
                {"b": ORIGIN},
            )
        ).mappings().one()
    return status, edges["tot"], edges["open"]


def test_invalidation_is_atomic_over_the_closure():
    async def _run():
        try:
            await run_seed()
            # Before: active, all edges open.
            status0, tot0, open0 = await _closure_state()
            assert status0 == "active" and tot0 == 8 and open0 == 8, (status0, tot0, open0)

            res = await invalidate_belief(ORIGIN, ACTOR)
            assert res["affected_agent_count"] == 9, res  # 8 spine + 1 branch holder
            assert res["affected_edge_count"] == 8, res

            # After: invalidated, ZERO edges left open (the atomic closure).
            status1, tot1, open1 = await _closure_state()
            assert status1 == "invalidated", status1
            assert tot1 == 8 and open1 == 0, (tot1, open1)

            # Audit row present with the right counts.
            async with engine.connect() as c:
                row = (
                    await c.execute(
                        text(
                            "SELECT action, affected_agent_count, affected_edge_count, "
                            "commit_hlc FROM audit_log WHERE belief_id=:b"
                        ),
                        {"b": ORIGIN},
                    )
                ).mappings().all()
            assert len(row) == 1, row
            assert row[0]["action"] == "belief_invalidation"
            assert row[0]["affected_agent_count"] == 9
            assert row[0]["affected_edge_count"] == 8
            assert row[0]["commit_hlc"], "snapshot HLC must be recorded"
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_invalidation_is_idempotent():
    async def _run():
        try:
            await run_seed()
            await invalidate_belief(ORIGIN, ACTOR)
            raised = False
            try:
                await invalidate_belief(ORIGIN, ACTOR)
            except AlreadyInvalidated:
                raised = True
            assert raised, "second invalidation must raise AlreadyInvalidated"

            # Still exactly one audit row; still zero open edges.
            _, tot, open_ = await _closure_state()
            assert tot == 8 and open_ == 0, (tot, open_)
            async with engine.connect() as c:
                n = (
                    await c.execute(
                        text("SELECT count(*) FROM audit_log WHERE belief_id=:b"),
                        {"b": ORIGIN},
                    )
                ).scalar_one()
            assert n == 1, f"expected 1 audit row, got {n}"
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_certificate_round_trips_from_s3_and_hash_verifies():
    async def _run():
        try:
            await _seed_with_performance()
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                r = await client.post(
                    f"/beliefs/{ORIGIN}/invalidate", json={"actor_id": str(ACTOR)}
                )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["certificate_status"] == "written", body
            key = body["certificate_s3_key"]

            # Real GET from S3, then re-derive the hash over the fetched document.
            fetched = await asyncio.to_thread(s3_audit.get_certificate, key)
            assert certificate.verify(fetched), "content_hash failed to re-verify after S3 GET"
            assert fetched["content_hash"] == body["content_hash"]

            # Staleness evidence is the REAL curve, not asserted.
            ev = fetched["staleness_evidence"]
            assert ev["available"] is True, ev
            assert ev["confidence_when_formed"] > 0.9, ev  # valid then
            assert ev["confidence_now"] < 0.6, ev          # rotten now
            assert ev["frauds_approved_last_window"] == 110, ev
            assert fetched["affected_closure"]["agent_count"] == 9

            # Self-contained pre-kill record survives the S3 round-trip (hash-covered): the
            # cert proves the belief was active + closure fully open WITHOUT needing AOST.
            pre = fetched["pre_invalidation_state"]
            assert pre["belief_status"] == "active", pre
            assert pre["closure_edge_total"] == 8 and pre["closure_edge_open"] == 8, pre
            assert pre["source"] == "issue-time-read", pre
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_certificate_aost_reproduces_pre_invalidation_state():
    async def _run():
        try:
            await _seed_with_performance()
            res = await invalidate_belief(ORIGIN, ACTOR)
            staleness = await certificate.gather_staleness_evidence(ORIGIN)
            cert = certificate.build_certificate(res, staleness)
            hlc = cert["db_snapshot_hlc"]

            # Replay the cluster AS OF the certificate's pinned MVCC version. HLC is inlined
            # (never a bind param) exactly as the Phase-1 AOST path requires.
            async with engine.connect() as c:
                async with c.begin():
                    await c.execute(text(f"SET TRANSACTION AS OF SYSTEM TIME {hlc}"))
                    past_status = (
                        await c.execute(
                            text("SELECT status FROM beliefs WHERE id=:b"), {"b": ORIGIN}
                        )
                    ).scalar_one()
                    past_open = (
                        await c.execute(
                            text(
                                "SELECT count(*) FROM belief_inheritance "
                                "WHERE belief_id=:b AND invalidated_at IS NULL"
                            ),
                            {"b": ORIGIN},
                        )
                    ).scalar_one()
                    past_agents = (
                        await c.execute(
                            text(
                                "SELECT count(DISTINCT to_agent_id) FROM belief_inheritance "
                                "WHERE belief_id=:b"
                            ),
                            {"b": ORIGIN},
                        )
                    ).scalar_one()

            # The certificate's claims reproduce from CRDB's own history: active, all edges
            # open, full closure intact — at the exact instant before the kill.
            assert past_status == "active", past_status
            assert past_open == 8, past_open
            assert past_agents == 8, past_agents

            # And current state is the opposite — the invalidation really happened.
            now_status, _, now_open = await _closure_state()
            assert now_status == "invalidated" and now_open == 0, (now_status, now_open)
        finally:
            await engine.dispose()

    asyncio.run(_run())
