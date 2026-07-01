"""VISIBLE counterfactual: staleness is derived from data, not stored.

Seeds a controlled belief-driven decisions set (uniform application; 5% fraud early, 55%
late), computes belief_performance, then flips the late window's ground truth back to legit
and recomputes. Confidence recovers — proving the number is a function of the rows, not a
stored constant. Same logic as tests/test_staleness.py, but it prints so you can watch it.

Run:  PYTHONPATH=. .venv/Scripts/python.exe -m scripts.demo_staleness
"""

import asyncio
import datetime as dt
import sys
import uuid

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import insert, text

from app.db import engine
from app.models import Decision
from app.services.performance import recompute_belief_performance
from seed.seed import aid, bid, seed as run_seed

ORIGIN = bid("origin")
AGENT = aid("crimson-7")
BASE = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)
EARLY = (BASE - dt.timedelta(days=400), BASE - dt.timedelta(days=390))
LATE = (BASE - dt.timedelta(days=100), BASE - dt.timedelta(days=90))


def _rows(decided_at, n, frauds):
    return [
        {
            "id": uuid.uuid4(), "agent_id": AGENT, "txn_ref": f"d-{decided_at.date()}-{i}",
            "merchant": "Grocery Mart #500", "amount": 99.00, "verdict": "approve",
            "driving_belief_id": ORIGIN, "confidence": 0.9, "decided_at": decided_at,
            "is_fraud": i < frauds,
        }
        for i in range(n)
    ]


def _show(label, perf):
    e, l = perf[0], perf[1]
    print(
        f"  {label:<22} early conf={e['confidence']:.3f} (fr_appr={e['frauds_approved']:>3})   "
        f"late conf={l['confidence']:.3f} (fr_appr={l['frauds_approved']:>3})"
    )


async def main() -> None:
    await run_seed()
    async with engine.begin() as c:
        await c.execute(insert(Decision), _rows(BASE - dt.timedelta(days=395), 200, 10))
        await c.execute(insert(Decision), _rows(BASE - dt.timedelta(days=95), 200, 110))

    print("\n=== STALENESS IS REAL (computed from decisions, not stored) ===")
    p1 = await recompute_belief_performance(ORIGIN, [EARLY, LATE])
    _show("as recorded:", p1)
    print("  -> belief was VALID early, ROTTEN late — straight from the rows.\n")

    async with engine.begin() as c:
        c_res = await c.execute(
            text(
                "UPDATE decisions SET is_fraud=false WHERE driving_belief_id=:b "
                "AND decided_at >= :s AND decided_at < :e"
            ),
            {"b": ORIGIN, "s": LATE[0], "e": LATE[1]},
        )
    print(f"  [counterfactual] flipped {c_res.rowcount} late-window decisions to is_fraud=false")
    p2 = await recompute_belief_performance(ORIGIN, [EARLY, LATE])
    _show("after flipping data:", p2)
    print(
        "  -> late confidence RECOVERED. A hardcoded drop could not recover.\n"
        "     The staleness signal is a pure function of the data. QED.\n"
    )
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
