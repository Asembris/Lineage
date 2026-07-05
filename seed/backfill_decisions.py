"""Deterministic historical backfill of `decisions` (Phase 2, pre-gate, OpenAI-FREE).

The dead generations applied a FIXED inherited rule — a mature agent applies its belief
consistently — so we apply that belief as a deterministic predicate over the seeded
transaction world (app/sim/transactions.py). `is_fraud` is the world's ground truth; the
verdict is the rule; nobody writes a performance number. Staleness is then the aggregation
of verdict-vs-fraud, which this script computes and prints per window so the decay curve is
visible straight from the raw rows.

The live OpenAI agent (post-gate) is a SEPARATE layer that will ADD current-window decisions
for crimson-7/crimson-8; it does not replace this backfill.

Decision policy (deterministic):
  * target-pattern txn (mcc 5411, <$180, age>6mo) -> APPROVE, driving_belief = origin belief.
    This is the belief-driven subset whose accuracy decays as the world drifts.
  * everything else -> generic verdict, driving_belief = NULL (does NOT touch this belief's
    performance) — present only so the raw table is a realistic mixed stream.

Window i maps to generation crimson-i; window 5 splits ~30% of its rows to the living branch
agent crimson-5b so the branch has real provenance too.

Run:  PYTHONPATH=. .venv/Scripts/python.exe seed/backfill_decisions.py
"""

import asyncio
import datetime as dt
import sys
import time
from decimal import Decimal

import numpy as np

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import text

from app.db import SessionLocal, engine
from app.models import Decision
from app.services.performance import recompute_belief_performance
from app.sim.transactions import (
    BASE,
    N_WINDOWS,
    Txn,
    _window_bounds_days_ago,
    generate_all,
    on_pattern_fraud_mean,
)
from seed.seed import aid, bid, seed as run_seed

ORIGIN_BELIEF = bid("origin")
_POLICY_SEED = 7788  # separate stream from the world generator; still fully reproducible


def _agent_for(window_idx: int, rng: np.random.Generator) -> str:
    """crimson-i for window i; window 5 sends ~30% to the living branch crimson-5b."""
    if window_idx == 5 and rng.random() < 0.30:
        return "crimson-5b"
    return f"crimson-{window_idx}"


def _decision_from(txn: Txn, window_idx: int, rng: np.random.Generator) -> Decision:
    if txn.on_pattern:
        # The belief fires: it asserts this pattern is safe -> approve, and IT drove the call.
        verdict = "approve"
        driving = ORIGIN_BELIEF
        confidence = float(rng.uniform(0.80, 0.95))
    else:
        # Generic handling; this belief did NOT drive it (driving_belief stays NULL).
        driving = None
        if txn.is_fraud:
            verdict = "blocked" if rng.random() < 0.6 else ("decline" if rng.random() < 0.5 else "approve")
        else:
            verdict = "approve" if rng.random() < 0.9 else "decline"
        confidence = float(rng.uniform(0.50, 0.85))

    return Decision(
        agent_id=aid(_agent_for(window_idx, rng)),
        txn_ref=txn.txn_ref,
        merchant=txn.merchant,
        amount=txn.amount,
        verdict=verdict,
        driving_belief_id=driving,
        confidence=confidence,
        decided_at=txn.decided_at,
        is_fraud=txn.is_fraud,
    )


def _window_ts(window_idx: int) -> tuple[dt.datetime, dt.datetime]:
    older, newer = _window_bounds_days_ago(window_idx)
    return BASE - dt.timedelta(days=older), BASE - dt.timedelta(days=newer)


_WINDOW_AGG = text(
    """
    SELECT
      COUNT(*)                                                        AS n,
      SUM(CASE WHEN is_fraud THEN 1 ELSE 0 END)                       AS frauds,
      SUM(CASE WHEN verdict='approve' AND is_fraud THEN 1 ELSE 0 END) AS frauds_approved,
      SUM(CASE WHEN (verdict='approve' AND NOT is_fraud)
                 OR (verdict IN ('decline','blocked') AND is_fraud)
               THEN 1 ELSE 0 END)                                     AS correct,
      SUM(CASE WHEN verdict IN ('decline','blocked') AND NOT is_fraud THEN 1 ELSE 0 END)
                                                                      AS fp,
      SUM(CASE WHEN NOT is_fraud THEN 1 ELSE 0 END)                   AS legit
    FROM decisions
    WHERE driving_belief_id = :belief
      AND decided_at >= :start AND decided_at < :end
    """
)

_BASELINE_AGG = text(
    """
    SELECT COUNT(*) AS n, SUM(CASE WHEN is_fraud THEN 1 ELSE 0 END) AS frauds
    FROM decisions
    WHERE driving_belief_id IS NULL
      AND decided_at >= :start AND decided_at < :end
    """
)


