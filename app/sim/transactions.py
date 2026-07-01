"""Synthetic transaction stream with REAL concept drift.

The founding belief blesses a target pattern:
    mcc 5411 (grocery) AND amount < $180 AND account_age > 6 months  ->  "safe, approve"

When crimson-0 formed it, that pattern really WAS safe (fraud ≈ the flat background rate).
Over the bloodline's ~780-day life, fraudsters adopt exactly this pattern, so its
ground-truth fraud rate climbs. The belief and the agent's application never change; only
the world drifts. Measured performance therefore decays for a purely data-driven reason.

Three layers of realism make the raw table read like organic drift, not a staged sigmoid:

  1. Hidden trend  — a logistic ramp on the target pattern's fraud MEAN (never written out).
  2. Regime jitter — one Gaussian shock per window shifts that mean off the curve, plus a
     modeled fraud CAMPAIGN that spikes around gen 5 and recedes in gen 6 (a ring that gets
     disrupted) — this produces the gen-6 recession dip a monotone curve could never show.
  3. Bernoulli   — each transaction's is_fraud is sampled, so the OBSERVED rate scatters
     around the window mean too.

Off-pattern traffic carries a non-zero, roughly-flat background fraud rate, so the table is
a realistic mix — not "0% everywhere except a curve." Everything is seeded, so the whole
4,000-row world is reproducible and judge-inspectable.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from decimal import Decimal

import numpy as np

# --- world constants -------------------------------------------------------------------

BASE = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)  # "today" — matches seed.py

N_WINDOWS = 8  # one per belief-holding generation, crimson-0 (gen 0) .. crimson-7 (gen 7)
ON_PATTERN_PER_WINDOW = 250  # belief-driven decisions/window (SE ≈ 0.032 worst case)
OTHER_PER_WINDOW = 250       # off/near-pattern rows for table realism (driving_belief=NULL)

TARGET_MCC = 5411  # grocery
AMOUNT_CEILING = Decimal("180")
AGE_MONTHS_THRESHOLD = 6.0

# Hidden fraud-rate process for the TARGET pattern (means only; never persisted).
_TREND_FLOOR = 0.025
_TREND_CEIL = 0.50
_TREND_CENTER = 4.3   # drift takes off around gen 4-5
_TREND_SCALE = 1.15
_CAMPAIGN_AMP = 0.11   # fraud-ring burst
_CAMPAIGN_CENTER = 5.0
_CAMPAIGN_WIDTH = 0.62
_WINDOW_JITTER_SD = 0.03  # per-window regime shock on the target pattern

_BASELINE_FRAUD = 0.03        # flat off/near-pattern background
_BASELINE_JITTER_SD = 0.008

# Off-pattern merchant categories (name, mcc) for a realistic mixed table.
_OTHER_MERCHANTS = [
    ("QuickCash ATM", 6011),
    ("Skyline Airlines", 4511),
    ("Nova Electronics", 5732),
    ("Prime Fuel", 5541),
    ("Urban Threads", 5651),
    ("Golden Wok", 5812),
]

SEED = 20260701  # fixed → the entire world is reproducible


@dataclass(frozen=True)
class Txn:
    txn_ref: str
    merchant: str
    mcc: int
    amount: Decimal
    account_age_months: float
    decided_at: dt.datetime
    is_fraud: bool
    on_pattern: bool  # matches the belief's target pattern exactly (=> belief-driven)


def matches_target(mcc: int, amount: Decimal, age_months: float) -> bool:
    """The founding belief's predicate. A mature agent applies it deterministically."""
    return (
        mcc == TARGET_MCC
        and amount < AMOUNT_CEILING
        and age_months > AGE_MONTHS_THRESHOLD
    )


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def on_pattern_fraud_mean(window_idx: int) -> float:
    """Hidden mean fraud rate for the target pattern in a window (trend + campaign).

    Exposed for documentation/inspection only. Nothing in the pipeline writes this value;
    the recorded fraud is the Bernoulli sample, and performance is aggregated from that.
    """
    trend = _TREND_FLOOR + (_TREND_CEIL - _TREND_FLOOR) * _sigmoid(
        (window_idx - _TREND_CENTER) / _TREND_SCALE
    )
    campaign = _CAMPAIGN_AMP * math.exp(
        -(((window_idx - _CAMPAIGN_CENTER) / _CAMPAIGN_WIDTH) ** 2)
    )
    return trend + campaign


