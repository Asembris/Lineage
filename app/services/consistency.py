"""Consistency-proof harness — strong (CRDB) vs eventually-consistent invalidation.

This is the honest, MEASURED contrast (plan (c)), not a narrated one. Both strategies close
the SAME closure (every belief_inheritance edge of a belief). The difference is structural:

  * STRONG (the real endpoint, invalidation.invalidate_belief): one serializable transaction.
    Every edge flips at a single commit. Commit points = 1. A concurrent observer can NEVER
    read a torn closure — snapshot isolation guarantees it sees all-open or all-closed.

  * EVENTUAL (eventual_invalidate, HERE — the Postgres+Pinecone-style baseline): the closure
    is denormalized per holder (each edge is that holder's live copy of the belief), and a
    system without cross-key atomic commit must fan out one update per holder. Commit points =
    N. Between commits the intermediate state is COMMITTED and externally visible — a real
    split window during which lagging holders still act on the dead belief.

Honesty notes:
  * The per-holder `delay` models real fan-out latency (network / per-namespace / per-region).
    We report the window as a function of it; we do NOT insert any delay into the strong path.
  * The delay-FREE facts stand on their own: commit points = 1 vs N, and "a committed split
    state is reachable" = false vs true. The delay only sizes an already-structural window.

The leaked-fraud proof uses the Phase-2 deterministic belief predicate (matches_target) — no
OpenAI, so the timing-sensitive test stays crisp — keyed on PER-HOLDER edge state: a holder
acts on the belief iff its own edge is still open.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import text

from app.db import SessionLocal, engine
from app.sim.transactions import matches_target

ALL_ACTIVE = "ALL_ACTIVE"
ALL_INVALIDATED = "ALL_INVALIDATED"
SPLIT = "SPLIT"  # some holders invalidated, some still live — the eventual-consistency window


def classify(total: int, open_edges: int) -> str:
    if open_edges == total:
        return ALL_ACTIVE
    if open_edges == 0:
        return ALL_INVALIDATED
    return SPLIT


async def _closure_counts(conn, belief_id: uuid.UUID) -> tuple[int, int]:
    row = (
        await conn.execute(
            text(
                "SELECT count(*) tot, count(*) - count(invalidated_at) open "
                "FROM belief_inheritance WHERE belief_id = :b"
            ),
            {"b": belief_id},
        )
    ).mappings().one()
    return row["tot"], row["open"]


@dataclass
class ObserverResult:
    samples: list[str] = field(default_factory=list)

    @property
    def split_count(self) -> int:
        return sum(1 for s in self.samples if s == SPLIT)

    @property
    def saw_transition(self) -> bool:
        return ALL_ACTIVE in self.samples and ALL_INVALIDATED in self.samples


async def observe_closure(
    belief_id: uuid.UUID,
    stop: asyncio.Event,
    interval: float = 0.01,
    ready: asyncio.Event | None = None,
    on_sample: Callable[[str, int, int], None] | None = None,
) -> ObserverResult:
    """Sample the closure repeatedly until `stop`, classifying each read.

    One connection; a rollback between reads ends each implicit txn so the next SELECT gets a
    fresh read timestamp (latest committed state). Records a final sample after stop so the
    terminal ALL_INVALIDATED is always captured.

    `ready` (if given) is set AFTER the connection is live and the first sample is taken — so
    a caller can gate its mutation on the observer actually sampling, not on a guessed sleep
    (CRDB Cloud's TLS handshake can outlast a short pre-delay and hide a fast strong commit).

    `on_sample` (if given) is called with (classification, total, open_edges) for EVERY sample
    as it is taken — the hook the SSE endpoint uses to stream samples live. It is additive:
    the accumulated ObserverResult return value is unchanged, so existing callers are unaffected.
    """
    result = ObserverResult()

    def _record(total: int, open_edges: int) -> None:
        classification = classify(total, open_edges)
        result.samples.append(classification)
        if on_sample is not None:
            on_sample(classification, total, open_edges)

    async with engine.connect() as conn:
        total, open_edges = await _closure_counts(conn, belief_id)
        _record(total, open_edges)
        await conn.rollback()
        if ready is not None:
            ready.set()
        while not stop.is_set():
            await asyncio.sleep(interval)
            total, open_edges = await _closure_counts(conn, belief_id)
            _record(total, open_edges)
            await conn.rollback()
        total, open_edges = await _closure_counts(conn, belief_id)
        _record(total, open_edges)
    return result


@dataclass
class EventualResult:
    commit_points: int
    closed_order: list[uuid.UUID]


async def eventual_invalidate(
    belief_id: uuid.UUID, actor_id: uuid.UUID, per_holder_delay: float = 0.06
) -> EventualResult:
    """The eventually-consistent baseline: one committed update PER holder edge, with a
    fan-out delay between them. Each commit is externally visible => a real split window.

    Deliberately NOT how the endpoint works — this exists only to measure the contrast.
    """
    async with engine.connect() as conn:
        edge_ids = list(
            (
                await conn.execute(
                    text(
                        "SELECT id FROM belief_inheritance "
                        "WHERE belief_id = :b AND invalidated_at IS NULL "
                        "ORDER BY inherited_at"
                    ),
                    {"b": belief_id},
                )
            ).scalars().all()
        )

    now = dt.datetime.now(dt.timezone.utc)
    closed: list[uuid.UUID] = []
    for edge_id in edge_ids:
        async with SessionLocal() as s:  # a SEPARATE committed transaction per holder
            async with s.begin():
                await s.execute(
                    text(
                        "UPDATE belief_inheritance SET invalidated_at=:t, invalidated_by=:a "
                        "WHERE id=:id"
                    ),
                    {"t": now, "a": actor_id, "id": edge_id},
                )
        closed.append(edge_id)
        await asyncio.sleep(per_holder_delay)  # models per-holder fan-out latency
    # The belief row itself would be yet another non-atomic write in a multi-store baseline;
    # flip it last so global status eventually agrees with the per-holder copies.
    async with SessionLocal() as s:
        async with s.begin():
            await s.execute(
                text(
                    "UPDATE beliefs SET status='invalidated', invalidated_at=:t, "
                    "invalidated_by=:a WHERE id=:b AND status='active'"
                ),
                {"t": now, "a": actor_id, "b": belief_id},
            )
    return EventualResult(commit_points=len(edge_ids) + 1, closed_order=closed)


# --- leaked-fraud proof (deterministic policy, per-holder view) -----------------------------

# A matching fraud: mcc 5411, under $180, mature account — exactly what the belief blesses.
FRAUD_TXN = dict(mcc=5411, amount=Decimal("99.00"), age_months=18.0)


async def holder_holds_belief_live(belief_id: uuid.UUID, agent_id: uuid.UUID) -> bool:
    """Does this holder still act on the belief? True iff its OWN closure edge is still open.

    This is the per-holder cache view — the thing that lags under eventual consistency.
    """
    async with engine.connect() as conn:
        open_edges = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM belief_inheritance "
                    "WHERE belief_id=:b AND to_agent_id=:a AND invalidated_at IS NULL"
                ),
                {"b": belief_id, "a": agent_id},
            )
        ).scalar_one()
    return open_edges > 0


async def deterministic_verdict(
    belief_id: uuid.UUID, agent_id: uuid.UUID, txn: dict | None = None
) -> str:
    """The Phase-2 belief predicate applied by a holder to a matching fraud.

    'approve' (the leak) iff the holder still holds the belief live AND the txn matches the
    target pattern; otherwise 'blocked'. No OpenAI — deterministic, so timing tests stay crisp.
    """
    txn = txn or FRAUD_TXN
    if not matches_target(txn["mcc"], txn["amount"], txn["age_months"]):
        return "blocked"
    live = await holder_holds_belief_live(belief_id, agent_id)
    return "approve" if live else "blocked"
