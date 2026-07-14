# READS NO LABEL COLUMN, and calls NO model. `aml_transactions.is_laundering` and the
# `aml_pattern_members` / `aml_pattern_instances` pair are the ANSWER KEY, not evidence — the
# same discipline app/services/aml_graph.py holds. They are read ONLY by tests, as a scoring
# oracle. See NOTES.md "Roadmap Item 4" and "Roadmap Item 5".
"""Click-to-interrogate over the AML money-flow graph (Roadmap Item 5).

Given one real `aml_transactions` row, this answers — deterministically, with no LLM and no
ground-truth lookup — what structure the graph actually witnesses around it, in what order,
and whether more than one typology witnesses it at once.

Three things this module exists to get right, each of which was measured before it was built:

1. **A transaction is an EDGE, not a node.** Accounts are the nodes of the money-flow graph
   (`aml_graph.Edge` is the transaction). So interrogation resolves BOTH: the subject and each
   witness member as real transaction rows, and the accounts they run between — including the
   `boundary_account` that an INCONCLUSIVE search names.

2. **A witness's id list carries no guaranteed traversal order.** `verify_witness_path` is
   order-insensitive by construction (it rebuilds the chain from a {src: edge} map), and on a
   FLAG `VerdictOutcome.witness_txn_ids` is the MODEL's list, not the graph's. So the order is
   RE-DERIVED here from the rows themselves; it is never read off the list.

3. **Only CYCLE has a linear order at all.** Measured over the live extract: all 57 CYCLE
   witnesses are contiguous AND closed (`last.dst == first.src`) — a closed RING, so every hop
   has a predecessor and the subject's predecessor is the final hop. SCATTER-GATHER's witness is
   two scatter legs leaving one source plus two gather legs entering one destination: two
   parallel two-hop routes, NOT a path. Asking for "the previous transaction" there is a
   category error, and `predecessor_of` reports that as its own status rather than a bare None.

Conflict is REPORTED here, never used to gate. See app/services/verdict_guard.py.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import engine
from app.services.aml_graph import (
    FLAG_CAPABLE,
    MIN_FANOUT,
    WITNESS,
    Check,
    Edge,
    Graph,
    Outcome,
    check,
    load_graph,
)
from app.services.time_travel import _AOST_RANGE_ERRORS, normalize_as_of


class TraversalKind(str, Enum):
    """What SHAPE a witness has — which determines whether 'previous hop' means anything."""

    RING = "RING"      # CYCLE: contiguous and closed. Predecessor is total (see probe above).
    LEGS = "LEGS"      # SCATTER-GATHER: labeled scatter[] + gather[]. Two parallel routes.
    BUNDLE = "BUNDLE"  # GATHER-SCATTER / STACK: a real edge set with no single traversal.
    NONE = "NONE"      # No witness (CONCLUSIVE_NO / INCONCLUSIVE).


class PredecessorStatus(str, Enum):
    """Why `predecessor_of` did or did not resolve a previous hop.

    A caller distinguishes the cases on this enum ALONE — never by parsing a reason string.

    Deliberately absent: a `FIRST_HOP` / "no predecessor" status. It is unreachable. A CYCLE
    witness is a CLOSED ring (verified 57/57 over the live extract, and locked by
    tests/test_aml_interrogate.py::test_cycle_witness_is_a_closed_ring_so_predecessor_is_total),
    so the subject's predecessor is the last hop and every member has exactly one. If a future
    ingestion ever produced an open CYCLE witness, that test fails loudly rather than this
    function silently inventing a fourth state.
    """

    RESOLVED = "RESOLVED"                  # A real previous transaction exists.
    NO_LINEAR_ORDER = "NO_LINEAR_ORDER"    # The witness is not a path (LEGS / BUNDLE).
    NOT_IN_WITNESS = "NOT_IN_WITNESS"      # The transaction is not part of this witness.


@dataclass(frozen=True)
class PredecessorResult:
    status: PredecessorStatus
    transaction_id: uuid.UUID | None = None

    @property
    def resolved(self) -> bool:
        return self.status is PredecessorStatus.RESOLVED


@dataclass(frozen=True)
class Witness:
    """One typology's verdict on one subject edge, with its traversal re-derived."""

    typology: str
    flag_capable: bool
    outcome: Outcome
    kind: TraversalKind
    transaction_ids: list[uuid.UUID]           # ordered iff kind is RING
    legs: dict[str, list[uuid.UUID]] | None    # populated iff kind is LEGS
    boundary_account_id: uuid.UUID | None
    detail: str

    @property
    def matched(self) -> bool:
        return self.outcome is Outcome.MATCH


# ---------------------------------------------------------------------------------------
# Traversal re-derivation. Never trusts the incoming list order.
# ---------------------------------------------------------------------------------------