def _window_bounds_days_ago(window_idx: int) -> tuple[float, float]:
    """(older_days_ago, newer_days_ago) for a generation's ~100-day lifespan window.

    Matches seed.py: gen g spawned 800-100g days ago, retired 700-100g days ago. Window 0
    starts at the belief's formation (780 days ago, 20 days into crimson-0's life). Window 7
    (the living generation) runs up to ~1 day ago so every decided_at stays strictly past.
    """
    older = 800.0 - 100.0 * window_idx
    newer = 700.0 - 100.0 * window_idx
    if window_idx == 0:
        older = 780.0  # belief formed_at (see seed.RULE_TEXT / days(780))
    if window_idx == N_WINDOWS - 1:
        newer = 1.0  # living generation — stop just short of now
    return older, newer


def _decided_at(rng: np.random.Generator, window_idx: int) -> dt.datetime:
    older, newer = _window_bounds_days_ago(window_idx)
    days_ago = float(rng.uniform(newer, older))
    return BASE - dt.timedelta(days=days_ago)


def generate_window(window_idx: int, rng: np.random.Generator) -> list[Txn]:
    """All transactions for one window: ON_PATTERN_PER_WINDOW target-pattern txns
    (belief-driven) + OTHER_PER_WINDOW off/near-pattern txns (background)."""
    txns: list[Txn] = []

    # --- target-pattern traffic (the belief-driven subset) ---
    on_mean = on_pattern_fraud_mean(window_idx)
    on_rate = float(np.clip(on_mean + rng.normal(0.0, _WINDOW_JITTER_SD), 0.005, 0.62))
    for i in range(ON_PATTERN_PER_WINDOW):
        amount = Decimal(f"{rng.uniform(5.0, 179.0):.2f}")
        age = float(rng.uniform(6.5, 96.0))  # > 6 months, comfortably in-pattern
        txns.append(
            Txn(
                txn_ref=f"txn-w{window_idx}-p{i:04d}",
                merchant=f"Grocery Mart #{int(rng.integers(100, 999))}",
                mcc=TARGET_MCC,
                amount=amount,
                account_age_months=age,
                decided_at=_decided_at(rng, window_idx),
                is_fraud=bool(rng.random() < on_rate),
                on_pattern=True,
            )
        )

    # --- background traffic (off-pattern + near-misses): flat non-zero fraud ---
    base_rate = float(
        np.clip(_BASELINE_FRAUD + rng.normal(0.0, _BASELINE_JITTER_SD), 0.005, 0.08)
    )
    for i in range(OTHER_PER_WINDOW):
        if rng.random() < 0.4:
            # near-miss on mcc 5411 (just fails one condition) — shows the belief's boundary
            name = f"Grocery Mart #{int(rng.integers(100, 999))}"
            mcc = TARGET_MCC
            if rng.random() < 0.5:
                amount = Decimal(f"{rng.uniform(180.0, 260.0):.2f}")  # over ceiling
                age = float(rng.uniform(6.5, 96.0))
            else:
                amount = Decimal(f"{rng.uniform(5.0, 179.0):.2f}")
                age = float(rng.uniform(0.5, 5.5))  # account too young
        else:
            name, mcc = _OTHER_MERCHANTS[int(rng.integers(0, len(_OTHER_MERCHANTS)))]
            amount = Decimal(f"{rng.uniform(5.0, 900.0):.2f}")
            age = float(rng.uniform(0.5, 96.0))
        txns.append(
            Txn(
                txn_ref=f"txn-w{window_idx}-o{i:04d}",
                merchant=name,
                mcc=mcc,
                amount=amount,
                account_age_months=age,
                decided_at=_decided_at(rng, window_idx),
                is_fraud=bool(rng.random() < base_rate),
                on_pattern=False,
            )
        )

    return txns


def generate_all() -> list[list[Txn]]:
    """The full seeded world: a list of per-window transaction lists (index == generation)."""
    rng = np.random.default_rng(SEED)
    return [generate_window(w, rng) for w in range(N_WINDOWS)]


def generation_windows() -> list[tuple[dt.datetime, dt.datetime]]:
    """The 8 (start, end) timestamp windows — one per belief-holding generation.

    Shared by the backfill report and app/services/performance.py so the per-window
    aggregation buckets identically to how the world was generated.
    """
    out: list[tuple[dt.datetime, dt.datetime]] = []
    for w in range(N_WINDOWS):
        older, newer = _window_bounds_days_ago(w)
        out.append((BASE - dt.timedelta(days=older), BASE - dt.timedelta(days=newer)))
    return out
