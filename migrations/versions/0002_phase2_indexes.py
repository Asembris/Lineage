"""phase 2 indexes — vector search + decision performance lookups

DDL only (no data, no OpenAI): this migration is part of the pre-gate work, which the
Phase 2 plan keeps OpenAI-free. It adds:

  * a C-SPANN VECTOR INDEX on beliefs.embedding so the live agent (post-gate) can retrieve
    the driving belief by distributed vector similarity. `CREATE VECTOR INDEX` was verified
    available on this Basic-tier cluster (v25.4.10) — see scripts/probe_crdb.py / NOTES.md.
    Raw op.execute() because Alembic can't emit CRDB's vector-index DDL.

  * a btree index on decisions(driving_belief_id, decided_at): every belief_performance
    window aggregation filters by driving_belief_id and buckets by decided_at, so this is
    the access path for the staleness computation.

Re-embedding the origin belief with a real text-embedding-3-small vector is deliberately
NOT here — that is a data op that needs OpenAI and belongs to the post-gate agent-brain
phase. The vector index works over the Phase-1 placeholder embedding until then.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-01
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Access path for per-window belief_performance aggregation (Phase 2/3 staleness).
    op.create_index(
        "ix_decisions_belief_time",
        "decisions",
        ["driving_belief_id", "decided_at"],
    )
    # Distributed vector index for the live agent's belief retrieval (post-gate).
    op.execute("CREATE VECTOR INDEX ix_beliefs_embedding ON beliefs (embedding)")


def downgrade() -> None:
    op.execute("DROP INDEX ix_beliefs_embedding")
    op.drop_index("ix_decisions_belief_time", table_name="decisions")
