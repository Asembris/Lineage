"""grounding seam — decisions may cite a REAL aml_transactions row (the FIRST moat change since Phase 1)

This is the seam Items 1/3/4/5/6/7 each deferred: a `decisions` row (five-table moat) citing a real
IBM AML money-flow edge, so a decision's `is_fraud` can come from real ground truth
(`aml_transactions.is_laundering`) instead of the Phase-2 simulator. Four changes to `decisions`,
and every one of them is here because a naive version of it was wrong. Full reasoning: NOTES.md
"The grounding seam ... INVESTIGATION: STEP 4 IS CUT".

  1. aml_transaction_id — nullable FK -> aml_transactions(id). THE ONE PERMITTED CROSSING EDGE.
  2. merchant          — DROP NOT NULL. A bank-to-bank transfer has no merchant.
  3. confidence        — DROP NOT NULL. A deterministic structural witness has no confidence.
  4. amount_currency   — NEW nullable column. The extract spans 14 currencies.

=========================== THE FK LIVES HERE AND *ONLY* HERE ===========================
`app/models.py` declares `aml_transaction_id` as a PLAIN `Uuid` with **NO `ForeignKey`**, and that
divergence from this migration is DELIBERATE, LOAD-BEARING, and MUST NOT BE "FIXED".

`aml_*` lives on its own `AmlBase` metadata (migration 0004), NOT `app.db.Base`. Item 0 provisions
the throwaway `demo` database with `Base.metadata.create_all` (app/demo_db.py::ensure_demo_ready).
Declaring `ForeignKey("aml_transactions.id")` on the Base-mapped `Decision` therefore points
Base.metadata at a table it does not contain, and `create_all` raises **NoReferencedTableError** —
so the `demo` database can no longer be provisioned and the SSE consistency demo breaks at runtime.

This was established by RUNNING it, not by reasoning — but that run was a one-off probe in the
gitignored `scratchpad/` and IS GONE. Do not go looking for it. The DURABLE guarantee, which you can
run right now, is `tests/test_grounding_seam.py`:
`test_no_base_foreign_key_escapes_base_metadata` fails the moment a ForeignKey on `Decision` points
outside `Base.metadata`, and `test_defaultdb_has_the_real_foreign_key` fails if the migration's FK
is missing from the database. Those two, not a vanished probe, are what hold this design in place.
(An earlier version of this header cited the probe path directly, which promised a reader an
artifact that does not exist. See NOTES "FABRICATED VERIFICATION CITATIONS".)

Putting the FK in the MIGRATION instead gives us both halves:
  * defaultdb gets a GENUINE, DATABASE-ENFORCED foreign key, so CLAUDE.md's "no dangling
    references" non-negotiable is enforced by CockroachDB and not merely by the writer;
  * Base.metadata stays free of any dangling reference, so `demo` still provisions.

Consequences, stated loudly so no future session is surprised by them:
  * MODEL-vs-DATABASE divergence: the ORM understates the schema. This is the ONE place in the
    project where that is true. `tests/test_grounding_seam.py` guards BOTH sides — it asserts the
    FK really exists in defaultdb AND that no ForeignKey in Base.metadata escapes Base.metadata.
  * DEMO-vs-DEFAULTDB divergence, stated as MEASURED rather than as intended:
      - The FK is absent from `demo` BY DESIGN (it is migration-only, and Alembic targets defaultdb).
      - `demo.decisions` ALSO lacks the seam COLUMNS entirely right now. `ensure_demo_ready()` calls
        `create_all(checkfirst=True)`, which SKIPS the already-existing `decisions` table and never
        ALTERs it — so demo's copy is frozen at the pre-0006 schema. Verified live, not assumed.
      - This is HARMLESS: `demo` runs only the lightweight genealogy seed (24 agents / 1 belief /
        9 edges) and never reads or writes `decisions` beyond `seed.seed()`'s DELETE. Nothing in the
        SSE consistency demo selects a decision column.
      - If `demo` is ever dropped and re-provisioned, `decisions` is created fresh from the ORM and
        WILL carry the seam columns — still without the FK. Both shapes are fine; a future session
        that makes the demo actually WRITE decisions must reconcile this first.
  * `scripts/verify_aml_ingest.py` check #7 ("no FK crosses the aml_/moat boundary") is amended to
    permit EXACTLY this one named edge. Every other crossing, in either direction, still fails.

============================ WHY merchant / confidence GO NULLABLE ============================
Both are NOT NULL today, and neither has an honest value for an AML transaction. Item 4 already
refused this exact fabrication ("forcing it in would need a synthetic agent and a fake merchant").

  * merchant: an IBM row is a transfer between (bank, account) pairs. There is no merchant, and
    inventing one — e.g. writing the counterparty bank into a merchant column — is the forced-fit
    the Item-1 spike, Item 4 and Item 5 each refused. NULL is the honest value.

  * confidence: the decider is `aml_graph.cycle_witness`, which is DETERMINISTIC — a witness either
    exists or it does not; there is no probability to report. Worse, Item D PROVED this column is
    already `rng.uniform(0.80, 0.95)` pure noise in the Phase-2 backfill, flat at ~0.875 per agent
    while the real measured confidence falls 0.924 -> 0.528, and concluded verbatim: "Do not use
    this column for anything." Fabricating a value for a deterministic witness would inject fresh
    noise into a column the project has ALREADY CONDEMNED. NULL is the only honest value.

Existing Phase-2 card decisions keep their values; nothing is backfilled or rewritten.

================================ WHY amount_currency EXISTS ================================
`decisions.amount` is a bare `Numeric` with no unit. The 1,500-edge extract spans **14 currencies**
(US Dollar only 596/1500; also Euro 444, Yuan, Rupee, Shekel, ...), so storing `amount_paid` alone
would make one column mean a different thing per row — a silent semantic muddle. AML decisions name
their unit from `aml_transactions.payment_currency`.

Existing Phase-2 rows are left NULL DELIBERATELY and are NOT backfilled to 'US Dollar'. The
simulator never declared a currency; inferring one from the belief text's "$180" would be
manufacturing a fact the data does not contain. **NULL here means "this world had no currency
concept", which is exactly true of the Phase-2 card world.**

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-12
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The FK is declared INLINE on ADD COLUMN. CockroachDB will not accept an ALTER TABLE ...
    # ADD CONSTRAINT against a column added in the SAME transaction, and Alembic runs a migration
    # inside one — so the two-statement form (add column, then add constraint) fails here.
    # Inline REFERENCES creates the column and its foreign key in a single schema change.
    op.execute(
        "ALTER TABLE decisions "
        "ADD COLUMN aml_transaction_id UUID REFERENCES aml_transactions (id)"
    )
    op.execute("ALTER TABLE decisions ADD COLUMN amount_currency TEXT")

    # An AML transfer has no merchant, and a deterministic witness has no confidence.
    # See the header — neither is a convenience; both are refusals to fabricate.
    op.execute("ALTER TABLE decisions ALTER COLUMN merchant DROP NOT NULL")
    op.execute("ALTER TABLE decisions ALTER COLUMN confidence DROP NOT NULL")


def downgrade() -> None:
    # Restoring NOT NULL would fail against any AML decision rows (their merchant/confidence are
    # legitimately NULL), so the seam's rows must be deleted before downgrading. Deliberate: a
    # downgrade that silently invented values to satisfy the old constraint would be the exact
    # fabrication the upgrade exists to avoid.
    op.execute("DELETE FROM decisions WHERE aml_transaction_id IS NOT NULL")
    op.execute("ALTER TABLE decisions ALTER COLUMN confidence SET NOT NULL")
    op.execute("ALTER TABLE decisions ALTER COLUMN merchant SET NOT NULL")
    op.execute("ALTER TABLE decisions DROP COLUMN amount_currency")
    op.execute("ALTER TABLE decisions DROP COLUMN aml_transaction_id")
