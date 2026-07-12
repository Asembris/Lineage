"""Done-test for the counterfactual "what-if invalidation" (Roadmap Item B, amended by G4).

Hermetic and deterministic: seeds its OWN small, fully-controlled belief-driven decisions set
(known counts, known fraud subset, known holders) so the affected-set logic is proven against
data the test owns — not against the 4,000-row backfill (whose real numbers, N=1000/M=392 at the
window-4 T, are confirmed by a live query, not by this test).

=====================================================================================
THE INVARIANT THIS TEST USED TO ASSERT IS DEAD, AND ITS DEATH IS A REAL FINDING.
=====================================================================================
Item B asserted `withdrawn_approvals == approvals` — "the belief ONLY EVER APPROVES, so
invalidation can only withdraw approvals". That was TRUE of the crimson belief (its whole
behaviour is one branch of `_decision_from`: on-pattern -> approve) and it is FALSE OF THE FLEET.
The grounding seam's azure belief BLOCKS: `aml_seam.VERDICT_FOR` maps a re-derived laundering
cycle to `blocked`, on 57 of its 1,500 decisions.

Under the old aggregate the endpoint counted those blocks as withdrawn approvals and — the real
damage — counted the 43 laundering rows the belief CORRECTLY BLOCKED as `frauds_auto_approved`.
A forensic tool crediting a belief's catches as its harms is wrong in the worst available
direction, so the aggregates are now verdict-aware and this test proves it on a belief that
genuinely blocks. `test_a_blocking_belief_is_not_reported_as_approving` is the regression: it
FAILS against the pre-G4 service.

Proves:
  * the affected set is exactly {driving_belief_id = X AND decided_at > T};
  * approvals and blocks are counted SEPARATELY, and fraud is attributed to the verdict that
    actually happened (a blocked fraud is `frauds_caught_by_block`, never `frauds_auto_approved`);
  * a NULL-driver decision after T is NOT counted (invalidation cannot flip a non-driven verdict);
  * `decided_at > T` is STRICT — a decision exactly at T is excluded;
  * the per-window breakdown sums to the summary, and windows come from the belief's OWN
    belief_performance rows;
  * a belief with NO measured windows gets `windows: None` — never a fabricated grid of zeros;
  * both extremes fall out of the plain WHERE with no special-casing: T before the earliest
    decision => the full driven set; T after the latest => an empty set.

Plus a pure test of parse_at (no DB). Mirrors test_staleness.py's controlled-seed philosophy.
"""

import asyncio
import datetime as dt
import uuid

import pytest
from sqlalchemy import insert

from app.db import engine
from app.models import Decision
from app.services import counterfactual as cf
from app.services.performance import recompute_belief_performance
from app.sim.transactions import generation_windows
from seed.seed import aid, bid, seed as run_seed

ORIGIN = bid("origin")
AML_BELIEF = bid("aml-cycle")  # the azure laundering belief — it BLOCKS (G4)
A7 = aid("crimson-7")   # a living holder
A5B = aid("crimson-5b")  # the branch holder
A6 = aid("crimson-6")
AZURE_7 = aid("azure-7")  # the living holder of the laundering belief

BASE = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)


def _row(agent, decided_at, *, fraud, driven=True, verdict="approve", belief=None):
    """A controlled belief-driven decision. Card-shaped (merchant/confidence set) so it satisfies
    migration 0007's ck_decisions_kind without needing a real aml_transactions FK target — the
    counterfactual reads only verdict / is_fraud / driving_belief_id / decided_at / agent_id."""
    return {
        "id": uuid.uuid4(),
        "agent_id": agent,
        "txn_ref": f"cf-{decided_at.date()}-{uuid.uuid4().hex[:6]}",
        "merchant": "Grocery Mart #500",
        "amount": 99.00,
        "verdict": verdict,
        "driving_belief_id": (belief or ORIGIN) if driven else None,
        "confidence": 0.9,
        "decided_at": decided_at,
        "is_fraud": fraud,
    }


def test_parse_at_forms_and_errors():
    # ISO date -> midnight UTC; naive datetime -> UTC; tz-aware preserved.
    assert cf.parse_at("2025-05-27") == dt.datetime(2025, 5, 27, tzinfo=dt.timezone.utc)
    assert cf.parse_at("2025-05-27T13:30:00") == dt.datetime(
        2025, 5, 27, 13, 30, tzinfo=dt.timezone.utc
    )
    assert cf.parse_at("2025-05-27T13:30:00+02:00").utcoffset() == dt.timedelta(hours=2)
    for bad in ("not-a-date", "", "2025-13-40", "yesterday"):
        with pytest.raises(ValueError):
            cf.parse_at(bad)


