"""Async database engine and session wiring.

The engine is created once at import. `get_session` is the FastAPI dependency for
ORM work. Raw AS OF SYSTEM TIME work uses `engine` directly (see services/time_travel.py)
because it needs precise control over the connection and transaction.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


_settings = get_settings()

# NOTE: no AUTOCOMMIT here. The default isolation lets us open an explicit
# transaction and issue SET TRANSACTION AS OF SYSTEM TIME as its first statement.
engine: AsyncEngine = create_async_engine(
    _settings.async_database_url,
    pool_pre_ping=True,  # CRDB Cloud can drop idle conns; validate on checkout
    echo=False,
)

SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


def get_engine() -> AsyncEngine:
    """FastAPI dependency returning the shared engine.

    Exists so the readiness probe (/health/ready) resolves its engine through DI rather
    than the module global: a test can override it with a deliberately-broken engine to
    prove the probe returns 503 on a real unreachable DB — the false-green we are fixing.
    """
    return engine
