"""Phase-4 SSE done-test: the consistency stream emits REAL observer samples live.

Consumes GET /demo/consistency/stream as an SSE client and asserts the wire behaviour:
  * a `start` event, then a run of `sample` events, then a `summary`;
  * the samples include the SPLIT window (the eventual fan-out's committed torn state) and
    reach the terminal ALL_INVALIDATED — i.e. the stream carries the real transition, not a
    canned sequence;
  * the summary reports the eventual baseline's N+1 commit points (9) and saw_transition.

Nothing is mocked — it reseeds and drives the real fan-out against the live cluster.
"""

import asyncio
import json

import httpx

from app.db import engine
from app.main import app
from app.services import consistency


def _parse_sse(chunk: str, buf: dict, events: list):
    """Feed raw text; append (event, data-dict) tuples to `events` as frames complete."""
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


def test_sse_streams_real_observer_samples():
    async def _run():
        try:
            events: list = []
            buf: dict = {}
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://t", timeout=120.0
            ) as client:
                async with client.stream("GET", "/demo/consistency/stream") as r:
                    assert r.status_code == 200, r.status_code
                    async for chunk in r.aiter_text():
                        _parse_sse(chunk, buf, events)
                        if any(ev == "summary" for ev, _ in events):
                            break

            kinds = [ev for ev, _ in events]
            assert kinds[0] == "start", kinds
            assert "summary" in kinds, kinds

            samples = [data for ev, data in events if ev == "sample"]
            states = [s["state"] for s in samples]
            assert len(samples) >= 3, states
            assert consistency.SPLIT in states, f"stream must witness the split window: {states}"
            assert consistency.ALL_INVALIDATED in states, states

            summary = next(data for ev, data in events if ev == "summary")
            assert summary["commit_points"] == 9, summary  # 8 edges + belief row
            assert summary["saw_transition"] is True, summary
            assert summary["split_samples"] >= 1, summary
        finally:
            await engine.dispose()

    asyncio.run(_run())
