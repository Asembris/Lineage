"""Lineage endpoint: the trace resolves end-to-end, including the branch."""

import asyncio
import uuid

from httpx import ASGITransport, AsyncClient

from app.db import engine
from app.main import app
from seed.seed import aid, bid, seed as run_seed


def test_lineage_resolves_full_spine_and_branch():
    async def _run():
        try:
            await run_seed()
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get(f"/beliefs/{bid('origin')}/lineage")
                assert r.status_code == 200, r.text
                data = r.json()

                # belief + origin resolve to real nodes (no dangling refs)
                assert data["origin_agent_id"] == str(aid("crimson-0"))
                assert data["belief"]["originating_agent_id"] == str(aid("crimson-0"))

                path = data["path"]
                assert len(path) == 9, "8-node spine + 1 branch node expected"

                by_id = {p["agent_id"]: p for p in path}

                # depth-0 root is the originator, with no inbound edge
                root = path[0]
                assert root["depth"] == 0
                assert root["agent_id"] == str(aid("crimson-0"))
                assert root["from_agent_id"] is None
                assert root["inherited_at"] is None

                # full spine generations 0..7 present
                assert {p["generation"] for p in path} == {0, 1, 2, 3, 4, 5, 6, 7}

                # living end-of-spine holder
                c7 = by_id[str(aid("crimson-7"))]
                assert c7["generation"] == 7 and c7["status"] == "alive"

                # BRANCH: crimson-5b inherited from crimson-4 (proves recursive CTE branching)
                b5 = by_id[str(aid("crimson-5b"))]
                assert b5["from_agent_id"] == str(aid("crimson-4"))
                assert b5["generation"] == 5 and b5["status"] == "alive"

                # non-inheriting sibling must NOT appear in the belief's lineage
                assert str(aid("crimson-2b")) not in by_id

                # unknown belief -> 404
                r404 = await client.get(f"/beliefs/{uuid.uuid4()}/lineage")
                assert r404.status_code == 404
        finally:
            await engine.dispose()

    asyncio.run(_run())
