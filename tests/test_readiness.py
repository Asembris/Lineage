"""Readiness probe done-test: /health/ready reflects REAL DB state, not a constant.

This is the J4 (Production Readiness) fix for the false-green class: /health returns a
constant and touches no database, so it reports healthy even when the cluster is unreachable.
/health/ready runs a real SELECT 1 (2s bounded) and MUST be able to return 503.

The acceptance bar is VACUITY: the 503 test must be capable of going red. A readiness check
hardcoded to 200 would pass a naive test — so `test_readiness_503_when_db_unreachable` is the
one with teeth. Stub the handler to a constant 200 and this test flips to failing (gets 200,
asserts 503); against the real check it passes. See NOTES.md "J4 — readiness".

State-independent by construction: SELECT 1 reads no table, so the green test needs NO seed,
writes NOTHING, and passes against ANY reachable cluster (not coupled to 5,500/8/0). The red
test injects a broken engine via dependency override and never touches the real cluster; the
override is cleared in `finally` so a failed assertion cannot leak it into another test.
"""

import asyncio

import httpx
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import engine, get_engine
from app.main import app


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    )


def test_readiness_200_when_db_reachable():
    """Green path: real cluster reachable -> 200 {"status":"ready","db":"ok"}.

    No seed, no writes — adds zero cluster-dirtying. A SELECT 1 succeeds against any reachable
    cluster regardless of what rows it holds.
    """

    async def _run():
        try:
            async with _client() as client:
                r = await client.get("/health/ready")
            assert r.status_code == 200, r.text
            assert r.json() == {"status": "ready", "db": "ok"}
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_readiness_503_when_db_unreachable():
    """Red path (the test with teeth): DB unreachable -> 503 {"status":"not_ready",...}.

    Injects a deliberately-broken engine (127.0.0.1:1 -> connection refused, connect_timeout 1)
    through the get_engine dependency. This NEVER touches the real cluster. The override is
    cleared and the broken engine disposed in `finally`, so even a failed assertion cannot leak
    the override into a later test. This is the assertion a constant-200 handler cannot satisfy.
    """

    async def _run():
        broken = create_async_engine(
            "cockroachdb+psycopg://u:p@127.0.0.1:1/defaultdb",
            connect_args={"connect_timeout": 1},
        )
        app.dependency_overrides[get_engine] = lambda: broken
        try:
            async with _client() as client:
                r = await client.get("/health/ready")
            assert r.status_code == 503, r.text
            assert r.json() == {"status": "not_ready", "db": "unreachable"}
        finally:
            app.dependency_overrides.pop(get_engine, None)
            await broken.dispose()

    asyncio.run(_run())
