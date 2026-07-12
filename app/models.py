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
    # Phase 3 — per-holder closure revocation. The atomic invalidation flips every edge
    # of a belief in ONE set-based UPDATE. NULL = the holder still holds it (active).
    invalidated_at: Mapped[dt.datetime | None] = mapped_column(_TS, nullable=True)
    invalidated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)


class Decision(Base):
    """Phase 2/3. Created empty in Phase 1. Migration 0006 added the AML grounding seam.

    A decision is either a Phase-2 CARD authorization (synthetic txn_ref, real merchant/amount,
    is_fraud from the simulator) or an AML decision grounded in a REAL IBM money-flow edge
    (aml_transaction_id set, merchant/confidence NULL, is_fraud from aml_transactions.is_laundering).
    """

    __tablename__ = "decisions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_UUID_PK)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agents.id"), nullable=False
    )
    txn_ref: Mapped[str] = mapped_column(Text, nullable=False)
    # NULL for AML decisions: a bank-to-bank transfer has no merchant, and inventing one is the
    # forced fit Item 4 refused ("a fake merchant"). Migration 0006 dropped the NOT NULL.
    merchant: Mapped[str | None] = mapped_column(Text, nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    # The unit of `amount`. NULL for Phase-2 card rows — the simulator never declared a currency,
    # and inferring "US Dollar" from the belief text's "$180" would manufacture a fact the data does
    # not contain. AML rows name their real currency (the extract spans 14). See migration 0006.
    amount_currency: Mapped[str | None] = mapped_column(Text, nullable=True)
    verdict: Mapped[str] = mapped_column(Text, nullable=False)  # approve|decline|blocked
    driving_belief_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("beliefs.id"), nullable=True
    )
    # NULL for AML decisions: the structural witness (aml_graph.cycle_witness) is DETERMINISTIC and
    # produces no confidence. Item D proved this column is rng.uniform(0.80, 0.95) noise in the
    # Phase-2 backfill and concluded "do not use this column for anything" — so fabricating a value
    # here would add noise to an already-condemned column. Migration 0006 dropped the NOT NULL.
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    decided_at: Mapped[dt.datetime] = mapped_column(_TS, nullable=False)
    is_fraud: Mapped[bool] = mapped_column(Boolean, nullable=False)

    # ===================== DELIBERATELY NO ForeignKey. DO NOT "FIX" THIS. =====================
    # The REAL foreign key (aml_transaction_id -> aml_transactions.id) EXISTS in defaultdb and is
    # enforced by CockroachDB — migration 0006 creates it. It is intentionally ABSENT here.
    #
    # WHY: aml_* lives on `AmlBase` metadata (app/aml_models.py), NOT on `Base`. Item 0 provisions
    # the throwaway `demo` database with `Base.metadata.create_all` (app/demo_db.py). Declaring
    # ForeignKey("aml_transactions.id") on this Base-mapped class points Base.metadata at a table it
    # does not contain, so create_all raises NoReferencedTableError, `demo` can no longer be
    # provisioned, and the SSE consistency demo BREAKS AT RUNTIME. Verified by running it.
    #
    # Keeping the FK in the migration only gets BOTH guarantees: defaultdb enforces "no dangling
    # references" (a CLAUDE.md non-negotiable) in the DATABASE, while Base.metadata stays free of a
    # dangling reference so `demo` still provisions. This is the ONE place in the project where the
    # ORM deliberately understates the schema, and tests/test_grounding_seam.py guards BOTH sides.
    # Full reasoning: NOTES.md "The grounding seam" -> "THE FK FINDING".
    aml_transaction_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)


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


class AuditLog(Base):
    """Phase 3 — every consequential action logged (who invalidated what, when).

    Written atomically with the invalidation. The S3 certificate pointer/hash are filled in
    after the post-commit PUT; commit_hlc pins the MVCC version the certificate re-verifies
    against with AS OF SYSTEM TIME.
    """

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_UUID_PK)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    belief_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("beliefs.id"), nullable=True
    )
    affected_agent_count: Mapped[int] = mapped_column(Integer, nullable=False)
    affected_edge_count: Mapped[int] = mapped_column(Integer, nullable=False)
    commit_hlc: Mapped[str | None] = mapped_column(Text, nullable=True)
    certificate_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    cert_s3_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    cert_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'pending'")
    )
    content_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(_TS, nullable=False)
