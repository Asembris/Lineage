"""Reversible-deterministic replay of a belief's inheritance closure (Roadmap Item 2).

Reconstructs the full inheritance CLOSURE of ONE belief — the genealogy nodes plus the
inheritance edges that carry it — AS OF an arbitrary past point in CockroachDB's MVCC
history, and emits a canonical, content-hashed snapshot. Real time-travel: the same
transaction-scoped `SET TRANSACTION AS OF SYSTEM TIME` mechanism proven by the deposition
path (app/services/time_travel.py), here over the belief-closure recursive CTE proven by
the lineage path (app/services/lineage.py). The live /lineage endpoint is deliberately
LEFT UNTOUCHED (current-state, unfiltered — the frontend Trace contract); this is a
separate, dedicated replay surface.

Scope + honesty (see NOTES "Roadmap Item 2"):
  * BOUNDED to the AOST-reachable window. Raw AS OF SYSTEM TIME cannot read older than the
    range `gc.ttlseconds` (~75 min on this cluster); an out-of-window `as_of` fails inside
    CRDB and is surfaced as a 400, never a 500 — the SAME translation the deposition uses.
    The roadmap's "any timestamp" means "any timestamp WITHIN the window"; durability past
    the window is the certificate's self-contained, hash-covered snapshot pattern (already
    proven for invalidation events), NOT raw replay.
  * BYTE-IDENTICAL as a falsifiable claim: `content_hash` is sha256 over the canonical
    (sorted-key) JSON of the RECONSTRUCTED WORLD (belief + closure) only — not the input
    `as_of` nor the resolved read HLC. Two independent reads at the same timestamp reproduce
    the same world => the same hash (tests/test_replay.py).
  * The world-builder and the digest come from app/services/certificate.py, NOT from a local
    copy. Item 2 deliberately kept them local ("coupling them buys nothing"); Item 6 REVERSED
    that, because the certifier Lambda now re-derives this exact hash on independent compute
    and compares it against the one the certificate embeds. Two canonicalizers that merely
    happen to agree today would make that comparison a false guarantee. See NOTES.md Item 6.

Why per-belief-closure is the right granularity (foundational for later items B and D):
  A belief IS the unit of inheritance and the unit of invalidation — belief_inheritance
  edges are keyed by belief_id, and the atomic kill-shot closes exactly one belief's closure.
  B (counterfactual "what-if invalidation") and D (confidence propagation) both operate on a
  single belief's chain, so a per-belief closure-as-of-T is precisely their input, not an
  artifact of today's single-belief seed. See NOTES for the multi-belief contract note.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import engine
from app.services.certificate import canonical_digest, closure_world
from app.services.time_travel import _AOST_RANGE_ERRORS, normalize_as_of

# Same recursive belief-closure traversal as app/services/lineage.py, with the per-edge
# revocation column (bi.invalidated_at) added: the closure-as-of-T must carry whether each
# holder edge was open or revoked at T, which is exactly what item B replays a hypothetical
# invalidation over. ORDER BY (depth, generation, agent_id) is a TOTAL order (agent_id is a
# unique UUID), which the byte-identical claim requires.
_CLOSURE_SQL = text(
    """
    WITH RECURSIVE chain AS (
        -- anchor: the originating agent (root of the closure, depth 0, no inbound edge)
        SELECT a.id AS agent_id, a.generation, a.bloodline, a.status,
               CAST(NULL AS UUID) AS from_agent_id,
               CAST(NULL AS TIMESTAMPTZ) AS inherited_at,
               CAST(NULL AS TIMESTAMPTZ) AS edge_invalidated_at,
               0 AS depth
        FROM agents a
        WHERE a.id = :origin_id
        UNION ALL
        -- recurse: follow THIS belief's inheritance edges from held agents to heirs
        SELECT a.id, a.generation, a.bloodline, a.status,
               bi.from_agent_id, bi.inherited_at, bi.invalidated_at, chain.depth + 1
        FROM belief_inheritance bi
        JOIN chain ON bi.from_agent_id = chain.agent_id
        JOIN agents a ON a.id = bi.to_agent_id
        WHERE bi.belief_id = :belief_id
    )
    SELECT depth, agent_id, generation, bloodline, status,
           from_agent_id, inherited_at, edge_invalidated_at
    FROM chain
    ORDER BY depth, generation, agent_id
    """
)

_BELIEF_SQL = text(
    "SELECT id, rule_text, status, originating_agent_id, formed_at, invalidated_at "
    "FROM beliefs WHERE id = :id"
)


async def closure_snapshot(belief_id: uuid.UUID, as_of: str | None = None) -> dict | None:
    """Reconstruct a belief's inheritance closure AS OF `as_of`, canonically hashed.

    Returns {as_of, read_hlc, content_hash, belief, origin_agent_id, closure} or None if the
    belief does not exist. `as_of` is an ISO-8601 timestamp or CRDB HLC decimal (validated by
    normalize_as_of); omit it for a current-state snapshot. Raises ValueError on a malformed
    or out-of-window `as_of` (the router maps it to 400). The belief lookup and the closure
    traversal run in ONE explicit transaction, so both read the SAME MVCC snapshot.
    """
    ts_literal = normalize_as_of(as_of) if as_of else None
    try:
        async with engine.connect() as conn:
            async with conn.begin():  # one explicit txn on this single physical connection
                if ts_literal is not None:
                    # MUST be the first statement in the transaction. Only the (validated)
                    # timestamp is inlined; the SELECTs below stay fully parameterized.
                    await conn.execute(
                        text(f"SET TRANSACTION AS OF SYSTEM TIME {ts_literal}")
                    )
                read_hlc = (
                    await conn.execute(text("SELECT cluster_logical_timestamp()::string"))
                ).scalar_one()
                belief = (
                    await conn.execute(_BELIEF_SQL, {"id": belief_id})
                ).mappings().first()
                if belief is None:
                    return None
                origin_id = belief["originating_agent_id"]
                rows = (
                    await conn.execute(
                        _CLOSURE_SQL, {"origin_id": origin_id, "belief_id": belief_id}
                    )
                ).mappings().all()
    except DBAPIError as e:
        # A well-formed but out-of-window timestamp (older than the GC TTL / in the future)
        # fails inside CRDB at SET TRANSACTION AS OF SYSTEM TIME. Surface it as a 400
        # (caller maps ValueError -> 400), not an unhandled 500. Mirrors time_travel.py.
        detail = str(getattr(e, "orig", None) or e).lower()
        if ts_literal is not None and any(s in detail for s in _AOST_RANGE_ERRORS):
            raise ValueError(
                f"as_of {as_of!r} is outside the time-travel window "
                "(older than the GC TTL or in the future)"
            ) from e
        raise

    # The reconstructed world — everything the hash covers. `as_of` (an INPUT) and `read_hlc`
    # (provenance) are deliberately NOT hashed: the claim is that the reconstructed CLOSURE at
    # a given time is byte-identical, and the world at two equal timestamps is the same world.
    world = closure_world(belief, rows)
    return {
        "as_of": as_of,
        "read_hlc": read_hlc,
        "content_hash": canonical_digest(world),
        **world,
    }
