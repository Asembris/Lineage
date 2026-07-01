"""Agent lifecycle — spawn a child that inherits the parent's beliefs (Phase 2).

spawn_child() performs spawn -> inherit -> retire as ONE CockroachDB transaction:
  * insert the child agent (next generation, same bloodline, alive);
  * for every ACTIVE belief the parent holds (originated ∪ inherited), write a real
    belief_inheritance edge parent -> child — the provenance the lineage CTE traverses;
  * retire the parent (dead, retired_at set).

Atomic on purpose: a child must never exist half-inherited, and the parent must not retire
while its beliefs failed to pass down. The inheritance rows are real FKs — no dangling refs,
so the child immediately resolves in GET /beliefs/{id}/lineage with no other change.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select, text

from app.db import SessionLocal
from app.models import Agent, BeliefInheritance

# Active beliefs an agent holds: ones it originated, or ones inherited via a real edge.
_HELD_ACTIVE_SQL = text(
    """
    SELECT DISTINCT b.id
    FROM beliefs b
    LEFT JOIN belief_inheritance bi
           ON bi.belief_id = b.id AND bi.to_agent_id = :agent
    WHERE b.status = 'active'
      AND (b.originating_agent_id = :agent OR bi.to_agent_id = :agent)
    """
)


async def spawn_child(parent_id: uuid.UUID) -> dict:
    """Spawn a child of `parent_id`, inheriting all active held beliefs. One transaction."""
    now = dt.datetime.now(dt.timezone.utc)
    async with SessionLocal() as s:
        async with s.begin():  # single atomic CRDB transaction
            parent = (
                await s.execute(select(Agent).where(Agent.id == parent_id))
            ).scalar_one_or_none()
            if parent is None:
                raise ValueError("parent agent not found")
            if parent.status != "alive":
                raise ValueError("parent agent is not alive")

            held_ids = list(
                (await s.execute(_HELD_ACTIVE_SQL, {"agent": parent_id})).scalars().all()
            )

            child_id = uuid.uuid4()
            child_generation = parent.generation + 1
            bloodline = parent.bloodline
            s.add(
                Agent(
                    id=child_id,
                    generation=child_generation,
                    bloodline=bloodline,
                    status="alive",
                    spawned_at=now,
                    parent_id=parent_id,
                )
            )
            await s.flush()  # child must exist before inheritance FKs reference it

            for belief_id in held_ids:
                s.add(
                    BeliefInheritance(
                        belief_id=belief_id,
                        from_agent_id=parent_id,
                        to_agent_id=child_id,
                        inherited_at=now,
                    )
                )

            parent.status = "dead"
            parent.retired_at = now

    return {
        "child_id": child_id,
        "generation": child_generation,
        "bloodline": bloodline,
        "parent_id": parent_id,
        "inherited_belief_ids": held_ids,
        "spawned_at": now,
    }