def test_counterfactual_affected_set_and_extremes():
    async def _run():
        try:
            await run_seed()  # agents + origin belief exist; decisions truncated

            T = BASE - dt.timedelta(days=300)  # window-4/5 boundary
            before = BASE - dt.timedelta(days=350)  # window 4
            after_a = BASE - dt.timedelta(days=250)  # window 5
            after_b = BASE - dt.timedelta(days=150)  # window 6

            rows = []
            # --- before T (excluded): 4 driven approvals by crimson-6, 1 fraud ---
            rows += [_row(A6, before, fraud=(i == 0)) for i in range(4)]
            # --- exactly at T (excluded — strict >): 1 driven approval, not fraud ---
            rows.append(_row(A7, T, fraud=False))
            # --- after T (included): 6 by crimson-7 (3 fraud) + 3 by crimson-5b (2 fraud) ---
            rows += [_row(A7, after_a, fraud=(i < 3)) for i in range(6)]
            rows += [_row(A5B, after_b, fraud=(i < 2)) for i in range(3)]
            # --- after T but NOT driven (must be ignored): a fraud approval with NULL driver ---
            rows.append(_row(A7, after_a, fraud=True, driven=False))

            async with engine.begin() as c:
                await c.execute(insert(Decision), rows)

            total_driven = 4 + 1 + 6 + 3  # 14 (the NULL-driver row is not driven)

            # The crimson belief really does only approve — so measure the windows it has.
            await recompute_belief_performance(ORIGIN, generation_windows())

            # --- the counterfactual at T ---
            r = await cf.what_if_invalidated_at(ORIGIN, T)
            assert r is not None
            assert r["total_belief_driven"] == total_driven, r
            assert r["affected_decisions"] == 9, r             # 6 + 3 after T
            assert r["withdrawn_approvals"] == 9, r            # this belief approved all 9
            assert r["withdrawn_blocks"] == 0, r               # ...and blocked none
            assert r["frauds_auto_approved"] == 5, r           # 3 + 2
            assert r["frauds_caught_by_block"] == 0, r         # it caught nothing; it never blocks
            assert r["affected_holder_count"] == 2, r          # crimson-7 + crimson-5b
            assert set(r["affected_holders"]) == {A7, A5B}, r

            # per-window breakdown sums to the summary, over the belief's OWN measured windows
            assert r["windows"] is not None
            assert sum(w["withdrawn_approvals"] for w in r["windows"]) == 9, r["windows"]
            assert sum(w["frauds_auto_approved"] for w in r["windows"]) == 5, r["windows"]

            # --- extreme 1: T before the earliest decision => the full driven set ---
            r_all = await cf.what_if_invalidated_at(ORIGIN, BASE - dt.timedelta(days=800))
            assert r_all["withdrawn_approvals"] == total_driven, r_all
            assert r_all["affected_decisions"] == r_all["total_belief_driven"], r_all
            assert r_all["frauds_auto_approved"] == 1 + 3 + 2, r_all  # before + after_a + after_b

            # --- extreme 2: T after the latest decision => an empty set ---
            r_none = await cf.what_if_invalidated_at(ORIGIN, BASE - dt.timedelta(days=1))
            assert r_none["withdrawn_approvals"] == 0, r_none
            assert r_none["frauds_auto_approved"] == 0, r_none
            assert r_none["affected_holders"] == [], r_none

            # --- unknown belief => None (router maps to 404) ---
            assert await cf.what_if_invalidated_at(uuid.uuid4(), T) is None
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_a_blocking_belief_is_not_reported_as_approving():
    """THE REGRESSION. A belief that BLOCKS fraud must never be reported as having APPROVED it.

    This is the defect the grounding seam exposed, reproduced on a controlled belief that blocks
    exactly the way the azure laundering belief does (MATCH -> `blocked`). Against the pre-G4
    service — `withdrawn_approvals = count(*)`, `frauds_auto_approved = count(*) FILTER (is_fraud)`
    — this test fails on BOTH counts: it would report 8 withdrawn approvals (3 of them blocks) and
    3 auto-approved frauds that were in fact CAUGHT. The belief's correct catches would be
    presented as its harms.

    It also pins `windows: None` for a belief with no measured belief_performance rows — the azure
    belief's real shape, and the reason eight zeros are no longer emitted.
    """
    async def _run():
        try:
            await run_seed()  # both beliefs exist; decisions + belief_performance empty

            T = BASE - dt.timedelta(days=300)
            after = BASE - dt.timedelta(days=250)

            # 5 approvals (2 fraud — the real harm) + 3 blocks (3 fraud — real CATCHES).
            rows = [_row(AZURE_7, after, fraud=(i < 2), belief=AML_BELIEF) for i in range(5)]
            rows += [
                _row(AZURE_7, after, fraud=True, belief=AML_BELIEF, verdict="blocked")
                for _ in range(3)
            ]
            async with engine.begin() as c:
                await c.execute(insert(Decision), rows)

            r = await cf.what_if_invalidated_at(AML_BELIEF, T)
            assert r is not None

            assert r["affected_decisions"] == 8, r        # every driven row after T
            assert r["withdrawn_approvals"] == 5, r       # ONLY the approvals. NOT 8.
            assert r["withdrawn_blocks"] == 3, r          # the blocks, counted as blocks

            # The load-bearing assertion: the 3 blocked frauds were CAUGHT, not auto-approved.
            assert r["frauds_auto_approved"] == 2, r      # NOT 5 — the blocked frauds are excluded
            assert r["frauds_caught_by_block"] == 3, r    # invalidating would FORFEIT these

            # No belief_performance rows for this belief => no fabricated window grid.
            assert r["windows"] is None, r["windows"]
        finally:
            await engine.dispose()

    asyncio.run(_run())
