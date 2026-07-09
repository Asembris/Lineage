"""typology_corpus — CockroachDB-native RAG corpus table + C-SPANN vector index (Roadmap Item 3)

Strictly ADDITIVE. ONE new table on defaultdb, holding the typology definitions embedded as real
1536-dim vectors on the SAME cluster / SAME AOST timeline as the lineage graph and the aml_*
evidence layer — the whole point being one transactional store spanning graph + vectors (not a
Pinecone-style split). ZERO changes to the five-table moat; ZERO foreign keys crossing into
agents/beliefs/... OR into aml_*. The join back to aml_pattern_instances is by the exact `typology`
string, verified at ingest, deliberately NOT a cross-metadata FK.

The corpus model lives on its OWN metadata (app/corpus_models.py CorpusBase, NOT app.db.Base), so
it is absent from target_metadata here by design — this migration is its sole DDL source. Same
pattern as migration 0004 (aml_* on AmlBase).

The VECTOR column + `CREATE VECTOR INDEX` reuse the Phase-2 / migration-0002 pattern (raw
op.execute — Alembic can't emit CRDB's vector DDL). NOTE (honest scope): at the 4-row corpus this
item ships, the planner does NOT exercise the vector index (a full scan wins at that size); the
index proves the MECHANISM is wired, not retrieval quality at scale. See NOTES.md "Roadmap Item 3".

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.config import get_settings
from app.types_crdb import Vector

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    dim = get_settings().embedding_dim  # 1536, matches beliefs.embedding + text-embedding-3-small
    op.create_table(
        "typology_corpus",
        # Client-assigned deterministic uuid5 (see scripts/ingest_corpus.py) — no server default,
        # so re-ingestion is idempotent and a revision is an in-place UPDATE (MVCC keeps history).
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True),
        sa.Column("typology", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False, server_default=sa.text("'altman-2306.16424'")),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        # The project's Vector UserDefinedType renders `VECTOR(dim)` DDL directly (same as
        # beliefs.embedding in migration 0001) — no raw-SQL column dance needed.
        sa.Column("embedding", Vector(dim), nullable=False),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("source", "typology", name="uq_typology_corpus_source_typology"),
    )
    # Join-back access path: Item 4 looks up aml_pattern_instances by the retrieved typology.
    op.create_index("ix_typology_corpus_typology", "typology_corpus", ["typology"])
    # Distributed C-SPANN vector index (mechanism wiring; not exercised at 4 rows — see NOTES).
    op.execute("CREATE VECTOR INDEX ix_typology_corpus_embedding ON typology_corpus (embedding)")


def downgrade() -> None:
    op.execute("DROP INDEX ix_typology_corpus_embedding")
    op.drop_index("ix_typology_corpus_typology", table_name="typology_corpus")
    op.drop_table("typology_corpus")
