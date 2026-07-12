"""regulatory_corpus — FATF/FFIEC/FinCEN red flags, and the project's FIRST WORKING VECTOR INDEX

Strictly ADDITIVE. One new table on defaultdb, on the CorpusBase metadata (so
`Base.metadata.create_all` cannot reach it, the demo database stays clean, and the five-table moat
stays five). ZERO foreign keys — none into the moat, none into aml_*, none into typology_corpus.

=========================== THE `vector_cosine_ops` IS THE POINT ===========================
This index is declared `(embedding vector_cosine_ops)`. Every OTHER vector index in this project
is not, and that is a REAL, SHIPPED DEFECT that went undetected through every prior session:

    beliefs          VECTOR INDEX ix_beliefs_embedding          (embedding vector_l2_ops)
    typology_corpus  VECTOR INDEX ix_typology_corpus_embedding  (embedding vector_l2_ops)

Both were created with a bare `CREATE VECTOR INDEX ... (embedding)`. CockroachDB's default opclass
is `vector_l2_ops`, which accelerates the L2 operator `<->` ONLY. But BOTH queries that use them —
`agent_brain._retrieve_beliefs` and `corpus._retrieval_sql` — rank with `<=>`, COSINE distance.
The opclass and the operator have never matched, so NEITHER index has ever been selected by the
planner, at any row count.

Item 3 recorded the true observation ("the plan is a FULL SCAN") with a FALSE CAUSE ("because there
are only 4 rows"), and `verify_corpus.py` has asserted that false cause on every run since. It was
found by running the decisive check, not by reading the four documents that agreed with each other.
Measured on the live cluster (1,000 random vectors, planner stats verified fresh; and again at 4):

    index opclass      query op   vector index used?
    vector_l2_ops      <->  L2    YES
    vector_l2_ops      <=>  cos   NO  -- FULL SCAN   <- what beliefs + typology_corpus actually do
    vector_cosine_ops  <->  L2    NO
    vector_cosine_ops  <=>  cos   YES                <- what THIS table does
    vector_cosine_ops  <=>  cos   YES, at n=4 too    <- so ROW COUNT WAS NEVER THE CAUSE

Fixing the two existing indexes is DELIBERATELY NOT DONE HERE. A selected C-SPANN index is an
APPROXIMATE nearest-neighbour search; today's full scan is EXACT. Item 4's Gate 0 depends on WHICH
three of four documents come back, and Item 8's golden set was built against exact retrieval.
Flipping typology_corpus from exact to approximate is a live behavioural change to the brake's
input and needs its own before/after measurement over the real four documents. It is deferred to
its own gated session. This table has no such history to disturb — it is born correct.

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.config import get_settings
from app.types_crdb import Vector

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    dim = get_settings().embedding_dim  # 1536 — same text-embedding-3-small path as everything else
    op.create_table(
        "regulatory_corpus",
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("doc_label", sa.Text(), nullable=False),
        # Recovered by a spine rule, NOT from markdown heading levels — LlamaParse flattens every
        # heading in all five documents to H1. See app/services/regulation.py.
        sa.Column("part", sa.Text(), nullable=True),
        sa.Column("section", sa.Text(), nullable=True),
        sa.Column("lead_in", sa.Text(), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        # The exact string that was embedded. Stored rather than re-derived — Item 8's
        # grounding-representation bug came from re-rendering evidence differently than the model
        # saw it, and cost a full recalibration to find.
        sa.Column("embed_input", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(dim), nullable=False),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("source", "ordinal", name="uq_regulatory_corpus_source_ordinal"),
    )
    op.create_index("ix_regulatory_corpus_source", "regulatory_corpus", ["source"])
    # THE ONE THAT WORKS. `vector_cosine_ops` matches the `<=>` the retrieval actually ranks with.
    op.execute(
        "CREATE VECTOR INDEX ix_regulatory_corpus_embedding "
        "ON regulatory_corpus (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX ix_regulatory_corpus_embedding")
    op.drop_index("ix_regulatory_corpus_source", table_name="regulatory_corpus")
    op.drop_table("regulatory_corpus")
