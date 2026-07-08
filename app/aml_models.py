# NOT on app.db.Base by design: the AML evidence layer is a defaultdb-ONLY concern. Keeping
# it on its own metadata makes that structural, not a convention to remember — Base.metadata
# .create_all (used to provision the throwaway `demo` database, see NOTES "Roadmap Item 0")
# physically cannot reach these tables, and no FK runs from aml_* into the five-table moat.
# Full reasoning: NOTES.md "Roadmap Item 1".
"""AML evidence layer — accounts + money-flow edges + labeled laundering typologies.

The IBM HI-Small dataset models a DIFFERENT graph from agent genealogy: accounts and the
money that flows between them, with a subset of transactions labeled as belonging to a
laundering typology (CYCLE, SCATTER-GATHER, ...). It is the evidence the belief/agent layer
is grounded and measured AGAINST — nothing here inherits, and nothing here references
agents/beliefs/inheritance/decisions. The grounding bridge (a decision citing a real
aml_transactions row) is a LATER, additive change to `decisions`, not modeled here.

Four tables:
  * aml_accounts        — node identity is the compound (bank, account), never account alone.
  * aml_transactions    — the money-flow edges (verbatim CSV rows); is_laundering is ground truth.
  * aml_pattern_instances — one row per loaded labeled block; num_* are the MATERIALIZED real
                            size (num_components > 1 => internally disjoint sub-chains, e.g. STACK).
  * aml_pattern_members — join block -> its transactions; the exact-tuple join materialized once.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class AmlBase(DeclarativeBase):
    """Declarative base for the AML evidence layer — a SEPARATE metadata from app.db.Base.

    This is the whole point of the split: AmlBase.metadata is independent, so the demo
    database's Base.metadata.create_all never creates these, and the five-table moat stays
    exactly the moat. Schema for defaultdb is owned by Alembic migration 0004.
    """


_TS = DateTime(timezone=True)
SOURCE = "ibm-hi-small"


class AmlAccount(AmlBase):
    __tablename__ = "aml_accounts"

    # Client-assigned deterministic uuid5 (see scripts/ingest_aml.py) — NO server default, so
    # re-ingestion is idempotent by identity, not by generating fresh ids.
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    bank: Mapped[str] = mapped_column(Text, nullable=False)
    account: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default=text(f"'{SOURCE}'"))

    __table_args__ = (
        UniqueConstraint("bank", "account", name="uq_aml_accounts_bank_account"),
    )


class AmlTransaction(AmlBase):
    __tablename__ = "aml_transactions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(_TS, nullable=False)
    from_account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("aml_accounts.id"), nullable=False
    )
    to_account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("aml_accounts.id"), nullable=False
    )
    amount_received: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    receiving_currency: Mapped[str] = mapped_column(Text, nullable=False)
    amount_paid: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    payment_currency: Mapped[str] = mapped_column(Text, nullable=False)
    payment_format: Mapped[str] = mapped_column(Text, nullable=False)
    is_laundering: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # The exact-tuple join key (ts + both bank/account pairs + amounts + currencies + format) as a
    # single string — the literal string match the spike proved is 1:1 with a CSV row. UNIQUE so
    # a re-run can't double-load the same underlying transaction.
    raw_key: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("raw_key", name="uq_aml_transactions_raw_key"),
    )


class AmlPatternInstance(AmlBase):
    __tablename__ = "aml_pattern_instances"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    typology: Mapped[str] = mapped_column(Text, nullable=False)
    # The generator's "Max N-degree/hops" label from Patterns.txt. This is a PARAMETER of the
    # synthetic generator, NOT the instance's real size — kept verbatim but never trusted as size.
    max_param: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default=text(f"'{SOURCE}'"))
    instance_index: Mapped[int] = mapped_column(Integer, nullable=False)  # block position in Patterns.txt
    # MATERIALIZED real size (computed at ingest, not read off the label):
    num_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    num_accounts: Mapped[int] = mapped_column(Integer, nullable=False)
    # Weakly-connected components among THIS instance's edges. 1 => a single connected story
    # (CYCLE, SCATTER-GATHER). >1 => internally disjoint sub-chains bundled under one label
    # (STACK is prone to this) — so num_rows/num_accounts alone do NOT imply "one coherent path".
    num_components: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("source", "instance_index", name="uq_aml_pattern_instances_source_idx"),
    )


class AmlPatternMember(AmlBase):
    __tablename__ = "aml_pattern_members"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    pattern_instance_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("aml_pattern_instances.id"), nullable=False
    )
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("aml_transactions.id"), nullable=False
    )
    hop_index: Mapped[int] = mapped_column(Integer, nullable=False)  # order of the row within the block

    __table_args__ = (
        UniqueConstraint(
            "pattern_instance_id", "transaction_id", name="uq_aml_pattern_members_instance_txn"
        ),
    )
