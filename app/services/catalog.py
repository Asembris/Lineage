"""Read-only catalog queries backing the frontend's list views.

Three reusable list endpoints (see AUDIT.md §8 — no read route existed for these):
  * list_agents      -> GET /agents      : the full genealogy (tree nodes + parent edges)
  * list_decisions   -> GET /decisions   : the fleet-wide decision feed, paginated + filterable
  * list_beliefs     -> GET /beliefs      : beliefs to investigate

Current-state reads only (no AS OF SYSTEM TIME here — that is the deposition path). Raw SQL on
`engine.connect()`, matching lineage.py / time_travel.py. Filters are always bind parameters;
nothing from the request is interpolated into SQL text.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text

from app.db import engine
from app.services import certificate
from app.services.aml_seam import witness_outcome_of


async def list_agents(
    bloodline: str | None = None, status: str | None = None
) -> list[dict]:
    """The full genealogy (optionally filtered), ordered for stable tree rendering."""
    clauses: list[str] = []
    params: dict = {}
    if bloodline is not None:
        clauses.append("bloodline = :bloodline")
        params["bloodline"] = bloodline
    if status is not None:
        clauses.append("status = :status")
        params["status"] = status
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = text(
        "SELECT id, generation, bloodline, status, spawned_at, retired_at, parent_id "
        "FROM agents" + where + " ORDER BY bloodline, generation, id"
    )
    async with engine.connect() as conn:
        rows = (await conn.execute(sql, params)).mappings().all()
    return [dict(r) for r in rows]


async def list_decisions(
    agent_id: uuid.UUID | None = None,
    *,
    aml_transaction_id: uuid.UUID | None = None,
    driving_belief_id: uuid.UUID | None = None,
    kind: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """A page of the decision feed + the total row count (for pagination). Filters AND together.

    `agent_id`           — one agent's history (the investigate flow). Omit for the fleet-wide feed.
    `aml_transaction_id` — THE REVERSE LOOKUP. See below.
    `driving_belief_id`  — every decision one belief drove.
    `kind`               — 'aml' | 'card'. The two kinds `ck_decisions_kind` makes structural.

    THE REVERSE LOOKUP is the one direction the seam did not resolve. The FK runs
    decisions -> aml_transactions, so a decision resolves its money-flow edge in one hop; the
    reverse ("I am looking at a flagged AML transaction — did any agent act on it, and what did it
    decide?") had no access path and no route. Migration 0008 adds the partial index that makes it
    a point lookup instead of the FULL SCAN it was (verified with EXPLAIN, not assumed).

    `driving_belief_id` and `kind` exist because the AML decisions were previously discoverable only
    by ACCIDENT, and neither accident is a guarantee (both are facts about this seed):
      (a) they all share one fixed `decided_at` NEWER than every card decision, so the newest-first
          feed happens to put them on page 1;
      (b) azure-7 happens to make ONLY AML decisions, so `?agent_id=azure-7` happens to isolate them.
    A future session that moves the seam's `decided_at` or gives azure-7 a card decision would break
    discoverability silently. These filters are what make that survivable. See NOTES "G5".

    Newest first. Every filter is a bind parameter; nothing is interpolated into SQL text.
    """
    clauses: list[str] = []
    params: dict = {}
    if agent_id is not None:
        clauses.append("agent_id = :agent_id")
        params["agent_id"] = agent_id
    if aml_transaction_id is not None:
        clauses.append("aml_transaction_id = :aml_txn")
        params["aml_txn"] = aml_transaction_id
    if driving_belief_id is not None:
        clauses.append("driving_belief_id = :belief_id")
        params["belief_id"] = driving_belief_id
    if kind == "aml":
        clauses.append("aml_transaction_id IS NOT NULL")
    elif kind == "card":
        clauses.append("aml_transaction_id IS NULL")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    async with engine.connect() as conn:
        total = (
            await conn.execute(
                text("SELECT count(*) FROM decisions" + where), params
            )
        ).scalar_one()
        page = (
            await conn.execute(
                text(
                    "SELECT id, agent_id, txn_ref, merchant, amount, amount_currency, verdict, "
                    "driving_belief_id, confidence, decided_at, is_fraud, aml_transaction_id "
                    "FROM decisions" + where
                    + " ORDER BY decided_at DESC, id DESC LIMIT :limit OFFSET :offset"
                ),
                {**params, "limit": limit, "offset": offset},
            )
        ).mappings().all()

    # The BASIS, promoted from a string prefix to a first-class field. `txn_ref` already carried the
    # witness outcome (`aml:INCONCLUSIVE`), but only as an undocumented convention inside a column
    # named "the transaction reference" — so an API caller saw 1,443 approvals and could not tell
    # that 980 of them are "we could not tell", not "we checked and it is clean". That is the single
    # most important caveat about this belief and it was reachable only by someone who already knew
    # to look. Now it is a field. It is PROJECTED from the persisted txn_ref (never re-derived from
    # the graph), so it reports what the agent RECORDED, not what the graph would say today.
    return [dict(r) | {"witness_outcome": witness_outcome_of(r["txn_ref"])} for r in page], int(total)


async def list_belief_performance(belief_id: uuid.UUID) -> dict | None:
    """The measured staleness curve for one belief, WITH its uncertainty.

    Returns None if the belief does not exist (router → 404). Otherwise returns the SHARED
    `certificate.staleness_evidence` block — a (possibly empty) ordered window list plus, when
    the samples are trustworthy, each window's size and 95% Wilson interval. An empty list is
    honest ("no measured windows yet"), not a 404. Windows are generation-ordered by
    window_start; the caller reads first vs. last as "valid then / rotten now".

    It reads through certificate.py's SQL and builder ON PURPOSE. This endpoint and the
    certificate must never be able to state different intervals for the same belief — the
    console would then contradict the hash-covered document justifying the invalidation. One
    instrument, two consumers (the same reason the faithfulness rubric is shared between the
    live guard and the offline eval).

    Current-state read (no AOST — belief_performance is app-level measured data on a different
    clock than MVCC time-travel; see NOTES §"Time concepts").
    """
    async with engine.connect() as conn:
        exists = (
            await conn.execute(
                text("SELECT 1 FROM beliefs WHERE id = :b"), {"b": belief_id}
            )
        ).first()
        if exists is None:
            return None
        rows = (
            await conn.execute(
                text(certificate._STALENESS_SQL), {"b": belief_id}
            )
        ).mappings().all()
    return certificate.staleness_evidence(rows)


async def list_beliefs(status: str | None = None) -> list[dict]:
    """Beliefs to investigate (optionally filtered by status), oldest first."""
    where = " WHERE status = :status" if status is not None else ""
    params: dict = {}
    if status is not None:
        params["status"] = status
    sql = text(
        "SELECT id, rule_text, status, originating_agent_id, formed_at, invalidated_at "
        "FROM beliefs" + where + " ORDER BY formed_at, id"
    )
    async with engine.connect() as conn:
        rows = (await conn.execute(sql, params)).mappings().all()
    return [dict(r) for r in rows]
