"""Dedicated, isolated database for the destructive consistency demo (roadmap Item 0).

The SSE consistency stream is the one endpoint whose whole design is destructive by
construction: every run TRUNCATEs + reseeds the genealogy and invalidates a belief closure.
For the console (the decision feed + the four supervisor interactions), that shared-cluster
blast radius was a full wipe of the data those read paths depend on (NOTES Phase 4 "OPERATIONAL
RISK"). Nothing distinguished a deliberate demo run from a stray reconnecting tab, and it had
already destroyed the 4,000-row backfill in practice.

The fix is physical isolation: the demo lives in its OWN database (`demo`) on the SAME cluster.
The console keeps reading `defaultdb` through the app's global engine; the demo reseeds/kills
in `demo` through the engine here. A TRUNCATE in `demo` is physically incapable of naming a
`defaultdb` table, so the demo can never wipe console state — deliberate run or stray tab alike.

Why a single PERSISTENT `demo` database rather than one per request: the realistic threat model
(a stray tab, a judge poking around, CI) is fully covered by decoupling demo from console, and
the existing single-flight guard already serialises two concurrent demo runs into a clean `busy`.
A database-per-request would buy true demo concurrency at the cost of per-run DDL, orphan-database
sweeping, and the schema-change-job stacking this project has repeatedly hit — a bad trade near
the deadline. One extra database is free on Basic tier (billing is aggregated per org/cluster;
CREATE/DROP DATABASE confirmed permitted for the app user on this cluster).

Provisioning is a one-time, idempotent `CREATE DATABASE IF NOT EXISTS` + `create_all` (all six
tables are ORM models on Base, incl. audit_log and the post-0003 closure columns, so no Alembic
is needed here). The demo needs only the lightweight genealogy seed — 24 agents, 1 belief, 9
edges — never the decisions backfill or belief_performance windows, which the console alone reads.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings
from app.db import Base

# The isolated database name. A distinct database on the SAME cluster as defaultdb.
DEMO_DB_NAME = "demo"


def _demo_url(async_url: str, db_name: str = DEMO_DB_NAME) -> str:
    """Derive the demo engine URL from the app's async URL by swapping the database path.

    Everything else (driver, host, credentials, sslmode/sslrootcert query params) is preserved
    verbatim — only the `/<database>` path segment is replaced. So the demo connects to the same
    cluster with the same TLS handling, just a different database.
    """
    parts = urlsplit(async_url)
    return urlunsplit(parts._replace(path=f"/{db_name}"))


_settings = get_settings()

# Engine bound to the `demo` database. Mirrors app.db.engine's options (pool_pre_ping for
# CRDB Cloud idle-drop tolerance; no AUTOCOMMIT so an explicit txn can still SET TRANSACTION).
demo_engine: AsyncEngine = create_async_engine(
    _demo_url(_settings.async_database_url),
    pool_pre_ping=True,
    echo=False,
)

DemoSession: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=demo_engine,
    expire_on_commit=False,
    autoflush=False,
)

# One-time provisioning guard. The single-flight stream guard already serialises demo runs, but
# the lock makes ensure_demo_ready() safe even if it is ever called concurrently.
_provision_lock = asyncio.Lock()
_provisioned = False


async def ensure_demo_ready() -> None:
    """Idempotently ensure the `demo` database exists and carries the five/six-table schema.

    Cheap after the first call (a process-level flag short-circuits it). The CREATE DATABASE runs
    through the app's global engine (which is connected to defaultdb and can create sibling
    databases); create_all runs through the demo engine under AUTOCOMMIT so each DDL statement
    commits on its own (CRDB is happiest running schema changes outside a multi-statement txn).
    """
    global _provisioned
    if _provisioned:
        return
    async with _provision_lock:
        if _provisioned:
            return

        # Import here to avoid any import-time coupling / cycles with app.db at module load.
        from sqlalchemy import text

        import app.models  # noqa: F401 — registers all six tables on Base.metadata
        from app.db import engine as app_engine

        # 1. Create the sibling database (idempotent). AUTOCOMMIT: CREATE DATABASE cannot run
        #    inside an explicit transaction on CRDB.
        async with app_engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(text(f"CREATE DATABASE IF NOT EXISTS {DEMO_DB_NAME}"))

        # 2. Build the schema in `demo` (idempotent via checkfirst). AUTOCOMMIT so each
        #    CREATE TABLE commits independently.
        async with demo_engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.run_sync(Base.metadata.create_all)

        _provisioned = True
