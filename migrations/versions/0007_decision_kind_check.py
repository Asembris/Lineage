"""decisions: restore, as a CHECK, the guarantee migration 0006's DROP NOT NULL gave away

Migration 0006 dropped NOT NULL on `merchant` and `confidence` so an AML decision would not have to
fabricate them. But it dropped them for EVERY row, and the absence is load-bearing only for AML
rows — so the CARD path silently lost a constraint it had relied on since Phase 1.

MEASURED, not theorised: after 0006, an INSERT of a card decision (aml_transaction_id NULL) omitting
BOTH merchant and confidence was **ACCEPTED** by the database. Before 0006 it was rejected. That is
a real regression in the moat's integrity and this migration closes it.

The CHECK restores the old guarantee for card rows AND makes the AML refusal structural:

    (aml_transaction_id IS NULL     AND merchant IS NOT NULL AND confidence IS NOT NULL)
 OR (aml_transaction_id IS NOT NULL AND merchant IS NULL     AND confidence IS NULL
                                    AND amount_currency IS NOT NULL)

Read it as the two-kinds taxonomy the DTO already documents, now enforced by CockroachDB:

  * A CARD decision (no AML citation) MUST carry a merchant and a confidence — exactly the NOT NULL
    guarantee Phase 1 had. Nothing about the card path is weakened by the seam any more.

  * An AML decision MUST name its currency, and MUST NOT carry a merchant or a confidence. This half
    is STRICTER than merely allowing NULLs, and deliberately so: it makes the "fake merchant" Item 4
    refused, and the fabricated confidence Item D condemned ("do not use this column for anything"),
    IMPOSSIBLE TO WRITE rather than merely discouraged in a comment. Same move as Item 1 putting
    aml_* on a separate metadata, and as the seam's fixed `decided_at`: make the wrong thing
    unrepresentable, do not rely on the next session reading the note.

If a future session ever needs a THIRD kind of decision, this constraint fails loudly and changing
it becomes a deliberate decision — never a silent flip. That failure is a capability change to
notice, not a number to update (the same posture as aml_graph's FLAG_CAPABLE soundness test).

Validated against the live cluster's 4,000 existing card rows before being added: all satisfy the
card branch (0 rows with a NULL merchant or a NULL confidence, 0 rows with an AML citation).

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-12
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CHECK = """
    (aml_transaction_id IS NULL
        AND merchant IS NOT NULL
        AND confidence IS NOT NULL)
 OR (aml_transaction_id IS NOT NULL
        AND merchant IS NULL
        AND confidence IS NULL
        AND amount_currency IS NOT NULL)
"""


def upgrade() -> None:
    op.execute(
        "ALTER TABLE decisions ADD CONSTRAINT ck_decisions_kind CHECK (" + _CHECK + ")"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE decisions DROP CONSTRAINT ck_decisions_kind")
