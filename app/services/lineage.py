"""Belief lineage — traverse belief_inheritance backward to the origin.

Uses a WITH RECURSIVE CTE (a real CRDB graph traversal) anchored at the belief's
originating agent, walking to_agent edges downward. Recursive (not just ORDER BY
inherited_at) so it correctly returns a TREE when a belief branches to more than one
child — which the seed deliberately creates (crimson-4 -> crimson-5b).

This is current-state traversal; no AOST here.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text

from app.db import engine

_LINEAGE_SQL = text(
    """
    WITH RECURSIVE chain AS (
        -- anchor: the originating agent (root of the tree, depth 0)
        SELECT a.id AS agent_id, a.generation, a.bloodline, a.status,
               CAST(NULL AS UUID) AS from_agent_id,
               CAST(NULL AS TIMESTAMPTZ) AS inherited_at,
               0 AS depth
        FROM agents a
        WHERE a.id = :origin_id
        UNION ALL
        -- recurse: follow inheritance edges of THIS belief from held agents to heirs
        SELECT a.id, a.generation, a.bloodline, a.status,
               bi.from_agent_id, bi.inherited_at, chain.depth + 1
        FROM belief_inheritance bi
        JOIN chain ON bi.from_agent_id = chain.agent_id
        JOIN agents a ON a.id = bi.to_agent_id
        WHERE bi.belief_id = :belief_id
    )
    SELECT depth, agent_id, generation, bloodline, status,
           from_agent_id, inherited_at
    FROM chain
    ORDER BY depth, generation, agent_id
    """
)


async def belief_lineage(belief_id: uuid.UUID) -> dict | None:
    """Return {belief, origin_agent_id, path[]} or None if the belief doesn't exist."""
    async with engine.connect() as conn:
        belief = (
            await conn.execute(
                text(
                    "SELECT id, rule_text, status, originating_agent_id, formed_at "
                    "FROM beliefs WHERE id = :id"
                ),
                {"id": belief_id},
            )
        ).mappings().first()
        if belief is None:
            return None

        origin_id = belief["originating_agent_id"]
        path = (
            await conn.execute(
                _LINEAGE_SQL, {"origin_id": origin_id, "belief_id": belief_id}
            )
        ).mappings().all()

    return {
        "belief": dict(belief),
        "origin_agent_id": origin_id,
        "path": [dict(r) for r in path],
    }
