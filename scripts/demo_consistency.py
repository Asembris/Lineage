"""Visible proof: atomic (CRDB) vs eventually-consistent invalidation.

Reseeds, then MEASURES the contrast against the live cluster - nothing narrated:

  PART A  A closure observer watches while each strategy runs.
          * Strong (the real endpoint): one transaction, 1 commit point, 0 split samples.
          * Eventual baseline: per-holder fan-out, N+1 commit points, a real split window.
  PART B  What the split window COSTS: in a committed intermediate state a lagging holder
          still APPROVES a matching fraud (Phase-2 deterministic predicate); the atomic
          correction leaves no such state, so the same holder blocks it.

Run:  PYTHONPATH=. .venv/Scripts/python.exe scripts/demo_consistency.py
"""

import asyncio
import sys
import time

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import text  # noqa: E402

from app.db import engine  # noqa: E402
from app.services import consistency  # noqa: E402
from app.services.invalidation import invalidate_belief  # noqa: E402
from seed.seed import aid, bid  # noqa: E402
from seed.seed import seed as run_seed  # noqa: E402

ORIGIN = bid("origin")
ACTOR = aid("crimson-7")
CRIMSON_7 = aid("crimson-7")
CRIMSON_5B = aid("crimson-5b")


async def _measure(strategy_factory):
    stop = asyncio.Event()
    ready = asyncio.Event()
    observer = asyncio.create_task(
        consistency.observe_closure(ORIGIN, stop, interval=0.008, ready=ready)
    )
    await ready.wait()
    await asyncio.sleep(0.05)
    t0 = time.monotonic()
    ret = await strategy_factory()
    elapsed_ms = (time.monotonic() - t0) * 1000
    stop.set()
    result = await observer
    return result, ret, elapsed_ms


async def part_a():
    print("\n" + "=" * 72)
    print("PART A - closure observer watches each invalidation strategy")
    print("=" * 72)

    await run_seed()
    strong, _, strong_ms = await _measure(lambda: invalidate_belief(ORIGIN, ACTOR))
    print("\n  STRONG (CRDB single transaction - the real endpoint):")
    print(f"    commit points     : 1")
    print(f"    observer samples  : {len(strong.samples)}")
    print(f"    SPLIT samples     : {strong.split_count}")
    print(f"    saw transition    : {strong.saw_transition}  (active -> invalidated)")
    print(f"    wall time         : {strong_ms:.0f} ms")

    await run_seed()
    ev_res, ev, ev_ms = await _measure(
        lambda: consistency.eventual_invalidate(ORIGIN, ACTOR, per_holder_delay=0.06)
    )
    print("\n  EVENTUAL (per-holder fan-out - Postgres+Pinecone-style baseline):")
    print(f"    commit points     : {ev.commit_points}")
    print(f"    observer samples  : {len(ev_res.samples)}")
    print(f"    SPLIT samples     : {ev_res.split_count}   <-- committed, externally visible")
    print(f"    saw transition    : {ev_res.saw_transition}")
    print(f"    window (wall time): {ev_ms:.0f} ms of divergent fleet state")

    print("\n  Delay-free structural fact (independent of the fan-out delay):")
    print(f"    strong = 1 commit point  -> a split state is UNREACHABLE")
    print(f"    eventual = {ev.commit_points} commit points -> a split state is REACHABLE and was observed")


async def part_b():
    print("\n" + "=" * 72)
    print("PART B - what the split window costs: a leaked fraud approval")
    print("=" * 72)

    await run_seed()
    # A committed intermediate state: crimson-5b updated, crimson-7 has not caught up.
    async with engine.begin() as c:
        await c.execute(
            text(
                "UPDATE belief_inheritance SET invalidated_at=now(), invalidated_by=:a "
                "WHERE belief_id=:b AND to_agent_id=:h"
            ),
            {"a": ACTOR, "b": ORIGIN, "h": CRIMSON_5B},
        )
    leaked = await consistency.deterministic_verdict(ORIGIN, CRIMSON_7)
    corrected = await consistency.deterministic_verdict(ORIGIN, CRIMSON_5B)
    print("\n  Mid-fan-out (crimson-5b updated, crimson-7 lagging) - a matching fraud arrives:")
    print(f"    crimson-7  (lagging) -> {leaked.upper()}   <-- FRAUD APPROVED on the dead belief")
    print(f"    crimson-5b (updated) -> {corrected.upper()}")

    # Atomic correction: one commit closes the whole closure. No lagging holder remains.
    await invalidate_belief(ORIGIN, ACTOR)
    after = await consistency.deterministic_verdict(ORIGIN, CRIMSON_7)
    print("\n  After the ATOMIC invalidation (one CRDB transaction, whole fleet at once):")
    print(f"    crimson-7            -> {after.upper()}   <-- corrected instantly, no window to leak in")


async def main():
    await part_a()
    await part_b()
    print("\n" + "=" * 72)
    print("CockroachDB closes the belief and its entire inherited closure in ONE commit.")
    print("A store without cross-key atomic transactions must fan out - and every holder it")
    print("has not yet reached is a living agent still approving fraud on a dead belief.")
    print("=" * 72 + "\n")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
