"""Idempotent seed for Phase 1.

Builds a genealogy of 2 bloodlines x (8-gen spine + 4 siblings) = 24 agents, then plants
ONE founding belief formed by crimson-0 and inherits it down the full crimson spine to the
living crimson-7 via real belief_inheritance rows — PLUS one branching edge (crimson-4 also
passes the belief to sibling crimson-5b) so the lineage recursive CTE's branch handling is
genuinely exercised, not just asserted.

Only the crimson bloodline carries the belief; azure is the second bloodline for the
genealogy graph (Phase 1 has exactly one belief, per CLAUDE.md).

UUIDs are derived via uuid5 so re-runs produce stable ids. Run:
    PYTHONPATH=. .venv/Scripts/python.exe seed/seed.py
"""

import asyncio
import datetime as dt
import sys
import uuid

import numpy as np
from sqlalchemy import text

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.config import get_settings
from app.db import SessionLocal, engine
from app.models import Agent, Belief, BeliefInheritance

BASE = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)  # anchor = today
_NS = uuid.uuid5(uuid.NAMESPACE_DNS, "lineage.hackathon")

BLOODLINES = ["crimson", "azure"]
# Non-spine siblings: (suffix, parent_name_fn, generation, inherits_belief, alive)
# `inherits`/`alive` only take effect for crimson (the belief-carrying bloodline).
SIBLINGS = [
    ("2b", 1, 2, False, False),
    ("3b", 2, 3, False, False),
    ("5b", 4, 5, True, True),   # <-- the branch: crimson-4 also gives the belief here
    ("6b", 5, 6, False, False),
]

RULE_TEXT = "merchant category 5411 under $180 is safe if account age > 6 months"


def aid(name: str) -> uuid.UUID:
    return uuid.uuid5(_NS, f"agent:{name}")


def bid(name: str) -> uuid.UUID:
    return uuid.uuid5(_NS, f"belief:{name}")


def days(n: int) -> dt.datetime:
    return BASE - dt.timedelta(days=n)


def placeholder_embedding(dim: int) -> list[float]:
    """Deterministic, normalized PLACEHOLDER. Real embeddings arrive in Phase 2."""
    rng = np.random.default_rng(42)
    v = rng.standard_normal(dim)
    return (v / np.linalg.norm(v)).tolist()


def build_agents() -> dict[str, Agent]:
    agents: dict[str, Agent] = {}
    for bl in BLOODLINES:
        # linear spine gen 0..7
        for g in range(8):
            name = f"{bl}-{g}"
            alive = g == 7
            agents[name] = Agent(
                id=aid(name),
                generation=g,
                bloodline=bl,
                status="alive" if alive else "dead",
                spawned_at=days(800 - 100 * g),
                retired_at=None if alive else days(700 - 100 * g),
                parent_id=aid(f"{bl}-{g - 1}") if g > 0 else None,
            )
        # siblings
        for suffix, parent_g, gen, inherits, alive in SIBLINGS:
            name = f"{bl}-{suffix}"
            live = alive and bl == "crimson"
            agents[name] = Agent(
                id=aid(name),
                generation=gen,
                bloodline=bl,
                status="alive" if live else "dead",
                spawned_at=days(800 - 100 * gen + 20),
                retired_at=None if live else days(800 - 100 * gen - 70),
                parent_id=aid(f"{bl}-{parent_g}"),
            )
    return agents


def build_inheritance() -> list[tuple[str, str]]:
    """(from_agent_name, to_agent_name) edges for the crimson belief."""
    edges = [(f"crimson-{g}", f"crimson-{g + 1}") for g in range(7)]  # spine 0->1..6->7
    edges.append(("crimson-4", "crimson-5b"))  # branch
    return edges