async def backfill() -> None:
    await run_seed()  # clean Phase-1 state: agents + origin belief exist, decisions truncated

    world = generate_all()
    rng = np.random.default_rng(_POLICY_SEED)

    # Insert per generation-window with a FLUSHED heartbeat. Rationale: the bulk insert is
    # the slow, silent phase (server-default UUID PKs force per-row INSERT...RETURNING over
    # the Cloud link — minutes, not seconds), and Python block-buffers stdout, so a single
    # big commit looks identical whether it's crawling or dead. A per-window line with
    # flush=True gives a visible pulse; each window is its own small commit so a stall is
    # localized and its progress survives. RNG draw order is unchanged (window-major, same
    # inner txn order), so the deterministic decay curve is byte-for-byte identical.
    total = 0
    n_windows = len(world)
    t0 = time.perf_counter()
    print(
        f"\n=== INSERTING decisions across {n_windows} windows "
        f"(per-window heartbeat; bulk insert over Cloud is slow, this is normal) ===",
        flush=True,
    )
    for w, txns in enumerate(world):
        window_decisions = [_decision_from(txn, w, rng) for txn in txns]
        t_win = time.perf_counter()
        async with SessionLocal() as s:
            s.add_all(window_decisions)
            await s.commit()
        total += len(window_decisions)
        print(
            f"  window {w}/{n_windows - 1}: +{len(window_decisions):>4} rows"
            f" (total {total:>4})  window {time.perf_counter() - t_win:5.1f}s"
            f"  elapsed {time.perf_counter() - t0:6.1f}s",
            flush=True,
        )

    print(f"=== insert phase done: {total} rows in {time.perf_counter() - t0:.1f}s ===", flush=True)

    # PERSIST the measured staleness curve into belief_performance from the SAME per-window
    # aggregation the report prints (confidence = correct/total; nothing hardcoded). Before this,
    # only test_staleness ever populated the table, so a real invalidation / the new
    # GET /beliefs/{id}/performance read on a freshly-seeded cluster saw zero rows. Now every
    # reseed leaves belief_performance consistent with decisions — one source of truth shared by
    # the certificate's staleness_evidence and the frontend Time-travel curve.
    # NOTE: only the origin belief exists today; if a second belief is ever seeded, loop this
    # recompute over all beliefs (recompute is idempotent per belief_id).
    windows = [_window_ts(w) for w in range(N_WINDOWS)]
    persisted = await recompute_belief_performance(ORIGIN_BELIEF, windows)
    print(
        f"=== belief_performance persisted: {len(persisted)} windows for origin belief ===",
        flush=True,
    )

    await report(total)


async def report(total: int) -> None:
    print(f"\n=== BACKFILL COMPLETE: {total} decisions inserted ===")
    print(
        "Per-window belief_performance for the origin belief, COMPUTED from the raw rows\n"
        "(confidence = correct/total; nothing hardcoded). Off-pattern baseline shown for context.\n"
    )
    hdr = (
        f"{'win/gen':<8}{'window (UTC dates)':<26}{'n':>5}{'fraud%':>8}"
        f"{'conf':>7}{'fr_appr':>8}{'fp_rate':>8}   {'baseline%':>9}"
    )
    print(hdr)
    print("-" * len(hdr))

    async with engine.connect() as c:
        for w in range(N_WINDOWS):
            start, end = _window_ts(w)
            r = (await c.execute(_WINDOW_AGG, {"belief": ORIGIN_BELIEF, "start": start, "end": end})).mappings().first()
            b = (await c.execute(_BASELINE_AGG, {"start": start, "end": end})).mappings().first()

            n = r["n"] or 0
            fraud_rate = (r["frauds"] or 0) / n if n else 0.0
            confidence = (r["correct"] or 0) / n if n else 0.0
            fp_rate = (r["fp"] or 0) / r["legit"] if r["legit"] else 0.0
            base_rate = (b["frauds"] or 0) / b["n"] if b["n"] else 0.0

            dates = f"{start.date().isoformat()}..{end.date().isoformat()}"
            marker = "  <-- ALIVE" if w == N_WINDOWS - 1 else ""
            print(
                f"{f'w{w}/g{w}':<8}{dates:<26}{n:>5}{fraud_rate*100:>7.1f}%"
                f"{confidence:>7.3f}{r['frauds_approved'] or 0:>8}{fp_rate:>8.3f}"
                f"   {base_rate*100:>8.1f}%{marker}"
            )

    print(
        "\nHidden trend means (documentation only — NEVER persisted; the numbers above are\n"
        "aggregated from Bernoulli-sampled labels, so they scatter around these):"
    )
    print("  " + "  ".join(f"g{w}:{on_pattern_fraud_mean(w):.3f}" for w in range(N_WINDOWS)))
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(backfill())
