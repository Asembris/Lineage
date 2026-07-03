"""phase 3 invalidation — closure state on belief_inheritance + audit_log

The atomic invalidation (POST /beliefs/{id}/invalidate) closes a belief AND its full
inherited closure — every holder edge in belief_inheritance — in ONE CockroachDB
transaction. CLAUDE.md defines the closure as "via belief_inheritance", so the closure
state lives on those edges (not a new fleet table, not a single-row flip):

  * belief_inheritance.invalidated_at / invalidated_by — per-edge revocation. Set-based
    UPDATE ... WHERE belief_id = X flips EVERY holder at one commit ("all holders at once").
    The lineage recursive CTE stays UNFILTERED, so provenance/trace still fully resolves —
    we annotate revocation, never delete history (paraphrase-free provenance holds).

  * audit_log — every consequential action is logged: who invalidated what, when, how many
    agents/edges were affected, the CRDB commit HLC (pins the exact MVCC version for the
    certificate's AOST cross-check), and the S3 certificate pointer + integrity hash.

DDL only; no data. `invalidated_by` and `actor` carry no FK (same choice as
beliefs.invalidated_by — the actor is a supervisor id, whose entity semantics are external).

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    # Per-holder closure revocation — the multi-row target of the atomic invalidation.
    op.add_column(
        "belief_inheritance", sa.Column("invalidated_at", _TS, nullable=True)
    )
    op.add_column(
        "belief_inheritance", sa.Column("invalidated_by", sa.Uuid(), nullable=True)
    )

    op.create_table(
        "audit_log",
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("action", sa.Text(), nullable=False),  # 'belief_invalidation'
        sa.Column("actor", sa.Uuid(), nullable=False),  # supervisor id (no FK)
        sa.Column("belief_id", sa.Uuid(), nullable=True),
        sa.Column("affected_agent_count", sa.Integer(), nullable=False),
        sa.Column("affected_edge_count", sa.Integer(), nullable=False),
        # CRDB commit HLC (cluster_logical_timestamp captured in-txn) — pins the exact
        # MVCC version the certificate re-verifies with AS OF SYSTEM TIME.
        sa.Column("commit_hlc", sa.Text(), nullable=True),
        # S3 certificate pointer + integrity, filled in after the post-commit PUT.
        sa.Column("certificate_id", sa.Uuid(), nullable=True),
        sa.Column("cert_s3_key", sa.Text(), nullable=True),
        # 'pending' at commit -> 'written' | 'failed' after the S3 side effect.
        sa.Column("cert_status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("created_at", _TS, nullable=False),
        sa.ForeignKeyConstraint(["belief_id"], ["beliefs.id"]),
    )
    op.create_index("ix_audit_belief", "audit_log", ["belief_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_belief", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_column("belief_inheritance", "invalidated_by")
    op.drop_column("belief_inheritance", "invalidated_at")
