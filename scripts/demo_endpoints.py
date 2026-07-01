"""Reseed to a clean state, then hit both heart endpoints and print real JSON.

Run: PYTHONPATH=. .venv/Scripts/python.exe scripts/demo_endpoints.py
"""

import asyncio
import datetime as dt
import json
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from httpx import ASGITransport, AsyncClient

from app.db import engine
from app.main import app
from seed.seed import aid, bid, seed as run_seed


def show(title, resp):
    print(f"\n### {title}  ->  HTTP {resp.status_code}")
    print(json.dumps(resp.json(), indent=2, default=str))


async def main():
    await run_seed()  # restore clean seeded state
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        c7 = str(aid("crimson-7"))
        origin = str(bid("origin"))

        show(
            f"GET /agents/{c7}/beliefs   (current state)",
            await client.get(f"/agents/{c7}/beliefs"),
        )

        # ISO-8601 as_of form (proves the non-HLC path). 'now' minus a couple seconds.
        iso = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=2)).isoformat()
        show(
            f"GET /agents/{c7}/beliefs?as_of={iso}   (ISO-8601 AOST)",
            await client.get(f"/agents/{c7}/beliefs", params={"as_of": iso}),
        )

        show(
            f"GET /beliefs/{origin}/lineage",
            await client.get(f"/beliefs/{origin}/lineage"),
        )

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
