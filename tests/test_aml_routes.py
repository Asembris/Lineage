"""HTTP-level tests for the AML interrogation routes (Roadmap Item 5).

Distinct from tests/test_aml_interrogate.py ON PURPOSE. That file proves the SERVICE returns
None for an unknown transaction and raises ValueError for an out-of-window `as_of`. It proves
nothing about whether the ROUTER turns those into a 404 and a 400 — that translation is router
code, and an untested translation is how a ValueError becomes an opaque 500 in front of a judge.
These tests drive the real FastAPI app through httpx.ASGITransport (the idiom
tests/test_validation.py established) and assert real status codes and payload shape.

Also asserted here, because it is a route-level property no service test can see: the /aml
surface exposes GET and nothing else, and no route reaches the paid OpenAI path.

CI-SAFE / NON-DESTRUCTIVE: no OpenAI, read-only, never calls run_seed.
"""

import ast
import asyncio
import sys
import uuid
from pathlib import Path

import httpx

from app.main import app

# Real rows from the live extract (deterministic uuid5 ids; see tests/test_aml_interrogate.py).
CYCLE_SUBJECT = "045adfd2-a822-566f-9cd2-6a17fc150539"
BENIGN_MULTI = "3cda6d1d-f765-5001-9342-0478b1a92232"
UNKNOWN = "00000000-0000-0000-0000-000000000000"


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def test_get_transaction_returns_200_and_a_real_row():
    async def _run():
        async with _client() as c:
            r = await c.get(f"/aml/transactions/{CYCLE_SUBJECT}")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == CYCLE_SUBJECT
        assert body["payment_format"]  # a real column, not an empty envelope
        assert body["amount_paid"] > 0
        assert body["from_account_id"] != body["to_account_id"]
        # The answer key is never served over HTTP.
        assert "is_laundering" not in body

    asyncio.run(_run())


def test_interrogate_returns_200_with_the_full_payload_shape():
    async def _run():
        async with _client() as c:
            r = await c.get(f"/aml/transactions/{BENIGN_MULTI}/interrogate")
        assert r.status_code == 200
        body = r.json()

        assert body["transaction_id"] == BENIGN_MULTI
        assert body["subject"]["id"] == BENIGN_MULTI
        assert body["as_of"] is None

        # All four typologies are reported, every one carrying its own structural verdict.
        assert len(body["witnesses"]) == 4
        by_typ = {w["typology"]: w for w in body["witnesses"]}
        assert set(by_typ) == {"CYCLE", "GATHER-SCATTER", "SCATTER-GATHER", "STACK"}

        # The conflict this row really carries (benign by the oracle; three witnesses).
        assert body["has_competing_structure"] is True
        assert body["competing_typologies"] == ["CYCLE", "GATHER-SCATTER", "STACK"]

        ring = by_typ["CYCLE"]
        assert ring["outcome"] == "MATCH"
        assert ring["kind"] == "RING"
        assert ring["flag_capable"] is True
        assert ring["transaction_ids"][0] == BENIGN_MULTI  # the walk starts at the subject

        # Every id the witnesses cite resolves to a real row in the same payload — the whole
        # point of the surface (a client renders the witness with no per-id round-trip).
        for tid in ring["transaction_ids"]:
            assert tid in body["transactions"]
        for txn in body["transactions"].values():
            assert txn["from_account_id"] in body["accounts"]
            assert txn["to_account_id"] in body["accounts"]
            assert "is_laundering" not in txn
        for acct in body["accounts"].values():
            assert acct["bank"] and acct["account"]

    asyncio.run(_run())


def test_unknown_transaction_is_404_on_both_routes():
    """The service returns None; the router must turn that into a 404, not a 500."""

    async def _run():
        async with _client() as c:
            a = await c.get(f"/aml/transactions/{UNKNOWN}")
            b = await c.get(f"/aml/transactions/{UNKNOWN}/interrogate")
        for r in (a, b):
            assert r.status_code == 404, r.text
            assert "not in the AML evidence layer" in r.json()["detail"]

    asyncio.run(_run())


def test_out_of_window_as_of_is_400_not_500():
    """A well-formed timestamp older than the GC TTL (~75 min) fails INSIDE CockroachDB at
    SET TRANSACTION AS OF SYSTEM TIME. The service raises ValueError; the router must translate
    it. Same contract as the deposition and replay surfaces."""

    async def _run():
        async with _client() as c:
            r = await c.get(f"/aml/transactions/{CYCLE_SUBJECT}/interrogate?as_of=2020-01-01T00:00:00Z")
        assert r.status_code == 400, r.text
        assert "outside the time-travel window" in r.json()["detail"]

    asyncio.run(_run())


def test_malformed_as_of_is_400_not_500():
    async def _run():
        async with _client() as c:
            r = await c.get(f"/aml/transactions/{CYCLE_SUBJECT}/interrogate?as_of=banana")
        assert r.status_code == 400, r.text
        assert "expected ISO-8601 or HLC decimal" in r.json()["detail"]

    asyncio.run(_run())


def test_a_malformed_transaction_id_is_422_not_500():
    """FastAPI's uuid.UUID path coercion rejects it before any query runs."""

    async def _run():
        async with _client() as c:
            r = await c.get("/aml/transactions/not-a-uuid/interrogate")
        assert r.status_code == 422

    asyncio.run(_run())


