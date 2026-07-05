"""Done-test for GET /beliefs/{id}/performance — the measured staleness curve read surface.

The frontend Time-travel interaction reads this to show "valid then / rotten now" from REAL
belief_performance rows (never asserted). This test drives the actual HTTP surface (httpx
ASGITransport) and covers the three contracts:

  * unknown belief            -> 404
  * known belief, no windows  -> 200 with an empty list (honest: not-measured != not-found)
  * known belief, measured    -> 200, windows ordered by window_start, first-high/last-low
                                 confidence + rising frauds_approved (a real decay curve)

Hermetic and OpenAI-free: it seeds its own controlled decisions (belief applied uniformly, the
world drifting from low to high fraud), recomputes belief_performance, then reads it back
through the endpoint — the same pure aggregation test_staleness proves is derived, not stored.
"""

import asyncio
import datetime as dt
import uuid

import httpx
from sqlalchemy import insert

from app.db import engine
from app.main import app
from app.models import Decision
from app.services.performance import recompute_belief_performance
from seed.seed import aid, bid
from seed.seed import seed as run_seed

ORIGIN = bid("origin")
AGENT = aid("crimson-7")  # a living holder of the belief

BASE = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)
# Three generation-like windows, oldest first. Fraud rises across them: the belief keeps
# approving the pattern while the world drifts, so measured confidence rots.
WINDOWS = [
    (BASE - dt.timedelta(days=300), BASE - dt.timedelta(days=290)),  # early: healthy
    (BASE - dt.timedelta(days=200), BASE - dt.timedelta(days=190)),  # mid
    (BASE - dt.timedelta(days=100), BASE - dt.timedelta(days=90)),   # late: stale
]
_ATS = [BASE - dt.timedelta(days=d) for d in (295, 195, 95)]
_FRAUDS = [10, 60, 120]  # of 200 belief-driven approvals per window


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def _rows(decided_at, n, frauds):
    """n belief-driven APPROVE decisions, exactly `frauds` of them fraudulent (deterministic)."""
    return [
        {
            "id": uuid.uuid4(),
            "agent_id": AGENT,
            "txn_ref": f"perf-{decided_at.date()}-{i:03d}",
            "merchant": "Grocery Mart #500",
            "amount": 99.00,
            "verdict": "approve",  # the belief asserts "safe" -> approve, uniformly
            "driving_belief_id": ORIGIN,
            "confidence": 0.9,
            "decided_at": decided_at,
            "is_fraud": i < frauds,
        }
        for i in range(n)
    ]


def test_belief_performance_curve_states_and_404():
    async def _run():
        try:
            await run_seed()  # origin belief exists; belief_performance truncated (empty)

            async with _client() as client:
                # Known belief, no measured windows yet -> 200 with an empty list, NOT 404.
                r_empty = await client.get(f"/beliefs/{ORIGIN}/performance")
                # Unknown belief -> 404.
                r_404 = await client.get(f"/beliefs/{uuid.uuid4()}/performance")

            assert r_empty.status_code == 200, r_empty.text
            empty_body = r_empty.json()
            assert empty_body["belief_id"] == str(ORIGIN)
            assert empty_body["count"] == 0
            assert empty_body["windows"] == []

            assert r_404.status_code == 404, r_404.text

            # Seed a controlled, drifting world and MEASURE it into belief_performance.
            async with engine.begin() as c:
                for (start, _end), at, frauds in zip(WINDOWS, _ATS, _FRAUDS):
                    await c.execute(insert(Decision), _rows(at, 200, frauds))
            await recompute_belief_performance(ORIGIN, WINDOWS)

            async with _client() as client:
                r = await client.get(f"/beliefs/{ORIGIN}/performance")

            assert r.status_code == 200, r.text
            body = r.json()
            assert body["belief_id"] == str(ORIGIN)
            assert body["count"] == 3, body
            wins = body["windows"]

            # Ordered by window_start (ascending) — ordinal position IS generation order.
            starts = [w["window_start"] for w in wins]
            assert starts == sorted(starts), starts

            first, last = wins[0], wins[-1]
            # Valid then / rotten now — derived from the rows, first high, last low.
            assert first["confidence"] > 0.9, first          # healthy (--alive)
            assert last["confidence"] < 0.5, last            # stale (--alert)
            # Confidence strictly decays across the curve; frauds_approved climbs.
            confs = [w["confidence"] for w in wins]
            assert confs == sorted(confs, reverse=True), confs
            assert [w["frauds_approved"] for w in wins] == _FRAUDS, wins
            # This belief only ever approves -> no false positives, honestly 0 everywhere.
            assert all(w["false_positive_rate"] == 0.0 for w in wins), wins
        finally:
            await engine.dispose()

    asyncio.run(_run())
