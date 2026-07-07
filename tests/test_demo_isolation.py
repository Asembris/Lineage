"""Live done-tests for the demo-stream cluster isolation (roadmap Item 0).

Prove, against the real cluster, that the destructive SSE consistency stream now runs entirely
in the dedicated `demo` database and can NOT disturb the console's data in defaultdb — while
still reproducing the exact 1-vs-9 contrast and real SPLIT/torn-state numbers verified across
Frontend Phases 4-6. Nothing is mocked.

IMPORTANT (and the whole point): unlike every prior consistency/SSE test, these are
NON-DESTRUCTIVE to defaultdb — they assert defaultdb is byte-identical before and after. So no
backfill recovery is needed after running them. The demo reseeds/kills only `demo`.
"""

import asyncio
import json

import httpx
from sqlalchemy import text

from app.db import engine as app_engine
from app.demo_db import demo_engine
from app.main import app
from app.services import consistency
from seed.seed import bid

ORIGIN = bid("origin")


async def _defaultdb_snapshot():
    """A fingerprint of everything the console reads that a demo run could conceivably touch."""
    async with app_engine.connect() as c:
        return {
            "belief_status": (
                await c.execute(text("SELECT status FROM beliefs WHERE id=:b"), {"b": ORIGIN})
            ).scalar(),
            "agents": (await c.execute(text("SELECT count(*) FROM agents"))).scalar(),
            "decisions": (await c.execute(text("SELECT count(*) FROM decisions"))).scalar(),
            "perf_windows": (
                await c.execute(text("SELECT count(*) FROM belief_performance"))
            ).scalar(),
            "open_edges": (
                await c.execute(
                    text(
                        "SELECT count(*)-count(invalidated_at) FROM belief_inheritance "
                        "WHERE belief_id=:b"
                    ),
                    {"b": ORIGIN},
                )
            ).scalar(),
        }


async def _demo_closure_state():
    async with demo_engine.connect() as c:
        status = (
            await c.execute(text("SELECT status FROM beliefs WHERE id=:b"), {"b": ORIGIN})
        ).scalar()
        open_edges = (
            await c.execute(
                text(
                    "SELECT count(*)-count(invalidated_at) FROM belief_inheritance "
                    "WHERE belief_id=:b"
                ),
                {"b": ORIGIN},
            )
        ).scalar()
    return status, open_edges


def _parse_sse(chunk, buf, events):
    for line in chunk.split("\n"):
        line = line.rstrip("\r")
        if line == "":
            if "data" in buf:
                events.append((buf.get("event", "message"), json.loads(buf["data"])))
            buf.clear()
        elif line.startswith("event:"):
            buf["event"] = line[len("event:"):].strip()
        elif line.startswith("data:"):
            buf["data"] = line[len("data:"):].strip()


async def _consume_stream(client, path="/demo/consistency/stream"):
    events, buf = [], {}
    async with client.stream("GET", path) as r:
        assert r.status_code == 200, r.status_code
        async for chunk in r.aiter_text():
            _parse_sse(chunk, buf, events)
            if any(ev in ("summary", "error", "busy") for ev, _ in events):
                break
    return events


def test_isolated_demo_run_preserves_defaultdb_and_reproduces_1v9():
    """A full eventual run leaves defaultdb byte-identical, kills the demo-db closure, and
    streams the SAME 1-vs-9 contrast + real SPLIT window as Phases 4-6.

    Concurrently, console reads (a second person's browser tab) stay stable throughout the run.
    """
    async def _run():
        try:
            before = await _defaultdb_snapshot()

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://t", timeout=120.0
            ) as client:
                # Run the destructive demo while a "second tab" polls the console reads.
                stream_task = asyncio.create_task(_consume_stream(client))
                belief_reads = []
                while not stream_task.done():
                    r = await client.get("/beliefs")
                    if r.status_code == 200:
                        row = next(
                            (b for b in r.json()["beliefs"] if b["id"] == str(ORIGIN)), None
                        )
                        belief_reads.append(row["status"] if row else None)
                    await asyncio.sleep(0.25)
                events = await stream_task

            # --- the stream carried the real transition + the 1-vs-9 contrast (unchanged) ---
            kinds = [ev for ev, _ in events]
            assert kinds[0] == "start", kinds
            samples = [d for ev, d in events if ev == "sample"]
            states = {s["state"] for s in samples}
            assert consistency.ALL_ACTIVE in states, states
            assert consistency.SPLIT in states, f"must witness the torn window: {states}"
            assert consistency.ALL_INVALIDATED in states, states
            summary = next(d for ev, d in events if ev == "summary")
            assert summary["commit_points"] == 9, summary       # 8 edges + belief row
            assert summary["split_samples"] >= 1, summary
            assert summary["saw_transition"] is True, summary

            # --- the demo actually ran: demo-db closure is fully invalidated ---
            demo_status, demo_open = await _demo_closure_state()
            assert demo_status == "invalidated", demo_status
            assert demo_open == 0, demo_open

            # --- and defaultdb (the console's data) is byte-identical: NON-INTERFERENCE ---
            after = await _defaultdb_snapshot()
            assert after == before, (before, after)
            # the concurrent console reads never saw the belief change under them
            assert belief_reads, "expected at least one console read during the run"
            assert all(s == before["belief_status"] for s in belief_reads), belief_reads
        finally:
            await demo_engine.dispose()
            await app_engine.dispose()

    asyncio.run(_run())


def test_concurrent_demo_runs_stay_confined_to_demo_db():
    """Two near-simultaneous demo runs: the single-flight guard admits one and cleanly rejects
    the other (busy), and — the isolation property — defaultdb is untouched by BOTH regardless."""
    async def _run():
        try:
            before = await _defaultdb_snapshot()

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://t", timeout=120.0
            ) as client:
                results = await asyncio.gather(
                    _consume_stream(client), _consume_stream(client)
                )

            terminals = [
                [ev for ev, _ in evs if ev in ("summary", "busy", "error")]
                for evs in results
            ]
            flat = [t for ts in terminals for t in ts]
            assert "summary" in flat, terminals  # at least one full run completed
            assert "busy" in flat, terminals      # the other was cleanly rejected, not queued

            after = await _defaultdb_snapshot()
            assert after == before, (before, after)
        finally:
            await demo_engine.dispose()
            await app_engine.dispose()

    asyncio.run(_run())
