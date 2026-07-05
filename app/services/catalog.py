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
    agent_id: uuid.UUID | None = None, *, limit: int = 50, offset: int = 0
) -> tuple[list[dict], int]:
    """A page of the decision feed + the total row count (for pagination).

    No `agent_id` => the fleet-wide feed across all agents (the frontend's decision panel).
    With `agent_id` => that one agent's history (the investigate flow). Newest first.
    """
    where = " WHERE agent_id = :agent_id" if agent_id is not None else ""
    params: dict = {}
    if agent_id is not None:
        params["agent_id"] = agent_id

    async with engine.connect() as conn:
        total = (
            await conn.execute(
                text("SELECT count(*) FROM decisions" + where), params
            )
        ).scalar_one()
        page = (
            await conn.execute(
                text(
                    "SELECT id, agent_id, txn_ref, merchant, amount, verdict, "
                    "driving_belief_id, confidence, decided_at, is_fraud "
                    "FROM decisions" + where
                    + " ORDER BY decided_at DESC, id DESC LIMIT :limit OFFSET :offset"
                ),
                {**params, "limit": limit, "offset": offset},
            )
        ).mappings().all()
    return [dict(r) for r in page], int(total)


async def list_belief_performance(belief_id: uuid.UUID) -> list[dict] | None:
    """The ordered belief_performance windows for one belief — the measured staleness curve.

    Returns None if the belief does not exist (router → 404). Returns a (possibly empty) list
    of windows if the belief exists: an empty list is honest ("no measured windows yet"), not a
    404. Windows are generation-ordered by window_start; the caller reads first vs. last as
    "valid then / rotten now". Current-state read (no AOST — belief_performance is app-level
    measured data on a different clock than MVCC time-travel; see NOTES §"Time concepts").
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
                text(
                    "SELECT window_start, window_end, confidence, "
                    "false_positive_rate, frauds_approved "
                    "FROM belief_performance WHERE belief_id = :b "
                    "ORDER BY window_start"
                ),
                {"b": belief_id},
            )
        ).mappings().all()
    return [dict(r) for r in rows]


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
