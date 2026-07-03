"""Phase-3 done-test: strong (CRDB) vs eventually-consistent invalidation, MEASURED.

The money-shot's honest core. Three properties:

  1. STRONG has NO split window — a concurrent observer watching the closure during the real
     single-transaction invalidation records ZERO split samples, yet DOES see the transition
     (all-active -> all-invalidated). Commit points = 1.
  2. EVENTUAL baseline EXPOSES a split window — the same observer, watching the per-holder
     fan-out, records split samples (some holders invalidated, some still live). Commit
     points = N+1. The window is real, committed, externally visible.
  3. The split window LEAKS a fraud approval — in a committed intermediate state a lagging
     holder still APPROVES a matching fraud (Phase-2 deterministic predicate), while an
     already-updated holder blocks it. The atomic correction eliminates that state entirely.

Not narrated — every assertion is measured against the live cluster.
"""

import asyncio

from sqlalchemy import text

from app.db import engine
from app.services import consistency
from app.services.invalidation import invalidate_belief
from seed.seed import aid, bid
from seed.seed import seed as run_seed

ORIGIN = bid("origin")
ACTOR = aid("crimson-7")
CRIMSON_7 = aid("crimson-7")   # living spine holder
CRIMSON_5B = aid("crimson-5b")  # living branch holder


async def _run_with_observer(strategy_coro_factory):
    """Run an invalidation strategy concurrently with the closure observer.

    The strategy waits briefly so the observer first records the all-active state, then runs,
    then signals stop. Returns (ObserverResult, strategy_return_value).
    """
    stop = asyncio.Event()
    ready = asyncio.Event()

    observer = asyncio.create_task(
        consistency.observe_closure(ORIGIN, stop, interval=0.008, ready=ready)
    )
    await ready.wait()          # observer is connected and has sampled ALL_ACTIVE
    await asyncio.sleep(0.05)   # a few more active samples for margin
    ret = await strategy_coro_factory()
    stop.set()
    result = await observer
    return result, ret


def test_strong_invalidation_has_no_split_window():
    async def _run():
        try:
            await run_seed()
            result, _ = await _run_with_observer(
                lambda: invalidate_belief(ORIGIN, ACTOR)
            )
            assert result.saw_transition, (
                "observer must witness active->invalidated, else 0-splits is meaningless"
            )
            assert result.split_count == 0, (
                f"strong path must never expose a split; saw {result.split_count} "
                f"in {len(result.samples)} samples"
            )
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_eventual_baseline_exposes_split_window():
    async def _run():
        try:
            await run_seed()
            result, ev = await _run_with_observer(
                lambda: consistency.eventual_invalidate(ORIGIN, ACTOR, per_holder_delay=0.06)
            )
            assert result.saw_transition, result.samples
            # The delay-free structural fact: N+1 commit points vs the strong path's 1.
            assert ev.commit_points == 9, ev.commit_points  # 8 edges + the belief row
            # The measured window: at least one committed split state was observed.
            assert result.split_count > 0, (
                "eventual fan-out must expose a committed split window"
            )
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_split_window_leaks_a_fraud_approval():
    async def _run():
        try:
            await run_seed()

            # A committed INTERMEDIATE state: crimson-5b's copy updated, crimson-7's has not
            # yet caught up (exactly what an eventual fan-out produces between commits).
            async with engine.begin() as c:
                await c.execute(
                    text(
                        "UPDATE belief_inheritance SET invalidated_at=now(), invalidated_by=:a "
                        "WHERE belief_id=:b AND to_agent_id=:h"
                    ),
                    {"a": ACTOR, "b": ORIGIN, "h": CRIMSON_5B},
                )

            # The lagging holder still acts on the dead belief -> APPROVES the fraud (the leak).
            leaked = await consistency.deterministic_verdict(ORIGIN, CRIMSON_7)
            corrected = await consistency.deterministic_verdict(ORIGIN, CRIMSON_5B)
            assert leaked == "approve", f"lagging holder should leak an approval, got {leaked}"
            assert corrected == "blocked", f"updated holder should block, got {corrected}"

            # The atomic correction closes the whole closure in one commit — no lagging holder
            # remains, so the leak is gone fleet-wide.
            await invalidate_belief(ORIGIN, ACTOR)
            after = await consistency.deterministic_verdict(ORIGIN, CRIMSON_7)
            assert after == "blocked", f"after atomic invalidation holder must block, got {after}"
        finally:
            await engine.dispose()

    asyncio.run(_run())
