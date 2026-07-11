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

THE SUBSTITUTION RULE (sourced from _decision_from, not invented)
-----------------------------------------------------------------
The belief's entire behaviour is one branch of the backfill policy: an on-pattern txn
(mcc 5411, <$180, age>6mo) gets `verdict='approve'` AND is the SOLE driver
(`driving_belief_id = origin`). Every other txn takes the generic path with
`driving_belief_id = NULL`. Two consequences decide the whole design:

  1. The belief ONLY EVER APPROVES. It never declines or blocks. So invalidating it can only
     REMOVE approvals; it can never create a decline and can never flip a NULL-driver decision.
     The counterfactually-affected set is therefore exactly, and unambiguously,
         { decisions : driving_belief_id = X AND decided_at > T }
     — every row of which is an approval.
  2. There is NO faithful per-row "what the generic path would have said instead". The generic
     branch is stochastic AND its RNG draw-count is branch-dependent, so re-deriving one row's
     fallback verdict would require re-running the whole seeded world with a restructured branch,
     which shifts every downstream draw. We therefore do NOT fabricate a replacement verdict.
     Each affected verdict changes from `approve (belief-driven)` to `approval withdrawn`.

The two quantities that ARE fully deterministic and forensically load-bearing:
    withdrawn_approvals (N) = |affected set|
    frauds_auto_approved (M) = of those, is_fraud = true  (real ground truth; the SAME
                               frauds_approved discipline belief_performance already holds).
M is the honest, non-inflated "how much fraud an earlier invalidation would have stripped the
belief's justification from" — never "how much we would have caught" (the fallback is stochastic).

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
from app.sim.transactions import generation_windows


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

# The affected set: this belief's driven decisions strictly after T. Every such row is an
# approval (the belief only ever approves), so withdrawn_approvals == the row count; the
# approvals tally is selected too, purely so the caller/test can assert that invariant holds
# in the real data rather than trust the prose.
_AFFECTED_SQL = text(
    """
    SELECT
      count(*)                                    AS withdrawn_approvals,
      count(*) FILTER (WHERE is_fraud)            AS frauds_auto_approved,
      count(*) FILTER (WHERE verdict = 'approve') AS approvals,
      count(DISTINCT agent_id)                    AS holder_count
    FROM decisions
    WHERE driving_belief_id = :b AND decided_at > :t
    """
)

_HOLDERS_SQL = text(
    "SELECT DISTINCT agent_id FROM decisions "
    "WHERE driving_belief_id = :b AND decided_at > :t ORDER BY agent_id"
)

# Per-window slice of the affected set — each generation window intersected with decided_at > T.
# Buckets identically to belief_performance (both use generation_windows()), so the counterfactual
# lines up against the staleness curve: the windows where confidence had already rotted are exactly
# the ones contributing the fraud.
_WINDOW_SQL = text(
    """
    SELECT
      count(*)                         AS withdrawn_approvals,
      count(*) FILTER (WHERE is_fraud) AS frauds_auto_approved
    FROM decisions
    WHERE driving_belief_id = :b
      AND decided_at > :t
      AND decided_at >= :start AND decided_at < :end
    """
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

            windows: list[dict] = []
            for start, end in generation_windows():
                w = (
                    await conn.execute(
                        _WINDOW_SQL,
                        {"b": belief_id, "t": at, "start": start, "end": end},
                    )
                ).mappings().one()
                windows.append(
                    {
                        "window_start": start,
                        "window_end": end,
                        "withdrawn_approvals": int(w["withdrawn_approvals"]),
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
        "withdrawn_approvals": int(summary["withdrawn_approvals"]),
        "frauds_auto_approved": int(summary["frauds_auto_approved"]),
        "approvals": int(summary["approvals"]),
        "affected_holder_count": int(summary["holder_count"]),
        "affected_holders": holders,
        "windows": windows,
    }