def ring_order(g: Graph, txn_ids: list[uuid.UUID], subject: Edge) -> list[uuid.UUID] | None:
    """Order a CYCLE's edges into the closed walk that starts at `subject`.

    Returns None if the ids do not form one closed directed walk containing the subject — the
    same structural condition `verify_witness_path` enforces, re-derived rather than assumed.
    """
    if subject.id not in txn_ids:
        return None
    edges = [g.by_id[i] for i in txn_ids if i in g.by_id]
    if len(edges) != len(txn_ids):
        return None
    nxt = {e.src: e for e in edges}
    if len(nxt) != len(edges):  # an account sends twice — not a simple cycle
        return None
    chain: list[Edge] = []
    cur: Edge | None = subject
    for _ in range(len(edges)):
        if cur is None:
            return None
        chain.append(cur)
        cur = nxt.get(cur.dst)
    if len(chain) != len(edges) or chain[-1].dst != subject.src:
        return None
    return [e.id for e in chain]


def scatter_gather_legs(
    g: Graph, txn_ids: list[uuid.UUID], subject: Edge
) -> dict[str, list[uuid.UUID]] | None:
    """Split a SCATTER-GATHER witness into its two labeled legs.

    This is NOT a path and must never be presented as one: `scatter` are the edges leaving the
    one fan-out source, `gather` are the edges those intermediaries send onward into the common
    destination.

    THE SUBJECT IS USUALLY — BUT NOT ALWAYS — ONE OF THE SCATTER LEGS. This docstring previously
    claimed it always was. That is FALSE, measured over the live extract: it holds for 41 of the
    42 SCATTER-GATHER matches and fails for one, and the cause is worth knowing because it is the
    same cause that makes the geometry hard to draw honestly.

    THE MONEY-FLOW GRAPH IS A MULTIGRAPH: two distinct transactions can run between the same pair
    of accounts. `Graph.succ()` keys one edge per (src, dst) PAIR, so `scatter_gather_witness`
    cites `g.succ(u)[v]` — which, when the subject has a parallel twin, is the TWIN and not the
    subject. On `c98de429` the graph cites `586a923b` (the same two accounts, a different amount)
    and the subject is absent from its own witness.

    A caller must therefore never assume membership. `interrogate_transaction` resolves the
    subject into the `transactions` map unconditionally, and a renderer must mark the subject only
    when the witness actually cites it. (GATHER-SCATTER is far worse — it omits the subject in 75
    of its 107 matches, because its witness is truncated to MIN_FANOUT. See aml_graph.)
    """
    edges = [g.by_id[i] for i in txn_ids if i in g.by_id]
    if len(edges) != len(txn_ids):
        return None
    by_src: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for e in edges:
        by_src[e.src].add(e.dst)
    sources = [a for a, ds in by_src.items() if len(ds) >= MIN_FANOUT]
    if not sources:
        return None
    src = min(sources, key=str)  # deterministic when a witness has more than one fan-out
    scatter = sorted((e for e in edges if e.src == src), key=lambda x: str(x.id))
    mids = {e.dst for e in scatter}
    gather = sorted((e for e in edges if e.src in mids), key=lambda x: str(x.id))
    if not gather:
        return None
    return {"scatter": [e.id for e in scatter], "gather": [e.id for e in gather]}


def _build_witness(g: Graph, subject: Edge, typology: str, result: Check) -> Witness:
    kind, legs, ids = TraversalKind.NONE, None, []
    if result.outcome is Outcome.MATCH:
        ids = list(result.witness_txn_ids)
        if typology == "CYCLE":
            ordered = ring_order(g, ids, subject)
            # The graph's own cycle witness is emitted subject-first and closed, so `ordered`
            # is not None here. Falling back to BUNDLE keeps a hypothetical future witness
            # constructor from being silently mis-presented as an ordered path.
            kind, ids = (TraversalKind.RING, ordered) if ordered else (TraversalKind.BUNDLE, ids)
        elif typology == "SCATTER-GATHER":
            legs = scatter_gather_legs(g, ids, subject)
            kind = TraversalKind.LEGS if legs else TraversalKind.BUNDLE
        else:
            kind = TraversalKind.BUNDLE
    return Witness(
        typology=typology,
        flag_capable=typology in FLAG_CAPABLE,
        outcome=result.outcome,
        kind=kind,
        transaction_ids=ids,
        legs=legs,
        boundary_account_id=result.boundary_account,
        detail=result.detail,
    )


