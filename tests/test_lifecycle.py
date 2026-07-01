"""Lifecycle: spawning a child creates REAL inheritance edges for every held belief.

Proves the spawn -> inherit -> retire mechanism writes genuine provenance (no dangling
refs) and that the new child immediately resolves in the belief's lineage — so the Phase-1
lineage CTE keeps working with zero changes.
"""

import asyncio

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.db import engine
from app.main import app
from app.services.lifecycle import spawn_child
from seed.seed import aid, bid, seed as run_seed

ORIGIN = bid("origin")
PARENT = aid("crimson-7")  # gen 7, alive, holds the inherited founding belief


def test_spawn_inherits_active_beliefs():
    async def _run():
        try:
            await run_seed()

            # crimson-7's active held beliefs before spawning (should be exactly the origin).
            async with engine.connect() as c:
                held_before = set(
                    (
                        await c.execute(
                            text(
                                "SELECT DISTINCT b.id FROM beliefs b "
                                "LEFT JOIN belief_inheritance bi "
                                "  ON bi.belief_id=b.id AND bi.to_agent_id=:a "
                                "WHERE b.status='active' "
                                "  AND (b.originating_agent_id=:a OR bi.to_agent_id=:a)"
                            ),
                            {"a": PARENT},
                        )
                    ).scalars().all()
                )
            assert held_before == {ORIGIN}, held_before

            result = await spawn_child(PARENT)
            child = result["child_id"]
            assert result["generation"] == 8
            assert result["bloodline"] == "crimson"
            assert set(result["inherited_belief_ids"]) == held_before

            async with engine.connect() as c:
                # one real inheritance edge parent -> child per held belief
                edges = (
                    await c.execute(
                        text(
                            "SELECT belief_id FROM belief_inheritance "
                            "WHERE from_agent_id=:p AND to_agent_id=:ch"
                        ),
                        {"p": PARENT, "ch": child},
                    )
                ).scalars().all()
                assert set(edges) == held_before
                assert len(edges) == len(held_before)

                # child is alive gen 8; parent retired
                childrow = (
                    await c.execute(
                        text("SELECT generation, status, parent_id FROM agents WHERE id=:c"),
                        {"c": child},
                    )
                ).mappings().first()
                assert childrow["status"] == "alive"
                assert childrow["generation"] == 8
                assert childrow["parent_id"] == PARENT

                parentrow = (
                    await c.execute(
                        text("SELECT status, retired_at FROM agents WHERE id=:p"),
                        {"p": PARENT},
                    )
                ).mappings().first()
                assert parentrow["status"] == "dead"
                assert parentrow["retired_at"] is not None

            # child now resolves in the belief's lineage (Phase-1 CTE, unchanged)
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get(f"/beliefs/{ORIGIN}/lineage")
                assert r.status_code == 200, r.text
                by_id = {p["agent_id"]: p for p in r.json()["path"]}
                assert str(child) in by_id, "spawned child missing from lineage"
                assert by_id[str(child)]["from_agent_id"] == str(PARENT)
                assert by_id[str(child)]["generation"] == 8
        finally:
            await engine.dispose()

    asyncio.run(_run())
