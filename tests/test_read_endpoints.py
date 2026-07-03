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


def _decision_rows(agent_id, n, *, base, merchant, fraud_every=0):
    """n deterministic decisions for `agent_id`, decided_at spaced 1 minute apart."""
    return [
        {
            "id": uuid.uuid4(),
            "agent_id": agent_id,
            "txn_ref": f"feed-{merchant}-{i:03d}",
            "merchant": merchant,
            "amount": 50.0 + i,
            "verdict": "approve",
            "driving_belief_id": ORIGIN,
            "confidence": 0.9,
            "decided_at": base + dt.timedelta(minutes=i),
            "is_fraud": bool(fraud_every) and i % fraud_every == 0,
        }
        for i in range(n)
    ]


def test_list_decisions_feed_filter_and_pagination():
    async def _run():
        try:
            await run_seed()  # leaves decisions empty; we insert a controlled set
            base = dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc)
            crimson_rows = _decision_rows(CRIMSON_7, 30, base=base, merchant="Grocery #7")
            azure_rows = _decision_rows(
                aid("azure-7"), 12, base=base, merchant="Fuel #A7"
            )
            async with engine.begin() as c:
                await c.execute(insert(Decision), crimson_rows + azure_rows)

            async with _client() as client:
                # Fleet-wide feed (no filter): sees both agents, newest first, paginated.
                r_all = await client.get("/decisions", params={"limit": 10})
                # Agent-scoped: only crimson-7's history.
                r_c7 = await client.get(
                    "/decisions", params={"agent_id": str(CRIMSON_7), "limit": 100}
                )
                # Second page of the fleet feed.
                r_page2 = await client.get(
                    "/decisions", params={"limit": 10, "offset": 10}
                )
                # Out-of-range limit is a 422 (guarded by Query bounds), not a 500.
                r_bad = await client.get("/decisions", params={"limit": 5000})

            assert r_all.status_code == 200, r_all.text
            all_body = r_all.json()
            assert all_body["total"] == 42  # 30 + 12
            assert all_body["agent_id"] is None
            assert len(all_body["decisions"]) == 10  # limited page
            # Newest first: decided_at is non-increasing across the page.
            times = [d["decided_at"] for d in all_body["decisions"]]
            assert times == sorted(times, reverse=True), times

            c7_body = r_c7.json()
            assert c7_body["total"] == 30
            assert c7_body["agent_id"] == str(CRIMSON_7)
            assert all(d["agent_id"] == str(CRIMSON_7) for d in c7_body["decisions"])

            # Page 2 is disjoint from page 1 (offset works).
            page1_ids = {d["id"] for d in all_body["decisions"]}
            page2_ids = {d["id"] for d in r_page2.json()["decisions"]}
            assert page1_ids.isdisjoint(page2_ids)

            assert r_bad.status_code == 422, r_bad.text
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_list_beliefs_returns_the_founding_belief():
    async def _run():
        try:
            await run_seed()
            async with _client() as client:
                r = await client.get("/beliefs")
                r_active = await client.get("/beliefs", params={"status": "active"})
                r_invalid = await client.get("/beliefs", params={"status": "invalidated"})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["count"] == 1, body
            belief = body["beliefs"][0]
            assert belief["id"] == str(ORIGIN)
            assert belief["status"] == "active"
            assert belief["originating_agent_id"] == str(CRIMSON_0)
            assert belief["invalidated_at"] is None

            # Status filter partitions cleanly (seed has exactly one active belief).
            assert r_active.json()["count"] == 1
            assert r_invalid.json()["count"] == 0
        finally:
            await engine.dispose()

    asyncio.run(_run())


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
