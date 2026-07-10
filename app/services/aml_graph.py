# READS NO LABEL COLUMN. `aml_transactions.is_laundering` and the whole
# `aml_pattern_members` / `aml_pattern_instances` pair are the ANSWER KEY, not evidence:
# a structural check that consults them is a ground-truth lookup wearing a graph's clothes.
# They are read ONLY by tests, as a scoring oracle. See NOTES.md "Roadmap Item 4".
"""Label-free structural corroboration over the AML money-flow graph (Roadmap Item 4).

The brake's question is never "is this transaction labeled laundering" — it is "does the
typology the agent CLAIMS actually materialize as real edges among real accounts?" So a
witness here is a concrete, ordered list of real `aml_transactions` ids that a reader can
re-derive. No witness, no flag.

Terminology (a real collision in this codebase, resolved once): "lineage evidence" for the
brake means THIS graph — accounts and money — never `belief_inheritance`. Agent belief
inheritance is a different graph, replayed by app/services/replay.py, and it has nothing to
say about whether funds returned to their account of origin.

The three outcomes exist because the evidence layer is a BOUNDED extract (1,500 edges) of a
5,078,345-row universe. A search that finds nothing has two very different meanings:

  MATCH         — a witness exists. The cited rows are real and form the claimed structure.
  CONCLUSIVE_NO — the search closed without ever touching the edge of what we ingested.
                  The absence of structure is a fact about the world, not about our slice.
  INCONCLUSIVE  — the search ran into a SINK: an account whose outgoing edges are simply not
                  materialized here. "No cycle" and "the cycle leaves our extract" are
                  indistinguishable, so the honest answer is "cannot determine".

220 of the 648 ingested accounts are sinks, so INCONCLUSIVE is the common case, not a corner.

Self-loops (447 rows, 446 of them benign "Reinvestment" transfers) are excluded from all
structure: an account paying itself is not a transfer between accounts. They are structurally
inert and land in CONCLUSIVE_NO.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum

from sqlalchemy import text

from app.db import engine

# Bound on the cycle search. Observed CYCLE instances close at lengths 6, 7 and 10 (measured,
# = their instance sizes), so 12 covers every ingested cycle with headroom. It is also what
# keeps the search cheap: the graph is small but a hop budget is what makes a negative result
# a *decision* rather than an exhaustion.
MAX_CYCLE_HOPS = 12
MIN_CYCLE_LEN = 3  # 2 would accept a reciprocal ping-pong pair; that is not laundering.
MIN_FANOUT = 2


class Outcome(str, Enum):
    MATCH = "MATCH"
    CONCLUSIVE_NO = "CONCLUSIVE_NO"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True)
class Edge:
    id: uuid.UUID
    src: uuid.UUID
    dst: uuid.UUID
    ts: object
    amount: object
    payment_format: str

    @property
    def is_self_loop(self) -> bool:
        return self.src == self.dst


@dataclass
class Check:
    """The result of asking the graph whether one claimed typology holds for one edge."""

    outcome: Outcome
    typology: str
    witness_txn_ids: list[uuid.UUID] = field(default_factory=list)
    boundary_account: uuid.UUID | None = None
    detail: str = ""

    @property
    def matched(self) -> bool:
        return self.outcome is Outcome.MATCH


class Graph:
    """The money-flow graph, built from `aml_transactions` alone. Self-loops are kept in
    `by_id` (they are real rows an agent may be asked about) but excluded from adjacency."""

    def __init__(self, edges: list[Edge]) -> None:
        self.by_id: dict[uuid.UUID, Edge] = {e.id: e for e in edges}
        self.out_edges: dict[uuid.UUID, list[Edge]] = defaultdict(list)
        self.in_edges: dict[uuid.UUID, list[Edge]] = defaultdict(list)
        self.accounts: set[uuid.UUID] = set()
        for e in edges:
            self.accounts.add(e.src)
            self.accounts.add(e.dst)
            if e.is_self_loop:
                continue
            self.out_edges[e.src].append(e)
            self.in_edges[e.dst].append(e)
        # One representative edge per (src, dst) pair, chosen deterministically by id, so a
        # reconstructed witness is stable across runs (no dict-order dependence).
        self.pair: dict[uuid.UUID, dict[uuid.UUID, Edge]] = defaultdict(dict)
        for src, es in self.out_edges.items():
            for e in sorted(es, key=lambda x: str(x.id)):
                self.pair[src].setdefault(e.dst, e)

    def succ(self, a: uuid.UUID) -> dict[uuid.UUID, Edge]:
        return self.pair.get(a, {})

    def is_sink(self, a: uuid.UUID) -> bool:
        """No outgoing edge in the slice — this is where our knowledge of the graph stops."""
        return not self.out_edges.get(a)


_LOAD_SQL = text(
    """
    SELECT id, from_account_id, to_account_id, ts, amount_paid, payment_format
    FROM aml_transactions
    """
)


async def load_graph(conn=None) -> Graph:
    """Load the full 1,500-edge extract. Deliberately selects no label column."""
    if conn is not None:
        rows = (await conn.execute(_LOAD_SQL)).mappings().all()
    else:
        async with engine.connect() as c:
            rows = (await c.execute(_LOAD_SQL)).mappings().all()
    return Graph(
        [
            Edge(
                id=r["id"],
                src=r["from_account_id"],
                dst=r["to_account_id"],
                ts=r["ts"],
                amount=r["amount_paid"],
                payment_format=r["payment_format"],
            )
            for r in rows
        ]
    )


# --------------------------------------------------------------------------------------
# Witness constructors. Each returns MATCH with real transaction ids, or explains why not.
# --------------------------------------------------------------------------------------


def cycle_witness(g: Graph, e: Edge) -> Check:
    """`e` closes a directed cycle back to its source over >= MIN_CYCLE_LEN distinct accounts."""
    if e.is_self_loop:
        return Check(Outcome.CONCLUSIVE_NO, "CYCLE", detail="self-loop is not a transfer cycle")

    u, v = e.src, e.dst
    parent: dict[uuid.UUID, uuid.UUID] = {}
    frontier, seen, touched = {v}, {v}, None
    for hop in range(1, MAX_CYCLE_HOPS + 1):
        nxt: set[uuid.UUID] = set()
        for a in sorted(frontier, key=str):
            succ = g.succ(a)
            if not succ:
                touched = touched or a
            for b in sorted(succ, key=str):
                # Checked before `seen`, so a longer return path is still found after a
                # too-short one (the reciprocal-pair case) rather than being swallowed.
                if b == u and hop + 1 >= MIN_CYCLE_LEN:
                    return Check(
                        Outcome.MATCH,
                        "CYCLE",
                        witness_txn_ids=_cycle_path(g, e, a, parent, v),
                        detail=f"directed cycle of length {hop + 1} returns to the source account",
                    )
                if b not in seen:
                    seen.add(b)
                    parent[b] = a
                    nxt.add(b)
        frontier = nxt
        if not frontier:
            break

    if touched is not None:
        return Check(
            Outcome.INCONCLUSIVE,
            "CYCLE",
            boundary_account=touched,
            detail="search reached an account whose outgoing edges are not in this extract",
        )
    return Check(Outcome.CONCLUSIVE_NO, "CYCLE", detail="no return path; search closed inside the extract")


def _cycle_path(g: Graph, e: Edge, last: uuid.UUID, parent: dict, start: uuid.UUID) -> list[uuid.UUID]:
    """Rebuild v -> ... -> last -> u, prefixed by the subject edge u -> v."""
    chain = [last]
    while chain[-1] != start:
        chain.append(parent[chain[-1]])
    chain.reverse()  # start(v) ... last
    ids = [e.id]
    for a, b in zip(chain, chain[1:]):
        ids.append(g.succ(a)[b].id)
    ids.append(g.succ(last)[e.src].id)  # close back to u
    return ids


def scatter_gather_witness(g: Graph, e: Edge) -> Check:
    """`e.src` scatters to >= 2 intermediaries (one of them `e.dst`) which gather into a
    common destination. The gather leg may not precede its own scatter leg in time."""
    if e.is_self_loop:
        return Check(Outcome.CONCLUSIVE_NO, "SCATTER-GATHER", detail="self-loop")

    u, v = e.src, e.dst
    mids = {m for m in g.succ(u) if m != u}
    if len(mids) < MIN_FANOUT or v not in mids:
        return Check(
            Outcome.CONCLUSIVE_NO,
            "SCATTER-GATHER",
            detail=f"source fans out to {len(mids)} account(s); needs >= {MIN_FANOUT}",
        )

    scatter_ts = {m: min(x.ts for x in g.out_edges[u] if x.dst == m) for m in mids}
    dests: dict[uuid.UUID, dict[uuid.UUID, Edge]] = defaultdict(dict)
    touched = None
    for m in sorted(mids, key=str):
        if g.is_sink(m):
            touched = touched or m
            continue
        for x in sorted(g.out_edges[m], key=lambda x: str(x.id)):
            if x.dst in (u, m) or x.ts < scatter_ts[m]:
                continue
            dests[x.dst].setdefault(m, x)

    for d in sorted(dests, key=str):
        backing = dests[d]
        if len(backing) >= MIN_FANOUT and v in backing:
            mids_used = [v] + [m for m in sorted(backing, key=str) if m != v][: MIN_FANOUT - 1]
            ids = [g.succ(u)[m].id for m in mids_used] + [backing[m].id for m in mids_used]
            return Check(
                Outcome.MATCH,
                "SCATTER-GATHER",
                witness_txn_ids=ids,
                detail=f"{len(backing)} intermediaries scatter from one source and gather into one destination",
            )

    if touched is not None:
        return Check(
            Outcome.INCONCLUSIVE,
            "SCATTER-GATHER",
            boundary_account=touched,
            detail="an intermediary's outgoing edges are not in this extract",
        )
    return Check(Outcome.CONCLUSIVE_NO, "SCATTER-GATHER", detail="intermediaries share no common destination")


def gather_scatter_witness(g: Graph, e: Edge) -> Check:
    """A hub gathers from >= 2 distinct sources, then scatters to >= 2 distinct destinations,
    with the source and destination sets disjoint and the gathering finishing before the
    scattering begins.

    NOT FLAG-CAPABLE in this extract — see FLAG_CAPABLE below. Retained because the soundness
    test measures it, and because a denser graph could make it sound.
    """
    if e.is_self_loop:
        return Check(Outcome.CONCLUSIVE_NO, "GATHER-SCATTER", detail="self-loop")

    touched = None
    for h in (e.dst, e.src):
        ins = [(x.src, x.ts) for x in g.in_edges.get(h, []) if x.src != h]
        outs = [(x.dst, x.ts) for x in g.out_edges.get(h, []) if x.dst != h]
        if not outs:
            touched = touched or h
            continue
        S, D = {a for a, _ in ins}, {d for d, _ in outs}
        if len(S) < MIN_FANOUT or len(D) < MIN_FANOUT or (S & D):
            continue
        first_out = min(t for _, t in outs)
        last_in = max(t for _, t in ins)
        gathered = {a for a, t in ins if t <= first_out}
        scattered = {d for d, t in outs if t >= last_in}
        if len(gathered) < MIN_FANOUT or len(scattered) < MIN_FANOUT:
            continue
        in_ids = [x.id for x in sorted(g.in_edges[h], key=lambda x: str(x.id)) if x.src in gathered]
        out_ids = [x.id for x in sorted(g.out_edges[h], key=lambda x: str(x.id)) if x.dst in scattered]
        return Check(
            Outcome.MATCH,
            "GATHER-SCATTER",
            witness_txn_ids=in_ids[:MIN_FANOUT] + out_ids[:MIN_FANOUT],
            detail=f"hub gathers from {len(gathered)} sources then scatters to {len(scattered)} destinations",
        )

    if touched is not None:
        return Check(Outcome.INCONCLUSIVE, "GATHER-SCATTER", boundary_account=touched,
                     detail="hub's outgoing edges are not in this extract")
    return Check(Outcome.CONCLUSIVE_NO, "GATHER-SCATTER", detail="no disjoint gather-then-scatter hub")


def stack_witness(g: Graph, e: Edge) -> Check:
    """Three tiers: >= 2 sources feed `e.src`, and `e.dst` feeds >= 2 destinations, with `e`
    the middle crossing.

    NOT FLAG-CAPABLE. Every ingested STACK instance is internally disjoint (num_components
    4..11, measured in Item 1), so there is no single connected path to witness.
    """
    if e.is_self_loop:
        return Check(Outcome.CONCLUSIVE_NO, "STACK", detail="self-loop")
    srcs = [x for x in g.in_edges.get(e.src, []) if x.src not in (e.src, e.dst)]
    dsts = [x for x in g.out_edges.get(e.dst, []) if x.dst not in (e.src, e.dst)]
    if len({x.src for x in srcs}) >= MIN_FANOUT and len({x.dst for x in dsts}) >= MIN_FANOUT:
        ids = [x.id for x in sorted(srcs, key=lambda x: str(x.id))[:MIN_FANOUT]]
        ids.append(e.id)
        ids += [x.id for x in sorted(dsts, key=lambda x: str(x.id))[:MIN_FANOUT]]
        return Check(Outcome.MATCH, "STACK", witness_txn_ids=ids, detail="two bipartite layers")
    if g.is_sink(e.dst):
        return Check(Outcome.INCONCLUSIVE, "STACK", boundary_account=e.dst,
                     detail="destination's outgoing edges are not in this extract")
    return Check(Outcome.CONCLUSIVE_NO, "STACK", detail="no two-layer structure")


WITNESS = {
    "CYCLE": cycle_witness,
    "SCATTER-GATHER": scatter_gather_witness,
    "GATHER-SCATTER": gather_scatter_witness,
    "STACK": stack_witness,
}

# Which typologies may authorize a FLAG. This is NOT a hand-picked allowlist: the criterion is
# "the witness never fires on an edge belonging to a DIFFERENT typology", measured across all
# 1,500 edges and asserted in tests/test_aml_brake.py::test_witness_soundness_against_oracle.
# Measured today: CYCLE 0/257 and SCATTER-GATHER 0/204 cross-typology false witnesses, versus
# GATHER-SCATTER 6/223 and STACK 27/216. If a future ingestion changes graph density those
# counts move, the test fails, and turning a typology's FLAG capability on becomes a deliberate
# decision rather than a silent one. See NOTES.md "Roadmap Item 4".
FLAG_CAPABLE = frozenset({"CYCLE", "SCATTER-GATHER"})


def check(g: Graph, e: Edge, typology: str) -> Check:
    fn = WITNESS.get(typology)
    if fn is None:
        return Check(Outcome.INCONCLUSIVE, typology, detail="no structural definition for this typology")
    return fn(g, e)


def verify_witness_path(g: Graph, typology: str, txn_ids: list[uuid.UUID], subject: Edge) -> str | None:
    """Re-derive a CITED path from scratch. Returns an error string, or None if the path is
    real, contains the subject, and genuinely forms `typology`. This is what catches the model
    asserting support the rows do not provide — it never trusts our own search's answer."""
    if not txn_ids:
        return "no evidence transactions cited"
    if len(set(txn_ids)) != len(txn_ids):
        return "cited evidence path repeats a transaction"
    edges = []
    for tid in txn_ids:
        e = g.by_id.get(tid)
        if e is None:
            return f"cited transaction {tid} does not exist"
        edges.append(e)
    if subject.id not in txn_ids:
        return "cited evidence path does not contain the subject transaction"

    if typology == "CYCLE":
        # The cited edges must form one closed directed walk over >= MIN_CYCLE_LEN accounts.
        nxt = {e.src: e for e in edges}
        if len(nxt) != len(edges):
            return "cited cycle visits an account twice as a source"
        chain, cur = [], subject
        for _ in range(len(edges)):
            chain.append(cur)
            cur = nxt.get(cur.dst)
            if cur is None:
                break
        if len(chain) != len(edges) or chain[-1].dst != subject.src:
            return "cited edges do not form a closed cycle back to the source"
        if len({e.src for e in edges}) < MIN_CYCLE_LEN:
            return f"cited cycle spans fewer than {MIN_CYCLE_LEN} accounts"
        return None

    if typology == "SCATTER-GATHER":
        src_counts = defaultdict(set)
        for e in edges:
            src_counts[e.src].add(e.dst)
        scatter = [a for a, ds in src_counts.items() if len(ds) >= MIN_FANOUT]
        if not scatter:
            return "no cited account scatters to two or more intermediaries"
        s = scatter[0]
        mids = src_counts[s]
        gather = defaultdict(set)
        for e in edges:
            if e.src in mids:
                gather[e.dst].add(e.src)
        if not any(len(ms) >= MIN_FANOUT for ms in gather.values()):
            return "cited intermediaries do not gather into a common destination"
        return None

    return f"{typology} is not structurally decidable in this evidence layer"
