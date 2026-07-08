"""AML evidence layer — four additive aml_* tables (Roadmap Item 1)

The IBM HI-Small AML dataset models accounts and money flow — a DIFFERENT graph from agent
genealogy. It becomes a SEPARATE evidence layer the belief/agent layer is grounded and
measured against. This migration is strictly ADDITIVE:

  * FOUR new tables — aml_accounts, aml_transactions, aml_pattern_instances, aml_pattern_members.
  * ZERO changes to the five-table moat; ZERO foreign keys running FROM these tables INTO
    agents/beliefs/belief_inheritance/decisions/belief_performance. The evidence layer is
    read BY the agent layer, never referenced by it. The grounding bridge (a nullable
    decisions.aml_transaction_id) is a LATER item, deliberately NOT added here.

Because there are no inbound FKs from aml_* into the five tables, `TRUNCATE ... CASCADE` of
the five tables (seed.seed / backfill) can never reach these — this static reference data
survives every reseed. See NOTES.md "Roadmap Item 1".

Hand-written (matching 0001-0003). The aml_* models live on their OWN metadata
(app/aml_models.py, NOT app.db.Base), so they are absent from target_metadata here by
design; this migration is the sole DDL source for them on defaultdb.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    # Node identity is the compound (bank, account), never account alone. uuid PK is
    # client-assigned (deterministic uuid5 at ingest) — no server default, so re-ingestion is
    # idempotent by identity.
    op.create_table(
        "aml_accounts",
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True),
        sa.Column("bank", sa.Text(), nullable=False),
        sa.Column("account", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False, server_default=sa.text("'ibm-hi-small'")),
        sa.UniqueConstraint("bank", "account", name="uq_aml_accounts_bank_account"),
    )

    op.create_table(
        "aml_transactions",
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True),
        # Source ts is minute-resolution 'YYYY/MM/DD HH:MM' with NO timezone; stored as
        # timestamptz-at-UTC by project convention (a storage-typing choice, not a tz claim).
        sa.Column("ts", _TS, nullable=False),
        sa.Column("from_account_id", sa.Uuid(), nullable=False),
        sa.Column("to_account_id", sa.Uuid(), nullable=False),
        sa.Column("amount_received", sa.Numeric(), nullable=False),
        sa.Column("receiving_currency", sa.Text(), nullable=False),
        sa.Column("amount_paid", sa.Numeric(), nullable=False),
        sa.Column("payment_currency", sa.Text(), nullable=False),
        sa.Column("payment_format", sa.Text(), nullable=False),
        sa.Column("is_laundering", sa.Boolean(), nullable=False),
        sa.Column("raw_key", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["from_account_id"], ["aml_accounts.id"]),
        sa.ForeignKeyConstraint(["to_account_id"], ["aml_accounts.id"]),
        sa.UniqueConstraint("raw_key", name="uq_aml_transactions_raw_key"),
    )
    op.create_index("ix_aml_txn_from", "aml_transactions", ["from_account_id"])
    op.create_index("ix_aml_txn_to", "aml_transactions", ["to_account_id"])
    op.create_index("ix_aml_txn_laundering", "aml_transactions", ["is_laundering"])
    op.create_index("ix_aml_txn_ts", "aml_transactions", ["ts"])

    op.create_table(
        "aml_pattern_instances",
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True),
        sa.Column("typology", sa.Text(), nullable=False),
        # generator's "Max N" label — a PARAMETER, not the instance's real size.
        sa.Column("max_param", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False, server_default=sa.text("'ibm-hi-small'")),
        sa.Column("instance_index", sa.Integer(), nullable=False),
        # MATERIALIZED real size (computed at ingest, never read off the label):
        sa.Column("num_rows", sa.Integer(), nullable=False),
        sa.Column("num_accounts", sa.Integer(), nullable=False),
        # weakly-connected components among the instance's edges; >1 => internally disjoint
        # sub-chains (STACK is prone to this), so size columns don't imply "one coherent path".
        sa.Column("num_components", sa.Integer(), nullable=False),
        sa.UniqueConstraint("source", "instance_index", name="uq_aml_pattern_instances_source_idx"),
    )
    op.create_index("ix_aml_instance_typology", "aml_pattern_instances", ["typology"])

    op.create_table(
        "aml_pattern_members",
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True),
        sa.Column("pattern_instance_id", sa.Uuid(), nullable=False),
        sa.Column("transaction_id", sa.Uuid(), nullable=False),
        sa.Column("hop_index", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["pattern_instance_id"], ["aml_pattern_instances.id"]),
        sa.ForeignKeyConstraint(["transaction_id"], ["aml_transactions.id"]),
        sa.UniqueConstraint(
            "pattern_instance_id", "transaction_id", name="uq_aml_pattern_members_instance_txn"
        ),
    )
    op.create_index("ix_aml_member_instance", "aml_pattern_members", ["pattern_instance_id"])
    op.create_index("ix_aml_member_txn", "aml_pattern_members", ["transaction_id"])


def downgrade() -> None:
    op.drop_index("ix_aml_member_txn", table_name="aml_pattern_members")
    op.drop_index("ix_aml_member_instance", table_name="aml_pattern_members")
    op.drop_table("aml_pattern_members")
    op.drop_index("ix_aml_instance_typology", table_name="aml_pattern_instances")
    op.drop_table("aml_pattern_instances")
    op.drop_index("ix_aml_txn_ts", table_name="aml_transactions")
    op.drop_index("ix_aml_txn_laundering", table_name="aml_transactions")
    op.drop_index("ix_aml_txn_to", table_name="aml_transactions")
    op.drop_index("ix_aml_txn_from", table_name="aml_transactions")
    op.drop_table("aml_transactions")
    op.drop_table("aml_accounts")
