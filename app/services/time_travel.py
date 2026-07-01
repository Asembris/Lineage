"""AS OF SYSTEM TIME deposition — the real CockroachDB time-travel path.

The AOST timestamp CANNOT be a bind parameter in CockroachDB; it must be inlined in the
SQL text. So we normalize the caller's `as_of` ourselves (the raw request string never
reaches SQL) and issue `SET TRANSACTION AS OF SYSTEM TIME <literal>` as the FIRST statement
of one explicit transaction, on a single physical connection, then run a normally-
parameterized SELECT. Verified against v25.4.10 (see scripts/probe_crdb.py).
"""

from __future__ import annotations

import datetime as dt
import re
import uuid

from sqlalchemy import text

from app.db import engine

# CRDB HLC decimal, as returned by cluster_logical_timestamp(), e.g. 178290...330.0000000000
_HLC = re.compile(r"^\d+(\.\d+)?$")


def normalize_as_of(as_of: str) -> str:
    """Return a SQL-safe AOST literal. Raises ValueError on anything unrecognized.

    Two accepted forms:
      * HLC decimal  -> used bare (unquoted), exact.
      * ISO-8601 ts  -> parsed to a datetime, then RE-SERIALIZED and quoted. The raw
                        input never reaches the SQL string, so there is no injection surface.
    """
    s = as_of.strip()
    if _HLC.match(s):
        return s
    try:
        parsed = dt.datetime.fromisoformat(s)
    except ValueError as e:
        raise ValueError(f"invalid as_of {as_of!r}: expected ISO-8601 or HLC decimal") from e
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return "'" + parsed.isoformat(sep=" ") + "'"


_BELIEFS_SQL = text(
    """
    SELECT DISTINCT b.id, b.rule_text, b.status,
           b.originating_agent_id, b.formed_at
    FROM beliefs b
    LEFT JOIN belief_inheritance bi
           ON bi.belief_id = b.id AND bi.to_agent_id = :agent_id
    WHERE b.originating_agent_id = :agent_id OR bi.to_agent_id = :agent_id
    ORDER BY b.formed_at
    """
)


async def get_agent(agent_id: uuid.UUID) -> dict | None:
    """Current-time agent lookup (for the response envelope / 404)."""
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT id, generation, bloodline, status FROM agents WHERE id = :id"
                ),
                {"id": agent_id},
            )
        ).mappings().first()
    return dict(row) if row else None


async def beliefs_held_by_agent(
    agent_id: uuid.UUID, as_of: str | None
) -> list[dict]:
    """Beliefs an agent holds (originated or inherited), optionally AS OF a past time."""
    ts_literal = normalize_as_of(as_of) if as_of else None
    async with engine.connect() as conn:
        async with conn.begin():  # one explicit txn on this single physical connection
            if ts_literal is not None:
                # MUST be the first statement in the transaction. Only the (validated)
                # timestamp is inlined; the SELECT below stays fully parameterized.
                await conn.execute(
                    text(f"SET TRANSACTION AS OF SYSTEM TIME {ts_literal}")
                )
            rows = (
                await conn.execute(_BELIEFS_SQL, {"agent_id": agent_id})
            ).mappings().all()
    return [dict(r) for r in rows]
