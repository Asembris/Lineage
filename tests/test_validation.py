"""Lean Phase-4 done-tests: input validation + rate limiting.

The bar is "nothing 500s embarrassingly in a live demo" — every case below is something a
judge could trivially trigger that previously crashed or wrote a dangling actor:

  1. NIL ACTOR — POST /invalidate with the all-zeros UUID is rejected 422 (a well-formed but
     null identifier must not produce a dangling audit row). Real non-fleet supervisor ids
     are still accepted — we do NOT validate actor_id against the agents table.
  2. AOST OUT OF WINDOW — GET /agents/{id}/beliefs with a timestamp older than the GC TTL (or
     in the future) returns 400, not 500 (CRDB rejects it inside SET TRANSACTION AS OF ...).
  3. RATE LIMIT — the hand-rolled per-IP/per-route limiter is concurrency-safe (no lost
     updates under a concurrent burst) and the middleware returns 429 past the window budget.
"""

import asyncio
import datetime as dt
import uuid

import httpx

from app.db import engine
from app.main import app
from app.ratelimit import RateLimiter
from seed.seed import aid, bid
from seed.seed import seed as run_seed

ORIGIN = bid("origin")
CRIMSON_7 = aid("crimson-7")
NIL = uuid.UUID(int=0)


def test_nil_actor_is_rejected_422():
    async def _run():
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                r = await client.post(
                    f"/beliefs/{ORIGIN}/invalidate", json={"actor_id": str(NIL)}
                )
            assert r.status_code == 422, r.text
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_arbitrary_nonfleet_actor_is_accepted():
    """A real human supervisor is NOT a fleet agent — a well-formed random UUID is allowed
    through validation (it reaches the handler, not blocked at the DTO)."""
    async def _run():
        try:
            await run_seed()
            supervisor = uuid.uuid4()  # not in the agents table, and that's correct
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                r = await client.post(
                    f"/beliefs/{ORIGIN}/invalidate", json={"actor_id": str(supervisor)}
                )
            # Not a 422/400 — the invalidation proceeds (200) with the human actor recorded.
            assert r.status_code == 200, r.text
            assert r.json()["actor_id"] == str(supervisor)
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_aost_outside_window_is_400_not_500():
    async def _run():
        try:
            await run_seed()
            # 2 days ago: well before the row existed and far outside the GC TTL (75 min).
            old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)).isoformat()
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                r = await client.get(
                    f"/agents/{CRIMSON_7}/beliefs", params={"as_of": old}
                )
            assert r.status_code == 400, r.text
            assert "time-travel window" in r.json()["detail"]
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_aost_future_is_400_not_500():
    async def _run():
        try:
            await run_seed()
            future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)).isoformat()
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                r = await client.get(
                    f"/agents/{CRIMSON_7}/beliefs", params={"as_of": future}
                )
            assert r.status_code == 400, r.text
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_limiter_is_concurrency_safe_under_a_burst():
    """200 concurrent checks against a budget of 100 must allow EXACTLY 100 — a naive
    unlocked counter would lose updates and over-admit."""
    async def _run():
        limiter = RateLimiter(max_requests=100, window_seconds=60.0)
        results = await asyncio.gather(
            *[limiter.check("1.2.3.4", "/x") for _ in range(200)]
        )
        allowed = sum(1 for ok, _ in results if ok)
        assert allowed == 100, f"expected exactly 100 admitted, got {allowed}"

    asyncio.run(_run())


def test_middleware_returns_429_past_the_window_budget():
    async def _run():
        probe = "/__ratelimit_probe__"  # bogus path: 404 in the app, so this is DB-free
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            statuses = [
                (await client.get(probe)).status_code for _ in range(61)
            ]
        # The default window budget is 60. First 60 are routed (404); the 61st trips 429.
        assert 429 not in statuses[:60], statuses[:60]
        assert statuses[60] == 429, statuses

    asyncio.run(_run())
