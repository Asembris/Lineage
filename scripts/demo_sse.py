"""Live SSE demo: watch the eventual-consistency SPLIT window open and close in real time.

Streams GET /demo/consistency/stream and prints each real observer sample as it arrives. The
per-holder fan-out takes several real seconds, so you see ALL_ACTIVE -> SPLIT (a committed,
externally-visible torn closure) -> ALL_INVALIDATED unfold on the wire — the exact window the
atomic CRDB endpoint never exposes.

Run (in-process, no server needed):
    PYTHONPATH=. .venv/Scripts/python.exe scripts/demo_sse.py

The same stream is consumable by any SSE client against a running server, e.g.:
    uvicorn app.main:app
    curl -N http://localhost:8000/demo/consistency/stream
"""

import asyncio
import json
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import httpx  # noqa: E402

from app.db import engine  # noqa: E402
from app.main import app  # noqa: E402

_MARK = {
    "ALL_ACTIVE": "  active   ",
    "SPLIT": ">>SPLIT<<  ",
    "ALL_INVALIDATED": "invalidated",
}


def _emit(event: str, data: dict) -> None:
    if event == "start":
        print(f"\n  stream start — belief {data['belief_id']} ({data['strategy']} strategy)")
        print(f"  {data['note']}\n")
        print(f"  {'seq':>3}  {'state':<12}{'closure':<10}{'t+ms':>7}")
        print("  " + "-" * 34)
    elif event == "sample":
        mark = _MARK.get(data["state"], data["state"])
        closure = f"{data['open_edges']}/{data['total_edges']} open"
        print(f"  {data['seq']:>3}  {mark:<12}{closure:<10}{data['elapsed_ms']:>7}")
    elif event == "summary":
        print("  " + "-" * 34)
        print(
            f"\n  SUMMARY: {data['commit_points']} commit points "
            f"(vs 1 for the atomic endpoint), {data['split_samples']} committed SPLIT reads, "
            f"transition seen={data['saw_transition']}, {data['total_samples']} samples "
            f"over {data['elapsed_ms']} ms.\n"
        )
    elif event == "busy":
        print(f"  busy: {data['detail']}")


async def main() -> None:
    buf: dict = {}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t", timeout=120.0) as c:
        async with c.stream("GET", "/demo/consistency/stream") as r:
            async for chunk in r.aiter_text():
                for line in chunk.split("\n"):
                    line = line.rstrip("\r")
                    if line == "":
                        if "data" in buf:
                            _emit(buf.get("event", "message"), json.loads(buf["data"]))
                        buf.clear()
                    elif line.startswith("event:"):
                        buf["event"] = line[len("event:"):].strip()
                    elif line.startswith("data:"):
                        buf["data"] = line[len("data:"):].strip()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
