"""Demo streaming endpoints (Phase 4 scoped addition).

ONE Server-Sent Events endpoint: it streams the consistency proof's REAL observer samples
live as the eventually-consistent fan-out runs. That fan-out has genuine multi-second timing
(a per-holder delay models real cross-region/namespace propagation), so streaming it is honest
pacing — the split window actually opens and closes in real time on the wire.

Deliberately NOT applied to the lineage trace: that recursive CTE returns in milliseconds, so
streaming it would be fake server-side pacing. The trace stays a single request.

This endpoint MUTATES demo state (it reseeds, then runs the eventual fan-out to invalidation).
A module-level single-flight guard admits only one stream at a time so two reseeds can't
collide on TRUNCATE ... CASCADE (see NOTES Phase 3, Step 7). Every run reseeds first, so it is
repeatable.
"""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from app.services import consistency
from seed.seed import aid, bid
from seed.seed import seed as run_seed

router = APIRouter(tags=["demo"])

ORIGIN = bid("origin")
ACTOR = aid("crimson-7")  # a living holder standing in as the invalidating supervisor

# Single-flight guard: each stream reseeds, and two overlapping TRUNCATE CASCADE reseeds
# collide on CRDB (indexes being dropped). One stream at a time is the right demo model.
# A plain module-level bool, NOT an asyncio.Lock: the test-and-set below is a synchronous
# read-then-write with no await between them, so it is atomic on the single-threaded event
# loop. A near-simultaneous second request therefore ALWAYS sees the flag set and gets a
# clean `busy` — it can never fall through and queue behind the lock (the TOCTOU an
# `if lock.locked(): ... ; async with lock:` pair leaves open).
_stream_active = False


def _sse(event: str, payload: dict) -> dict:
    return {"event": event, "data": json.dumps(payload)}


async def _consistency_events():
    global _stream_active
    # Single-flight test-and-set (see the _stream_active note above). If a stream is already
    # running, tell the client cleanly and stop — never queue behind it, never reseed under it.
    if _stream_active:
        yield _sse("busy", {"detail": "a consistency stream is already running; retry shortly"})
        return
    _stream_active = True

    try:
        await run_seed()

        queue: asyncio.Queue = asyncio.Queue()
        stop = asyncio.Event()
        ready = asyncio.Event()
        start = time.monotonic()

        def on_sample(state: str, total: int, open_edges: int) -> None:
            # Called from the observer's event-loop task; put_nowait is safe (same loop).
            queue.put_nowait((state, total, open_edges, time.monotonic()))

        observer = asyncio.create_task(
            consistency.observe_closure(
                ORIGIN, stop, interval=0.1, ready=ready, on_sample=on_sample
            )
        )
        fanout: asyncio.Task | None = None
        seq = 0
        split_samples = 0

        def _drain_one(item) -> dict:
            nonlocal seq, split_samples
            state, total, open_edges, ts = item
            seq += 1
            if state == consistency.SPLIT:
                split_samples += 1
            return _sse(
                "sample",
                {
                    "seq": seq,
                    "state": state,
                    "open_edges": open_edges,
                    "total_edges": total,
                    "elapsed_ms": round((ts - start) * 1000),
                },
            )

        try:
            await ready.wait()  # observer connected and took its first (ALL_ACTIVE) sample
            yield _sse(
                "start",
                {
                    "belief_id": str(ORIGIN),
                    "strategy": "eventual",
                    "note": "per-holder fan-out to invalidation; watch the SPLIT window open",
                },
            )

            # Kick off the eventually-consistent fan-out concurrently with the observer.
            fanout = asyncio.create_task(
                consistency.eventual_invalidate(ORIGIN, ACTOR, per_holder_delay=0.5)
            )

            # Stream each observer sample as it arrives, until the fan-out is done and drained.
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=0.2)
                except asyncio.TimeoutError:
                    if fanout.done() and queue.empty():
                        break
                    continue
                yield _drain_one(item)

            stop.set()
            ev = await fanout
            result = await observer
            # The observer's post-stop final sample (terminal ALL_INVALIDATED) + any stragglers.
            while not queue.empty():
                yield _drain_one(queue.get_nowait())

            yield _sse(
                "summary",
                {
                    "commit_points": ev.commit_points,  # N edges + the belief row (vs 1 atomic)
                    "split_samples": split_samples,     # committed, externally-visible split reads
                    "saw_transition": result.saw_transition,
                    "total_samples": seq,
                    "elapsed_ms": round((time.monotonic() - start) * 1000),
                },
            )
        finally:
            # Client disconnect (GeneratorExit) or any error: stop the observer and reap tasks.
            stop.set()
            for task in (fanout, observer):
                if task is not None and not task.done():
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass
    finally:
        # Release the single-flight guard so the next stream can run (also on run_seed error
        # or client disconnect before the inner try was entered).
        _stream_active = False


@router.get("/demo/consistency/stream")
async def stream_consistency_proof():
    """SSE stream of the eventual-consistency fan-out's real observer samples.

    Events: `start`, then one `sample` per observed closure read (state = ALL_ACTIVE / SPLIT /
    ALL_INVALIDATED, with open/total edge counts and elapsed ms), then a `summary`. `busy` if a
    stream is already in flight.
    """
    return EventSourceResponse(_consistency_events(), ping=15)
