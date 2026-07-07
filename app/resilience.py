"""Bounded retry/backoff for transient CockroachDB Cloud failures.

Scoped deliberately to the two surfaces most exposed to transient DB errors during a live demo:
the atomic invalidation endpoint and the SSE consistency stream's reseed. This is NOT a
project-wide error-handling rewrite — everywhere else keeps its existing behaviour.

Transients we retry (a fresh attempt is the right response):
  * SERIALIZATION/retry errors (SQLSTATE 40001). CRDB is SERIALIZABLE; a contended transaction
    can be told to restart. The correct retry unit is the WHOLE transaction (a fresh call).
  * dropped/reset connections. CRDB Cloud closes idle conns and this project has seen a
    mid-statement drop during backfill; a re-attempt on a fresh connection recovers.
  * TRUNCATE schema-change contention ("indexes being dropped") — two overlapping reseeds collide
    on CRDB's online schema change; it clears once the job settles, so a short backoff wins.

Everything else — a real bug, a constraint violation, or the domain signals BeliefNotFound /
AlreadyInvalidated — is NOT transient and is re-raised immediately. We never retry a deterministic
failure into a hang, and bounded attempts mean one bad call can't spin forever.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")

# SQLSTATE codes that mean "transient, try again": serialization failures + the 08xxx / 57Pxx
# connection-and-shutdown family CRDB Cloud can surface when a node/gateway recycles a conn.
_TRANSIENT_SQLSTATES = frozenset(
    {
        "40001",  # serialization_failure (CRDB retry error)
        "40003",  # statement_completion_unknown
        "08000",  # connection_exception
        "08001",  # sqlclient_unable_to_establish_sqlconnection
        "08003",  # connection_does_not_exist
        "08004",  # sqlserver_rejected_establishment_of_sqlconnection
        "08006",  # connection_failure
        "57P01",  # admin_shutdown
        "57P02",  # crash_shutdown
        "57P03",  # cannot_connect_now
    }
)

# Substrings that identify a transient even when no SQLSTATE is attached (e.g. a raw connection
# drop surfaced as an OperationalError, or CRDB's schema-change contention message). Matched
# case-insensitively against the exception chain's str().
_TRANSIENT_SUBSTRINGS = (
    "restart transaction",
    "retry_serializable",
    "retry transaction",
    "indexes being dropped",  # overlapping TRUNCATE schema change (Phase 3 Step 7 gotcha)
    "server closed the connection",
    "connection reset",
    "connection refused",
    "terminating connection",
    "eof detected",
    "bad connection",
    "consumed",  # psycopg "the connection is closed"/consumed variants
)


class TransientRetryExhausted(Exception):
    """Raised when a transient DB error persisted past the retry budget.

    Carries the last underlying error as __cause__ so callers can map it to a clean, retriable
    response (a 503 / an SSE `error` event) instead of an opaque 500.
    """


def _sqlstate(exc: BaseException) -> str | None:
    # psycopg exposes .sqlstate; some drivers use .pgcode. SQLAlchemy wraps the DBAPI error in
    # .orig, so check the whole __cause__/__context__/orig chain.
    for e in _chain(exc):
        code = getattr(e, "sqlstate", None) or getattr(e, "pgcode", None)
        if code:
            return str(code)
    return None


def _chain(exc: BaseException):
    seen = set()
    stack = [exc]
    while stack:
        e = stack.pop()
        if id(e) in seen or e is None:
            continue
        seen.add(id(e))
        yield e
        for nxt in (getattr(e, "orig", None), e.__cause__, e.__context__):
            if nxt is not None:
                stack.append(nxt)


def is_transient(exc: BaseException) -> bool:
    """True iff `exc` (or something in its cause chain) is a retriable CRDB transient."""
    code = _sqlstate(exc)
    if code in _TRANSIENT_SQLSTATES:
        return True
    text = " ".join(str(e) for e in _chain(exc)).lower()
    return any(s in text for s in _TRANSIENT_SUBSTRINGS)


async def run_with_retry(
    factory: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 0.2,
    max_delay: float = 2.0,
) -> T:
    """Call `factory()` (which returns a FRESH awaitable each time), retrying transient failures.

    `factory` must build a new coroutine per attempt — retrying a whole transaction means calling
    it again from scratch, not awaiting a spent coroutine. Non-transient errors propagate at once
    (so BeliefNotFound / AlreadyInvalidated map to 404/409 unchanged). A transient that outlives
    the bounded attempts is wrapped in TransientRetryExhausted so the caller can return a clean
    retriable response. Backoff is exponential (base_delay * 2**i), capped at max_delay.
    """
    last: BaseException | None = None
    for i in range(attempts):
        try:
            return await factory()
        except Exception as exc:  # noqa: BLE001 — classify, then re-raise or retry
            if not is_transient(exc):
                raise
            last = exc
            if i == attempts - 1:
                break
            await asyncio.sleep(min(max_delay, base_delay * (2**i)))
    raise TransientRetryExhausted(
        f"transient DB error persisted after {attempts} attempts"
    ) from last