def predecessor_of(witness: Witness, txn_id: uuid.UUID) -> PredecessorResult:
    """The 'trace ancestor' primitive: the previous real transaction along a witness.

    Unambiguous by status, never by a reason string:
      * RESOLVED        — a real previous transaction (RING only).
      * NO_LINEAR_ORDER — the witness is LEGS/BUNDLE; 'previous' is undefined for it.
      * NOT_IN_WITNESS  — `txn_id` is not a member of this witness.

    A RING is closed, so the subject's predecessor is the final hop. There is no "first hop"
    case — see PredecessorStatus.
    """
    if txn_id not in witness.transaction_ids:
        return PredecessorResult(PredecessorStatus.NOT_IN_WITNESS)
    if witness.kind is not TraversalKind.RING:
        return PredecessorResult(PredecessorStatus.NO_LINEAR_ORDER)
    i = witness.transaction_ids.index(txn_id)
    return PredecessorResult(
        PredecessorStatus.RESOLVED,
        witness.transaction_ids[i - 1],  # i == 0 wraps to the closing hop; the ring is closed
    )


# ---------------------------------------------------------------------------------------
# Row resolution. Selects no label column, by construction.
# ---------------------------------------------------------------------------------------

_TXN_SQL = text(
    """
    SELECT id, ts, from_account_id, to_account_id, amount_paid, payment_currency,
           amount_received, receiving_currency, payment_format
    FROM aml_transactions
    WHERE id = ANY(:ids)
    """
)

_ACCT_SQL = text("SELECT id, bank, account FROM aml_accounts WHERE id = ANY(:ids)")


async def _resolve_transactions(conn, ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    if not ids:
        return {}
    rows = (await conn.execute(_TXN_SQL, {"ids": ids})).mappings().all()
    return {r["id"]: dict(r) for r in rows}


async def _resolve_accounts(conn, ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    if not ids:
        return {}
    rows = (await conn.execute(_ACCT_SQL, {"ids": ids})).mappings().all()
    return {r["id"]: dict(r) for r in rows}


@asynccontextmanager
async def _snapshot(as_of: str | None):
    """One connection, one explicit txn — optionally pinned to a past MVCC instant.

    Graph load AND row resolution read the same snapshot, so an interrogation can never mix a
    graph from one instant with rows from another (the discipline app/services/replay.py holds).
    A malformed / out-of-window `as_of` becomes ValueError -> 400, never a 500.
    """
    literal = normalize_as_of(as_of) if as_of else None
    try:
        async with engine.connect() as conn:
            async with conn.begin():
                if literal is not None:
                    await conn.execute(text(f"SET TRANSACTION AS OF SYSTEM TIME {literal}"))
                yield conn
    except DBAPIError as e:
        detail = str(getattr(e, "orig", None) or e).lower()
        if literal is not None and any(s in detail for s in _AOST_RANGE_ERRORS):
            raise ValueError(
                f"as_of {as_of!r} is outside the time-travel window "
                "(older than the GC TTL or in the future)"
            ) from e
        raise


# ---------------------------------------------------------------------------------------
# The entry point Item F / a frontend session calls.
# ---------------------------------------------------------------------------------------


def interrogate(g: Graph, subject: Edge) -> tuple[list[Witness], list[str]]:
    """Pure core: run every typology's witness against one subject. No I/O, no model, no labels."""
    witnesses = [_build_witness(g, subject, t, check(g, subject, t)) for t in sorted(WITNESS)]
    competing = [w.typology for w in witnesses if w.matched]
    return witnesses, competing


async def interrogate_transaction(txn_id: uuid.UUID, *, as_of: str | None = None) -> dict | None:
    """Interrogate one real transaction. Returns None if it is not in the evidence layer.

    Deterministic and free: no OpenAI call, so this is safe behind a GET and replayable offline.
    `as_of` time-travels the whole interrogation — graph and rows — with real AOST.

    `competing_typologies` lists every typology that independently witnesses this subject.
    More than one means the structural evidence genuinely supports more than one story. It is
    reported, never used to suppress a verdict.
    """
    async with _snapshot(as_of) as conn:
        g = await load_graph(conn)
        subject = g.by_id.get(txn_id)
        if subject is None:
            return None

        witnesses, competing = interrogate(g, subject)

        txn_ids = {subject.id}
        for w in witnesses:
            txn_ids.update(w.transaction_ids)
        txns = await _resolve_transactions(conn, sorted(txn_ids, key=str))

        acct_ids = {a for r in txns.values() for a in (r["from_account_id"], r["to_account_id"])}
        acct_ids.update(w.boundary_account_id for w in witnesses if w.boundary_account_id)
        accounts = await _resolve_accounts(conn, sorted(acct_ids, key=str))

    return {
        "transaction_id": subject.id,
        "subject": txns[subject.id],
        "witnesses": witnesses,
        "competing_typologies": competing,
        "has_competing_structure": len(competing) > 1,
        "transactions": txns,
        "accounts": accounts,
        "as_of": as_of,
    }
