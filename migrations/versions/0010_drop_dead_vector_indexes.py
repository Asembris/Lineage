"""drop the two vector indexes that no query could ever use

TWO OF THIS PROJECT'S THREE VECTOR INDEXES HAVE NEVER BEEN SELECTED BY THE PLANNER — not at any
row count, not once, since the day they were created. This migration removes them. It is a
CORRECTION, not a retreat: what remains is a schema that says exactly what is true.

    beliefs          ix_beliefs_embedding          (embedding vector_l2_ops)  -- migration 0002
    typology_corpus  ix_typology_corpus_embedding  (embedding vector_l2_ops)  -- migration 0005

=================== WHY FLIPPING THE OPCLASS WAS THE WRONG FIX ===================
The obvious repair — `vector_l2_ops` -> `vector_cosine_ops`, so the opclass matches the `<=>`
cosine operator both queries actually rank with — is INERT. It changes no plan. Measured, not
reasoned (scripts/probe_vector_opclass.py, part 1; live cluster, planner stats verified fresh):

    index                              query                        vector index used?
    (embedding)      cosine opclass    ORDER BY <=>  no WHERE       YES
    (embedding)      cosine opclass    ORDER BY <=>  WHERE source   NO  - scan
    (source, embedding) cosine         ORDER BY <=>  WHERE source   YES
    (source, embedding) cosine         ORDER BY <=>  no WHERE       NO  - scan

A CockroachDB vector index is selected ONLY when every PREFIX column is constrained to a value.
Both legacy indexes are on `(embedding)` ALONE — zero prefix columns — so ANY where-clause
forces a scan whatever the opclass. And BOTH real queries carry one:

  * app/services/corpus.py::_retrieval_sql emits `WHERE source = :source`, and all 12
    row-returning `retrieve_typology()` call sites pass `source=SOURCE` (the agent's own path,
    aml_agent.py, among them). The index could not be used even with the opclass "fixed".
  * app/services/agent_brain.py::_retrieve_beliefs filters `WHERE b.status = 'active'` AND an
    ownership predicate that is an OR across a LEFT JOIN to belief_inheritance — which cannot be
    a prefix constraint under ANY index definition. CockroachDB volunteers a NON-vector index in
    the plan (`CREATE INDEX ... (status) STORING (originating_agent_id, embedding)`), because it
    cannot use the vector one here at all.

=================== WHY NOT MAKE THEM WORK INSTEAD ===================
`typology_corpus` COULD be made live with a `(source, embedding vector_cosine_ops)` prefix index.
That was measured and REJECTED. It converts retrieval from EXACT to APPROXIMATE (a C-SPANN/ANN
search), and Item 4's Gate 0 is a set-membership test on exactly the top-3 of a k=3 retrieval
against a FOUR-document corpus — one document swapping out flips a verdict from FLAG to
INSUFFICIENT_COVERAGE. The measurement says it is safe TODAY (0 top-3 changes over 1,572
adversarial queries and over the real agent queries — see the probe), but that safety is a
property of four rows fitting in ONE C-SPANN partition, not a property of the design. It would
buy a sponsor bullet by making the brake's input approximate, and it would hold only by luck of
scale. `beliefs` cannot be made live at all without denormalizing holder identity onto the moat
to turn the ownership predicate into a prefix constraint — a five-table schema change to run an
approximate search over TWO rows. Theatre, twice over.

=================== WHAT SURVIVES, AND IT IS TRUE ===================
`beliefs.embedding` and `typology_corpus.embedding` remain REAL `VECTOR(1536)` columns searched
with CockroachDB's REAL cosine `<=>` operator. The search is now honestly EXACT: at 2 and 4 rows,
with these query shapes, a scan is not merely acceptable — it is the right plan, and it is the
one the planner was choosing all along. What is removed is the false claim that an index served
them.

`ix_regulatory_corpus_embedding` (migration 0009, `vector_cosine_ops`, 233 rows) is untouched and
is the one genuinely-exercised distributed vector index in this system: its EXPLAIN contains a
real `vector search` node. It stays the sponsor claim, and it is now the ONLY one — which is a
checkable statement rather than an aspirational one.

Down-migration recreates the indexes EXACTLY as they were (bare `CREATE VECTOR INDEX`, i.e.
vector_l2_ops), because a downgrade must restore the prior state, dead index and all — not
silently "improve" it into a different one.

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-13
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Raw op.execute: Alembic cannot emit CRDB vector-index DDL (the 0002/0005/0009 precedent).
    op.execute("DROP INDEX IF EXISTS ix_beliefs_embedding")
    op.execute("DROP INDEX IF EXISTS ix_typology_corpus_embedding")


def downgrade() -> None:
    # Restore them exactly as they were — bare, therefore vector_l2_ops, therefore dead. A
    # downgrade returns the prior world; it does not get to fix it.
    op.execute("CREATE VECTOR INDEX ix_beliefs_embedding ON beliefs (embedding)")
    op.execute(
        "CREATE VECTOR INDEX ix_typology_corpus_embedding ON typology_corpus (embedding)"
    )