async def seed(session_factory=None) -> None:
    """Plant the genealogy + founding belief + inheritance closure.

    `session_factory` defaults to the app's global SessionLocal (defaultdb). The isolated demo
    stream passes its DemoSession so the SAME genealogy is seeded into the demo database — the
    unqualified DELETE/INSERTs resolve against whichever database the session is bound to.
    """
    make_session = session_factory if session_factory is not None else SessionLocal
    embedding = placeholder_embedding(get_settings().embedding_dim)
    agents = build_agents()
    edges = build_inheritance()

    async with make_session() as s:
        # Idempotent reset. Ordered DELETEs (child tables first), NOT TRUNCATE: on
        # CockroachDB TRUNCATE is a schema change that drops/recreates every index and
        # costs ~100s regardless of row count, while these DELETEs are plain DML (~1s).
        # DELETE also leaves a continuous MVCC history — no schema-change boundary — which
        # is what the AOST time-travel tests read, and it cannot hit the "indexes being
        # dropped" schema-change collision (NOTES Phase 3, Step 7). No FK has ON DELETE
        # CASCADE, so order matters; audit_log references beliefs and must be cleared
        # explicitly here (the old TRUNCATE ... CASCADE reached it implicitly).
        for table in (
            "belief_inheritance",
            "decisions",
            "belief_performance",
            "audit_log",
            "beliefs",
            "agents",
        ):
            await s.execute(text(f"DELETE FROM {table}"))

        s.add_all(agents.values())
        await s.flush()  # agents must exist before FKs reference them

        belief = Belief(
            id=bid("origin"),
            rule_text=RULE_TEXT,
            originating_agent_id=aid("crimson-0"),
            formed_at=days(780),
            embedding=embedding,
            status="active",
        )
        s.add(belief)
        await s.flush()

        for frm, to in edges:
            s.add(
                BeliefInheritance(
                    belief_id=belief.id,
                    from_agent_id=aid(frm),
                    to_agent_id=aid(to),
                    inherited_at=agents[to].spawned_at,
                )
            )
        await s.commit()

    print_summary(agents, edges)


def print_summary(agents: dict[str, Agent], edges: list[tuple[str, str]]) -> None:
    holders = {to for _, to in edges}
    origin = "crimson-0"
    holders.add(origin)  # the originator holds it too

    print("\n=== GENEALOGY (24 agents, 2 bloodlines) ===")
    hdr = f"{'agent':<12}{'gen':>3}  {'bloodline':<9}{'status':<6}{'parent':<12}{'belief?':<8}spawned->retired"
    print(hdr)
    print("-" * len(hdr))
    for name in sorted(agents, key=lambda n: (agents[n].bloodline, agents[n].generation, n)):
        a = agents[name]
        parent = next((k for k, v in agents.items() if v.id == a.parent_id), "-")
        holds = "HOLDS" if (name in holders and a.bloodline == "crimson") else ""
        ret = a.retired_at.date().isoformat() if a.retired_at else "ALIVE"
        print(
            f"{name:<12}{a.generation:>3}  {a.bloodline:<9}{a.status:<6}{parent:<12}"
            f"{holds:<8}{a.spawned_at.date().isoformat()} -> {ret}"
        )

    print("\n=== BELIEF INHERITANCE (crimson) ===")
    print(f'belief "{RULE_TEXT}"')
    print(f"  formed by {origin} (gen 0) on {days(780).date().isoformat()}")
    for frm, to in edges:
        tag = "  [BRANCH]" if to.endswith("b") else ""
        print(f"    {frm:<12} -> {to:<12} on {agents[to].spawned_at.date().isoformat()}{tag}")

    print("\n=== TRACE CHECK ===")
    print(f"  living holder crimson-7 (gen 7, ALIVE) holds belief formed by crimson-0 (gen 0)")
    print(f"  branch holder crimson-5b (gen 5, ALIVE) also inherited it via crimson-4")
    print(f"  lineage of belief:origin should resolve to 9 nodes (8 spine + 1 branch)\n")


async def _main() -> None:
    await seed()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
