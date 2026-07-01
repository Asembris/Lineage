"""THE Phase-2 done-test: prove staleness is REAL — derived from data, not stored.

Hermetic and OpenAI-free: it seeds its OWN small, fully-controlled decisions set (the belief
applied uniformly, is_fraud low early / high late), computes belief_performance, and asserts
the decay. Then the KILL-SHOT: flip the late window's is_fraud back to legit and recompute —
confidence RECOVERS. If the number were hardcoded it would persist; because it recovers, it
is a pure function of the stored data. This mirrors the Phase-1 AOST test's "change the data
-> the result changes" philosophy.
"""

import asyncio
import datetime as dt
import uuid

from sqlalchemy import insert, text

from app.db import engine
from app.models import Decision
from app.services.performance import recompute_belief_performance
from seed.seed import aid, bid, seed as run_seed

ORIGIN = bid("origin")
AGENT = aid("crimson-7")  # a living holder of the belief

BASE = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)
EARLY = (BASE - dt.timedelta(days=400), BASE - dt.timedelta(days=390))
LATE = (BASE - dt.timedelta(days=100), BASE - dt.timedelta(days=90))
_EARLY_AT = BASE - dt.timedelta(days=395)
_LATE_AT = BASE - dt.timedelta(days=95)


def _rows(decided_at, n, frauds):
    """n belief-driven APPROVE decisions, exactly `frauds` of them fraudulent (deterministic)."""
    out = []
    for i in range(n):
        out.append(
            {
                "id": uuid.uuid4(),
                "agent_id": AGENT,
                "txn_ref": f"stale-{decided_at.date()}-{i:03d}",
                "merchant": "Grocery Mart #500",
                "amount": 99.00,
                "verdict": "approve",  # the belief asserts "safe" -> approve, uniformly
                "driving_belief_id": ORIGIN,
                "confidence": 0.9,
                "decided_at": decided_at,
                "is_fraud": i < frauds,
            }
        )
    return out


def test_staleness_emerges_and_recovers():
    async def _run():
        try:
            await run_seed()  # agents + origin belief exist; decisions truncated

            # Uniform belief application; world drifts: 5% fraud early, 55% fraud late.
            async with engine.begin() as c:
                await c.execute(insert(Decision), _rows(_EARLY_AT, 200, 10))
                await c.execute(insert(Decision), _rows(_LATE_AT, 200, 110))

            # --- staleness emerges, computed from the rows ---
            await recompute_belief_performance(ORIGIN, [EARLY, LATE])
            perf = await _fetch_perf()
            assert len(perf) == 2, perf
            early, late = perf[0], perf[1]  # ordered by window_start
            assert early["confidence"] > 0.9, early          # valid then
            assert late["confidence"] < 0.6, late            # rotten now
            assert late["frauds_approved"] == 110, late
            assert early["frauds_approved"] == 10, early

            # --- KILL-SHOT: flip the late window's ground truth, recompute ---
            async with engine.begin() as c:
                await c.execute(
                    text(
                        "UPDATE decisions SET is_fraud = false "
                        "WHERE driving_belief_id = :b "
                        "AND decided_at >= :s AND decided_at < :e"
                    ),
                    {"b": ORIGIN, "s": LATE[0], "e": LATE[1]},
                )
            await recompute_belief_performance(ORIGIN, [EARLY, LATE])
            perf2 = await _fetch_perf()
            late2 = perf2[1]
            assert late2["confidence"] > 0.9, (
                "confidence did NOT recover when the data changed — it is stored, not derived"
            )
            assert late2["frauds_approved"] == 0, late2
        finally:
            await engine.dispose()

    asyncio.run(_run())


async def _fetch_perf():
    async with engine.connect() as c:
        rows = (
            await c.execute(
                text(
                    "SELECT window_start, confidence, false_positive_rate, frauds_approved "
                    "FROM belief_performance WHERE belief_id = :b ORDER BY window_start"
                ),
                {"b": ORIGIN},
            )
        ).mappings().all()
    return [dict(r) for r in rows]
