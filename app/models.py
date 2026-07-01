"""SQLAlchemy 2.x ORM models — the five core tables (the competitive moat).

Kept faithful to CLAUDE.md's data model. UUID PKs default to gen_random_uuid()
(CRDB best practice: avoids sequential-insert range hotspots). Timestamps are TIMESTAMPTZ.

Phase 1 populates agents, beliefs, belief_inheritance. `decisions` and
`belief_performance` are created but stay EMPTY until Phases 2-3.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import get_settings
from app.db import Base
from app.types_crdb import Vector

_UUID_PK = dict(
    primary_key=True,
    server_default=text("gen_random_uuid()"),
)
_TS = DateTime(timezone=True)


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_UUID_PK)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    bloodline: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)  # 'alive' | 'dead'
    spawned_at: Mapped[dt.datetime] = mapped_column(_TS, nullable=False)
    retired_at: Mapped[dt.datetime | None] = mapped_column(_TS, nullable=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agents.id"), nullable=True
    )

    parent: Mapped[Agent | None] = relationship(remote_side="Agent.id")


class Belief(Base):
    __tablename__ = "beliefs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_UUID_PK)
    rule_text: Mapped[str] = mapped_column(Text, nullable=False)
    originating_agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agents.id"), nullable=False
    )
    formed_at: Mapped[dt.datetime] = mapped_column(_TS, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(get_settings().embedding_dim), nullable=True
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'active'")
    )  # 'active' | 'invalidated'
    invalidated_at: Mapped[dt.datetime | None] = mapped_column(_TS, nullable=True)
    # Who invalidated it (agent or supervisor id). No FK: Phase 3 defines the semantics.
    invalidated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)


class BeliefInheritance(Base):
    __tablename__ = "belief_inheritance"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_UUID_PK)
    belief_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("beliefs.id"), nullable=False
    )
    from_agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agents.id"), nullable=False
    )
    to_agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agents.id"), nullable=False
    )
    inherited_at: Mapped[dt.datetime] = mapped_column(_TS, nullable=False)


class Decision(Base):
    """Phase 2/3. Created empty in Phase 1."""

    __tablename__ = "decisions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_UUID_PK)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agents.id"), nullable=False
    )
    txn_ref: Mapped[str] = mapped_column(Text, nullable=False)
    merchant: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    verdict: Mapped[str] = mapped_column(Text, nullable=False)  # approve|decline|blocked
    driving_belief_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("beliefs.id"), nullable=True
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    decided_at: Mapped[dt.datetime] = mapped_column(_TS, nullable=False)
    is_fraud: Mapped[bool] = mapped_column(Boolean, nullable=False)


class BeliefPerformance(Base):
    """Phase 2/3 — makes staleness REAL (queried, never hardcoded). Empty in Phase 1."""

    __tablename__ = "belief_performance"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_UUID_PK)
    belief_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("beliefs.id"), nullable=False
    )
    window_start: Mapped[dt.datetime] = mapped_column(_TS, nullable=False)
    window_end: Mapped[dt.datetime] = mapped_column(_TS, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    false_positive_rate: Mapped[float] = mapped_column(Float, nullable=False)
    frauds_approved: Mapped[int] = mapped_column(Integer, nullable=False)
