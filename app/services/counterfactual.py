"""Counterfactual "what-if invalidation" over the moat's decisions (Roadmap Item B).

THE QUESTION, RESOLVED AGAINST REAL DATA
----------------------------------------
"If belief X had been invalidated at time T, which downstream verdicts change?" There are
two verdict-producing paths in this system, and only one can answer it honestly:

  * the moat's `decisions` table — driven by `driving_belief_id` and the deterministic
    backfill policy (app/sim/transactions.py + seed/backfill_decisions.py), and
  * the AML agent's FLAG/NO_FLAG/INSUFFICIENT_COVERAGE brake (Items 4/5), which has NEVER
    touched a belief-driven decision — `decisions.aml_transaction_id` does not exist, and the
    one belief is a card-authorization heuristic the AML money-flow graph never carried.

So "which verdicts change" is answerable ONLY against `decisions`, the sole place a real
`driving_belief_id -> beliefs.id` link exists. This service reads that table.

THE SUBSTITUTION RULE (sourced from the backfills, not invented)
----------------------------------------------------------------
  1. There is NO faithful per-row "what would have been decided instead". For the crimson belief
     the generic branch is stochastic AND its RNG draw-count is branch-dependent, so re-deriving
     one row's fallback verdict would require re-running the whole seeded world with a restructured
     branch, which shifts every downstream draw. For the azure belief there is no fallback rule at
     all. We therefore do NOT fabricate a replacement verdict: an affected decision simply loses
     its justification.
  2. The affected set is exactly, and unambiguously,
         { decisions : driving_belief_id = X AND decided_at > T }

====================================================================================
THE "BELIEF ONLY EVER APPROVES" INVARIANT IS DEAD. It was true of the crimson belief and
it is FALSE of the fleet. Read this before touching the aggregates.
====================================================================================
Item B was built when exactly one belief existed, and that belief's entire behaviour was one
branch of `seed/backfill_decisions.py::_decision_from`: an on-pattern txn -> `verdict='approve'`,
and nothing else was ever belief-driven. So the service (correctly, then) treated the affected
COUNT as the withdrawn-APPROVAL count, and `test_counterfactual` asserted
`withdrawn_approvals == approvals` against real data.

The grounding seam's azure belief **BLOCKS** (`aml_seam.VERDICT_FOR`: a re-derived cycle ->
`blocked`; 57 of its 1,500 decisions). Under the old aggregate the endpoint reported those blocks
as withdrawn approvals, and — far worse — counted the 43 laundering rows the belief CORRECTLY
BLOCKED as `frauds_auto_approved`. **It credited the belief's 43 correct catches as 43 fraud
approvals: a forensic tool stating the exact opposite of what happened, in the most damaging
possible direction.** Measured on a live probe before the fix (30 rows: 6 blocked / 24 approved)
the endpoint returned `withdrawn_approvals: 30, frauds_auto_approved: 6` — where all 6 of those
frauds had been blocked.

So the aggregates are now VERDICT-AWARE, and the vocabulary is split rather than conflated:
    withdrawn_approvals  (N) = affected rows the belief APPROVED  -> the approval loses its driver
    withdrawn_blocks         = affected rows the belief BLOCKED   -> the block loses its driver
    frauds_auto_approved (M) = of the APPROVED rows, is_fraud     -> the real harm
    frauds_caught_by_block   = of the BLOCKED rows, is_fraud      -> what invalidation would forfeit
M is the honest, non-inflated "how much fraud an earlier invalidation would have stripped the
belief's justification from" — never "how much we would have caught" (no fallback is reproduced).
`frauds_caught_by_block` is its counterweight and exists so the counterfactual can never again
present a correct block as a harm.

THE PER-WINDOW BREAKDOWN IS BELIEF-SCOPED, AND IS NULL WHEN THE BELIEF HAS NO WINDOWS
-------------------------------------------------------------------------------------
The breakdown used to bucket by `generation_windows()` — the CRIMSON generation clock (window 0
opens 2024-05-12; window 7 closes 2026-06-30). The azure belief's decisions all carry ONE fixed
`decided_at` (2026-07-12, deliberately — it is what makes the base-rate mirage structurally
unavailable; see `seed/backfill_aml_decisions.py`), which falls OUTSIDE all eight. Measured: the
breakdown came back `[0,0,0,0,0,0,0,0]` while `withdrawn_approvals` was non-zero — eight zeros
silently contradicting the headline.

Windows are therefore taken from the belief's OWN `belief_performance` rows, and a belief with no
measured windows gets `windows: null` — an explicit "this belief has no measured time structure",
never a fabricated grid of zeros. This is the same honest-absence the staleness block already
emits (`certificate.staleness_evidence` -> `available: false`) and the same reason the azure
belief has no `belief_performance` at all.

WHY THIS IS A PLAIN QUERY, NOT AOST / replay.closure_snapshot() (a deliberate rejection)
----------------------------------------------------------------------------------------
T is a `decided_at` / `belief_performance.window_start` BUSINESS-TIME instant (~400 days ago),
NOT an MVCC timestamp. `AS OF SYSTEM TIME` time-travels DATABASE STATE and is bounded by the
75-min GC TTL — a T ~400 days ago is both the wrong clock (the "two clocks" the project never
conflates) and out-of-window (the backfilled rows were all INSERTED at seed time, 2026-07;
they never existed in MVCC history at T). Reusing `closure_snapshot(X, as_of=T)` would be a
category error. The affected set needs no closure reconstruction at all: every belief-driven
decision already carries `driving_belief_id` and `decided_at`. This is a plain, deterministic
WHERE over immutable columns. No content-hash either: hash-coverage is load-bearing only when a
second party re-derives and compares it (Item 6's certifier); there is none here, and the
`decisions` inputs are already reproducible (deterministic backfill; a real invalidation never
touches `decisions` rows). See NOTES "Roadmap Item B".

Read-only. No migration, no new table, no AML read, no LLM call, no AOST. `engine` is injectable
(defaults to the app global, defaultdb) so a done-test can run against controlled data.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db import engine as _DEFAULT_ENGINE

# NOTE: `app.sim.transactions.generation_windows()` is deliberately NO LONGER imported. It is the
# CRIMSON generation clock, and bucketing every belief by it produced eight silent zeros for a
# belief whose decisions fall outside those windows. Windows now come from the belief's own
# `belief_performance` rows. Do not reintroduce it here.


def parse_at(at: str) -> dt.datetime:
    """Parse the counterfactual instant `at` (business-time). Raises ValueError (router -> 400).

    Accepts an ISO-8601 date ('2025-05-27') or datetime; a naive value is read as UTC. This is
    a normal bind parameter (unlike the AOST `as_of`, which must be inlined) — the parsed
    datetime is passed parameterized, so there is no injection surface and no SQL-literal step.
    """
    try:
        parsed = dt.datetime.fromisoformat(at.strip())
    except ValueError as e:
        raise ValueError(
            f"invalid at {at!r}: expected an ISO-8601 date or datetime"
        ) from e
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


_BELIEF_SQL = text(
    "SELECT id, rule_text, status, originating_agent_id, formed_at, invalidated_at "
    "FROM beliefs WHERE id = :id"
)

# The affected set: this belief's driven decisions strictly after T, SPLIT BY VERDICT.
#
# Every count here is FILTERED on the verdict, and that is the whole point of the fix. A bare
# `count(*)` labelled `withdrawn_approvals` was true only while the one belief never blocked
# anything; against the azure belief it reported blocks as approvals and, worse, reported
# correctly-blocked fraud as auto-approved fraud. See the module docstring.
_AFFECTED_SQL = text(
    """
    SELECT
      count(*)                                                     AS affected,
      count(*) FILTER (WHERE verdict = 'approve')                  AS withdrawn_approvals,
      count(*) FILTER (WHERE verdict IN ('decline', 'blocked'))    AS withdrawn_blocks,
      count(*) FILTER (WHERE is_fraud AND verdict = 'approve')     AS frauds_auto_approved,
      count(*) FILTER (WHERE is_fraud
                         AND verdict IN ('decline', 'blocked'))    AS frauds_caught_by_block,
      count(DISTINCT agent_id)                                     AS holder_count
    FROM decisions
    WHERE driving_belief_id = :b AND decided_at > :t
    """
)

_HOLDERS_SQL = text(
    "SELECT DISTINCT agent_id FROM decisions "
    "WHERE driving_belief_id = :b AND decided_at > :t ORDER BY agent_id"
)

# Per-window slice of the affected set. The windows come from the belief's OWN belief_performance
# rows (NOT generation_windows(), which is the crimson generation clock — see the module
# docstring), so the breakdown lines up against exactly the staleness curve this belief actually
# has. A belief with no measured windows gets no breakdown at all, rather than a grid of zeros.
_WINDOW_SQL = text(
    """
    SELECT
      count(*) FILTER (WHERE verdict = 'approve')               AS withdrawn_approvals,
      count(*) FILTER (WHERE verdict IN ('decline', 'blocked')) AS withdrawn_blocks,
      count(*) FILTER (WHERE is_fraud AND verdict = 'approve')  AS frauds_auto_approved
    FROM decisions
    WHERE driving_belief_id = :b
      AND decided_at > :t
      AND decided_at >= :start AND decided_at < :end
    """
)

# The belief's own measured windows. Empty => this belief has no measured time structure, and the
# counterfactual says so (windows: null) instead of inventing one.
_PERF_WINDOWS_SQL = text(
    "SELECT window_start, window_end FROM belief_performance "
    "WHERE belief_id = :b ORDER BY window_start"
)

_TOTAL_SQL = text("SELECT count(*) FROM decisions WHERE driving_belief_id = :b")


async def what_if_invalidated_at(
    belief_id: uuid.UUID,
    at: dt.datetime,
    engine: AsyncEngine | None = None,
) -> dict | None:
    """Counterfactual: had `belief_id` been invalidated at `at`, which verdicts change?

    Returns the affected-set summary (see the module docstring for the substitution rule), or
    None if the belief does not exist (router -> 404). Read-only. The belief lookup and every
    aggregate run in ONE explicit transaction, so they share a single MVCC snapshot (no torn
    read between the belief row and the decision aggregates). `at` before the belief's formation
    yields the full driven set; `at` after the last decision yields an empty set — both fall out
    of the plain WHERE with no special-casing.
    """
    eng = engine if engine is not None else _DEFAULT_ENGINE
    async with eng.connect() as conn:
        async with conn.begin():
            belief = (
                await conn.execute(_BELIEF_SQL, {"id": belief_id})
            ).mappings().first()
            if belief is None:
                return None

            total_driven = (
                await conn.execute(_TOTAL_SQL, {"b": belief_id})
            ).scalar_one()

            summary = (
                await conn.execute(_AFFECTED_SQL, {"b": belief_id, "t": at})
            ).mappings().one()

            holders = [
                r["agent_id"]
                for r in (
                    await conn.execute(_HOLDERS_SQL, {"b": belief_id, "t": at})
                ).mappings().all()
            ]

            # The belief's OWN windows. No measured windows => no breakdown (None, not zeros).
            perf = (
                await conn.execute(_PERF_WINDOWS_SQL, {"b": belief_id})
            ).mappings().all()

            windows: list[dict] | None = None
            if perf:
                windows = []
                for p in perf:
                    w = (
                        await conn.execute(
                            _WINDOW_SQL,
                            {
                                "b": belief_id,
                                "t": at,
                                "start": p["window_start"],
                                "end": p["window_end"],
                            },
                        )
                    ).mappings().one()
                    windows.append(
                        {
                            "window_start": p["window_start"],
                            "window_end": p["window_end"],
                            "withdrawn_approvals": int(w["withdrawn_approvals"]),
                            "withdrawn_blocks": int(w["withdrawn_blocks"]),
                            "frauds_auto_approved": int(w["frauds_auto_approved"]),
                        }
                    )

    return {
        "belief_id": belief["id"],
        "rule_text": belief["rule_text"],
        "belief_status": belief["status"],
        "originating_agent_id": belief["originating_agent_id"],
        "formed_at": belief["formed_at"],
        "at": at,
        "total_belief_driven": int(total_driven),
        "affected_decisions": int(summary["affected"]),
        "withdrawn_approvals": int(summary["withdrawn_approvals"]),
        "withdrawn_blocks": int(summary["withdrawn_blocks"]),
        "frauds_auto_approved": int(summary["frauds_auto_approved"]),
        "frauds_caught_by_block": int(summary["frauds_caught_by_block"]),
        "affected_holder_count": int(summary["holder_count"]),
        "affected_holders": holders,
        # None (not []) when the belief has no measured windows — an explicit "no time structure",
        # never a fabricated grid of zeros. See the module docstring.
        "windows": windows,
    }
