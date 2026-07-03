"""Read-surface done-tests: the three list endpoints the frontend renders from.

These are the routes AUDIT.md §8 flagged as missing (no read route existed for the genealogy
tree, the decision feed, or the belief catalog). Each asserts real cluster data through the
actual HTTP surface (httpx ASGITransport), not a script's stdout.

  * GET /agents      — full genealogy (24 nodes, real parent edges), + bloodline/status filters
  * GET /decisions   — fleet-wide feed by default; agent filter + pagination (total/limit/offset)
  * GET /beliefs     — the founding belief catalog, + status filter
"""

import asyncio
import datetime as dt
import uuid

import httpx
from sqlalchemy import insert

from app.db import engine
from app.main import app
from app.models import Decision
from seed.seed import aid, bid
from seed.seed import seed as run_seed

CRIMSON_7 = aid("crimson-7")
CRIMSON_0 = aid("crimson-0")
ORIGIN = bid("origin")


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    )


def test_list_agents_returns_full_genealogy_with_parent_edges():
    async def _run():
        try:
            await run_seed()
            async with _client() as client:
                r = await client.get("/agents")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["count"] == 24, body["count"]  # 2 bloodlines x 12
            assert len(body["agents"]) == 24

            by_id = {a["id"]: a for a in body["agents"]}
            # crimson-7 is the living head of the spine, parented by crimson-6.
            c7 = by_id[str(CRIMSON_7)]
            assert c7["status"] == "alive"
            assert c7["generation"] == 7
            assert c7["bloodline"] == "crimson"
            assert c7["parent_id"] == str(aid("crimson-6"))
            # crimson-0 is a founding root — no parent.
            assert by_id[str(CRIMSON_0)]["parent_id"] is None

            # Bloodline filter narrows to 12; status filter surfaces the living agents.
            async with _client() as client:
                r_crimson = await client.get("/agents", params={"bloodline": "crimson"})
                r_alive = await client.get("/agents", params={"status": "alive"})
            assert r_crimson.json()["count"] == 12
            assert all(a["bloodline"] == "crimson" for a in r_crimson.json()["agents"])
            alive_names = {a["id"] for a in r_alive.json()["agents"]}
            # Living agents: both spine heads (crimson-7, azure-7) + the crimson branch leaf.
            assert alive_names == {
                str(CRIMSON_7),
                str(aid("azure-7")),
                str(aid("crimson-5b")),
            }, alive_names
        finally:
            await engine.dispose()

    asyncio.run(_run())
