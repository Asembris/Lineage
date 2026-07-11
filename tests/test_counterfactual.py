"""Done-test for Roadmap Item B — counterfactual "what-if invalidation".

Hermetic and deterministic: seeds its OWN small, fully-controlled belief-driven decisions set
(known counts, known fraud subset, known holders) so the affected-set logic is proven against
data the test owns — not against the 4,000-row backfill (whose real numbers, N=1000/M=392 at the
window-4 T, are confirmed by a live query, not by this test). Proves:

  * the affected set is exactly {driving_belief_id = X AND decided_at > T}, every row an approval
    (withdrawn_approvals == approvals), so invalidation only ever WITHDRAWS approvals;
  * a NULL-driver decision after T is NOT counted (invalidation cannot flip a non-driven verdict);
  * `decided_at > T` is STRICT — a decision exactly at T is excluded;
  * frauds_auto_approved is the real is_fraud subset of the affected set;
  * the per-window breakdown sums to the summary (buckets identically to belief_performance);
  * both extremes fall out of the plain WHERE with no special-casing: T before the earliest
    decision => the full driven set; T after the latest => an empty set.

Plus a pure test of parse_at (no DB). Mirrors test_staleness.py's controlled-seed philosophy.
"""

import asyncio
import datetime as dt
import uuid

import pytest
from sqlalchemy import insert

from app.db import engine
from app.models import Decision
from app.services import counterfactual as cf
from seed.seed import aid, bid, seed as run_seed

ORIGIN = bid("origin")
A7 = aid("crimson-7")   # a living holder
A5B = aid("crimson-5b")  # the branch holder
A6 = aid("crimson-6")

BASE = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)


def _row(agent, decided_at, *, fraud, driven=True, verdict="approve"):
    return {
        "id": uuid.uuid4(),
        "agent_id": agent,
        "txn_ref": f"cf-{decided_at.date()}-{uuid.uuid4().hex[:6]}",
        "merchant": "Grocery Mart #500",
        "amount": 99.00,
        "verdict": verdict,
        "driving_belief_id": ORIGIN if driven else None,
        "confidence": 0.9,
        "decided_at": decided_at,
        "is_fraud": fraud,
    }


def test_parse_at_forms_and_errors():
    # ISO date -> midnight UTC; naive datetime -> UTC; tz-aware preserved.
    assert cf.parse_at("2025-05-27") == dt.datetime(2025, 5, 27, tzinfo=dt.timezone.utc)
    assert cf.parse_at("2025-05-27T13:30:00") == dt.datetime(
        2025, 5, 27, 13, 30, tzinfo=dt.timezone.utc
    )
    assert cf.parse_at("2025-05-27T13:30:00+02:00").utcoffset() == dt.timedelta(hours=2)
    for bad in ("not-a-date", "", "2025-13-40", "yesterday"):
        with pytest.raises(ValueError):
            cf.parse_at(bad)


def test_counterfactual_affected_set_and_extremes():
    async def _run():
        try:
            await run_seed()  # agents + origin belief exist; decisions truncated

            T = BASE - dt.timedelta(days=300)  # window-4/5 boundary
            before = BASE - dt.timedelta(days=350)  # window 4
            after_a = BASE - dt.timedelta(days=250)  # window 5
            after_b = BASE - dt.timedelta(days=150)  # window 6

            rows = []
            # --- before T (excluded): 4 driven approvals by crimson-6, 1 fraud ---
            rows += [_row(A6, before, fraud=(i == 0)) for i in range(4)]
            # --- exactly at T (excluded — strict >): 1 driven approval, not fraud ---
            rows.append(_row(A7, T, fraud=False))
            # --- after T (included): 6 by crimson-7 (3 fraud) + 3 by crimson-5b (2 fraud) ---
            rows += [_row(A7, after_a, fraud=(i < 3)) for i in range(6)]
            rows += [_row(A5B, after_b, fraud=(i < 2)) for i in range(3)]
            # --- after T but NOT driven (must be ignored): a fraud approval with NULL driver ---
            rows.append(_row(A7, after_a, fraud=True, driven=False))

            async with engine.begin() as c:
                await c.execute(insert(Decision), rows)

            total_driven = 4 + 1 + 6 + 3  # 14 (the NULL-driver row is not driven)

            # --- the counterfactual at T ---
            r = await cf.what_if_invalidated_at(ORIGIN, T)
            assert r is not None
            assert r["total_belief_driven"] == total_driven, r
            assert r["withdrawn_approvals"] == 9, r            # 6 + 3 after T
            assert r["approvals"] == 9, r                      # every affected row is an approve
            assert r["frauds_auto_approved"] == 5, r           # 3 + 2
            assert r["affected_holder_count"] == 2, r          # crimson-7 + crimson-5b
            assert set(r["affected_holders"]) == {A7, A5B}, r

            # per-window breakdown sums to the summary and buckets to belief_performance windows
            assert sum(w["withdrawn_approvals"] for w in r["windows"]) == 9, r["windows"]
            assert sum(w["frauds_auto_approved"] for w in r["windows"]) == 5, r["windows"]

            # --- extreme 1: T before the earliest decision => the full driven set ---
            r_all = await cf.what_if_invalidated_at(ORIGIN, BASE - dt.timedelta(days=800))
            assert r_all["withdrawn_approvals"] == total_driven, r_all
            assert r_all["withdrawn_approvals"] == r_all["total_belief_driven"], r_all
            assert r_all["frauds_auto_approved"] == 1 + 3 + 2, r_all  # before + after_a + after_b

            # --- extreme 2: T after the latest decision => an empty set ---
            r_none = await cf.what_if_invalidated_at(ORIGIN, BASE - dt.timedelta(days=1))
            assert r_none["withdrawn_approvals"] == 0, r_none
            assert r_none["frauds_auto_approved"] == 0, r_none
            assert r_none["affected_holders"] == [], r_none

            # --- unknown belief => None (router maps to 404) ---
            assert await cf.what_if_invalidated_at(uuid.uuid4(), T) is None
        finally:
            await engine.dispose()

    asyncio.run(_run())