def test_a_within_window_as_of_replays_over_http():
    """Real AOST through the route: the evidence layer is static, so a within-window replay
    reproduces the present interrogation byte-for-byte."""

    async def _run():
        from sqlalchemy import text

        from app.db import engine

        async with engine.connect() as conn:
            hlc = (await conn.execute(text("SELECT cluster_logical_timestamp()"))).scalar_one()

        async with _client() as c:
            now = await c.get(f"/aml/transactions/{BENIGN_MULTI}/interrogate")
            past = await c.get(f"/aml/transactions/{BENIGN_MULTI}/interrogate?as_of={hlc}")

        assert now.status_code == past.status_code == 200
        assert past.json()["as_of"] == str(hlc)
        assert past.json()["competing_typologies"] == now.json()["competing_typologies"]
        assert [w["transaction_ids"] for w in past.json()["witnesses"]] == [
            w["transaction_ids"] for w in now.json()["witnesses"]
        ]

    asyncio.run(_run())


def test_the_aml_surface_is_read_only_and_never_reaches_the_paid_llm_path():
    """A route-level property no service test can see.

    (a) Every /aml route is a GET. `aml_*` is static reference data; nothing is written by a
        request.
    (b) No /aml route reaches aml_agent.evaluate_transaction(), which makes a paid OpenAI call.
        A paid, non-deterministic call behind a GET is a separate decision (NOTES.md Item 5);
        this test is what stops it being made by accident.
    """
    aml_routes = [r for r in app.routes if getattr(r, "path", "").startswith("/aml")]
    assert aml_routes, "the /aml routes are not registered on the app"

    for r in aml_routes:
        assert r.methods <= {"GET", "HEAD"}, f"{r.path} exposes {r.methods}"

    # The router's entire import surface, asserted rather than eyeballed.
    import app.routers.aml as aml_router

    assert not hasattr(aml_router, "aml_agent")
    assert not hasattr(aml_router, "evaluate_transaction")

    import app.services.aml_interrogate as svc

    assert not hasattr(svc, "get_openai")
    assert not hasattr(svc, "embed_text")


def test_no_route_anywhere_on_the_app_reaches_the_paid_llm_verdict_path():
    """The tripwire above only ever looked at routes whose path starts with '/aml'.

    That left the hole exactly where the mistake is most tempting: Item 6 considered feeding a
    real FLAG verdict into POST /beliefs/{id}/invalidate as 'grounded evidence'. That route is
    not an /aml route, so nothing in the suite would have caught a paid, non-deterministic
    OpenAI call being wired onto the one governed write. (The idea was rejected on separate
    grounds — no relation links a belief to an AML transaction — but the guard should not have
    depended on that.)

    Asserted statically over the whole `app` package, so it holds for every route on every
    router, on every branch, without executing any of them: `evaluate_transaction` has exactly
    ZERO callers inside the application. It stays a callable, invoked deliberately by scripts
    and tests. See NOTES.md "Roadmap Item 5" / "Roadmap Item 6".
    """
    app_root = Path(__file__).resolve().parents[1] / "app"
    owner = app_root / "services" / "aml_agent.py"  # where it is DEFINED
    offenders: list[str] = []

    for path in sorted(app_root.rglob("*.py")):
        if path == owner:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            hit = (
                (isinstance(node, ast.Name) and node.id == "evaluate_transaction")
                or (isinstance(node, ast.Attribute) and node.attr == "evaluate_transaction")
                or (
                    isinstance(node, ast.ImportFrom)
                    and (node.module or "").endswith("aml_agent")
                )
                or (
                    isinstance(node, ast.Import)
                    and any(a.name.endswith("aml_agent") for a in node.names)
                )
            )
            if hit:
                offenders.append(f"{path.relative_to(app_root.parent)}:{node.lineno}")

    assert not offenders, (
        "the paid OpenAI verdict path is reachable from application code: "
        + ", ".join(offenders)
    )

    # And no route on the app — not just /aml — carries it in its module namespace.
    for module in {r.endpoint.__module__ for r in app.routes if hasattr(r, "endpoint")}:
        mod = sys.modules[module]
        assert not hasattr(mod, "aml_agent"), f"{module} imported aml_agent"
        assert not hasattr(mod, "evaluate_transaction"), f"{module} imported evaluate_transaction"


def test_unknown_transaction_never_reaches_the_llm_even_on_the_error_path():
    """Regression guard for the obvious future mistake: 'just fall back to the agent on a miss'."""

    async def _run():
        import app.services.aml_agent as agent

        called = []
        original = agent.evaluate_transaction

        async def _tripwire(*a, **k):
            called.append(a)
            return await original(*a, **k)

        agent.evaluate_transaction = _tripwire
        try:
            async with _client() as c:
                await c.get(f"/aml/transactions/{UNKNOWN}/interrogate")
                await c.get(f"/aml/transactions/{CYCLE_SUBJECT}/interrogate")
        finally:
            agent.evaluate_transaction = original
        assert called == [], "an /aml route invoked the paid LLM verdict path"

    asyncio.run(_run())
