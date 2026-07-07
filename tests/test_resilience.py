"""Hermetic tests for app/resilience.py — the bounded retry/backoff used at the two demo
surfaces (invalidation endpoint + SSE reseed). No cluster access: the classifier and the retry
loop are pure, and the endpoint 503-mapping is exercised with a patched invalidate_belief.
"""

import asyncio
import uuid

import httpx

from app import resilience
from app.main import app
from app.routers import beliefs as beliefs_router


class _FakeDBError(Exception):
    """Stand-in for a DBAPI error carrying a SQLSTATE (psycopg exposes .sqlstate)."""

    def __init__(self, msg: str, sqlstate: str | None = None):
        super().__init__(msg)
        self.sqlstate = sqlstate


def test_classifier_recognises_transients_and_rejects_deterministic_errors():
    # SQLSTATE-based
    assert resilience.is_transient(_FakeDBError("boom", "40001"))       # serialization
    assert resilience.is_transient(_FakeDBError("boom", "08006"))       # connection failure
    assert resilience.is_transient(_FakeDBError("boom", "57P01"))       # admin shutdown
    # substring-based (no sqlstate attached)
    assert resilience.is_transient(_FakeDBError("please restart transaction: RETRY_SERIALIZABLE"))
    assert resilience.is_transient(
        _FakeDBError("cannot perform TRUNCATE on decisions which has indexes being dropped")
    )
    assert resilience.is_transient(_FakeDBError("server closed the connection unexpectedly"))
    # a transient wrapped as __cause__ is still found through the chain
    outer = RuntimeError("wrapped")
    outer.__cause__ = _FakeDBError("x", "40001")
    assert resilience.is_transient(outer)
    # deterministic failures are NOT transient
    assert not resilience.is_transient(_FakeDBError("null value in column", "23502"))
    assert not resilience.is_transient(ValueError("bad input"))


def test_retry_recovers_after_transient_failures():
    async def _run():
        calls = {"n": 0}

        async def factory():
            calls["n"] += 1
            if calls["n"] < 3:
                raise _FakeDBError("transient", "40001")
            return "ok"

        # base_delay 0 keeps the test instant; must succeed on the 3rd attempt.
        out = await resilience.run_with_retry(factory, attempts=3, base_delay=0.0)
        assert out == "ok"
        assert calls["n"] == 3

    asyncio.run(_run())


def test_retry_exhaustion_raises_transient_retry_exhausted():
    async def _run():
        calls = {"n": 0}

        async def factory():
            calls["n"] += 1
            raise _FakeDBError("always transient", "40001")

        raised = None
        try:
            await resilience.run_with_retry(factory, attempts=3, base_delay=0.0)
        except resilience.TransientRetryExhausted as e:
            raised = e
        assert raised is not None, "must raise TransientRetryExhausted past the budget"
        assert isinstance(raised.__cause__, _FakeDBError)  # last error preserved
        assert calls["n"] == 3, "must stop at the bounded attempt count"

    asyncio.run(_run())


def test_retry_does_not_retry_deterministic_errors():
    async def _run():
        calls = {"n": 0}

        async def factory():
            calls["n"] += 1
            raise ValueError("deterministic")

        raised = None
        try:
            await resilience.run_with_retry(factory, attempts=5, base_delay=0.0)
        except ValueError as e:
            raised = e
        assert raised is not None
        assert calls["n"] == 1, "a non-transient error must propagate immediately, no retries"

    asyncio.run(_run())


def _invalidate(client, actor=None):
    body = {"actor_id": str(actor or uuid.uuid4())}
    return client.post(f"/beliefs/{uuid.uuid4()}/invalidate", json=body)


def test_invalidate_endpoint_maps_transient_exhaustion_to_503(monkeypatch):
    """A persistent transient in the atomic txn surfaces as a clean 503, never a 500."""

    async def _run():
        async def always_transient(belief_id, actor_id, **kw):
            raise _FakeDBError("please restart transaction: RETRY_SERIALIZABLE", "40001")

        monkeypatch.setattr(
            beliefs_router.invalidation, "invalidate_belief", always_transient
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            r = await _invalidate(client)
        assert r.status_code == 503, (r.status_code, r.text)

    asyncio.run(_run())


def test_invalidate_endpoint_does_not_retry_belief_not_found(monkeypatch):
    """A deterministic 404 must not be retried into a 503/hang."""

    async def _run():
        calls = {"n": 0}

        async def not_found(belief_id, actor_id, **kw):
            calls["n"] += 1
            raise beliefs_router.invalidation.BeliefNotFound(str(belief_id))

        monkeypatch.setattr(beliefs_router.invalidation, "invalidate_belief", not_found)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            r = await _invalidate(client)
        assert r.status_code == 404, (r.status_code, r.text)
        assert calls["n"] == 1, "BeliefNotFound is deterministic — must not retry"

    asyncio.run(_run())
