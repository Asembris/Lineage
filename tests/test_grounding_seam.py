"""The grounding seam's three guards (migration 0006).

The seam puts a REAL foreign key (decisions.aml_transaction_id -> aml_transactions.id) in the
MIGRATION but deliberately NOT in the ORM model, because aml_* lives on a separate metadata and
declaring the ForeignKey on the Base-mapped Decision breaks Item 0's demo-database provisioning.
That divergence buys two guarantees, and each is worth exactly as much as the test that proves it:

  1. `test_no_base_foreign_key_escapes_base_metadata` — HERMETIC. The ORM must stay free of a
     dangling reference, or `Base.metadata.create_all` (app/demo_db.py::ensure_demo_ready) raises
     NoReferencedTableError and the `demo` database can no longer be provisioned. This is the guard
     that fires if a future session "fixes" the model by adding the ForeignKey back.

  2. `test_defaultdb_has_the_real_foreign_key` — LIVE. The database-enforced half must actually
     exist. A migration-only FK that silently failed to apply would leave us with neither guarantee
     while claiming both.

  3. `test_database_rejects_a_dangling_aml_transaction_id` — LIVE. CLAUDE.md's "no dangling
     references" must be enforced by CockroachDB, not by the writer's good intentions. Proven by
     trying to insert one and requiring the database to refuse.

All three were verified to FAIL when the condition they guard is violated (introduce the breakage,
watch it fail at the file and line, revert) — a guard that cannot be shown to fail is theatre.
See NOTES.md "The grounding seam".
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db import Base, engine
import app.models  # noqa: F401 — registers all moat tables on Base.metadata


# ---------------------------------------------------------------------------------------------
# 1. HERMETIC — the ORM must not carry a foreign key pointing outside its own metadata.
# ---------------------------------------------------------------------------------------------

def test_no_base_foreign_key_escapes_base_metadata():
    """Every FK declared on Base.metadata must target a table INSIDE Base.metadata.

    This is precisely the condition Base.metadata.create_all needs in order to provision the
    `demo` database. Adding ForeignKey("aml_transactions.id") to app/models.py::Decision violates
    it, because aml_* is on AmlBase — and create_all then raises NoReferencedTableError.
    """
    known = set(Base.metadata.tables)
    escaping = []
    for table in Base.metadata.tables.values():
        for fk in table.foreign_keys:
            target_table = fk.target_fullname.split(".")[0]
            if target_table not in known:
                escaping.append(f"{table.name}.{fk.parent.name} -> {fk.target_fullname}")

    assert not escaping, (
        "A ForeignKey on Base.metadata points OUTSIDE Base.metadata: "
        f"{escaping}. This breaks Base.metadata.create_all (NoReferencedTableError) and with it "
        "Item 0's `demo` database provisioning + the SSE consistency demo. The AML foreign key is "
        "DELIBERATELY migration-only — see migration 0006 and app/models.py::Decision. Do not "
        "'fix' the model by adding it back."
    )


def test_decision_carries_the_seam_columns_without_a_foreign_key():
    """The column exists on the model; the CONSTRAINT deliberately does not."""
    decisions = Base.metadata.tables["decisions"]
    assert "aml_transaction_id" in decisions.columns
    assert "amount_currency" in decisions.columns
    # The honest NULLs (see migration 0006: no fake merchant, no fabricated confidence).
    assert decisions.columns["merchant"].nullable is True
    assert decisions.columns["confidence"].nullable is True
    # ...and NO ORM-level FK on the seam column.
    assert not decisions.columns["aml_transaction_id"].foreign_keys, (
        "aml_transaction_id must NOT declare a ForeignKey in the ORM — the real FK lives in "
        "migration 0006. See the comment in app/models.py::Decision."
    )


# ---------------------------------------------------------------------------------------------
# 2 & 3. LIVE — the database half of the bargain.
# ---------------------------------------------------------------------------------------------

def test_defaultdb_has_the_real_foreign_key():
    """The FK the ORM omits must genuinely exist in defaultdb, or we have neither guarantee."""

    async def _run():
        async with engine.connect() as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT ccu.table_name AS parent, kcu.column_name AS col "
                        "FROM information_schema.table_constraints tc "
                        "JOIN information_schema.key_column_usage kcu "
                        "  ON tc.constraint_name = kcu.constraint_name "
                        "JOIN information_schema.constraint_column_usage ccu "
                        "  ON tc.constraint_name = ccu.constraint_name "
                        "WHERE tc.constraint_type = 'FOREIGN KEY' "
                        "  AND tc.table_name = 'decisions'"
                    )
                )
            ).mappings().all()
        return {(r["col"], r["parent"]) for r in rows}

    fks = asyncio.run(_run())
    assert ("aml_transaction_id", "aml_transactions") in fks, (
        "decisions.aml_transaction_id has NO foreign key in defaultdb. The ORM deliberately omits "
        "it, so the migration is the ONLY thing enforcing CLAUDE.md's 'no dangling references' for "
        "this column. Re-apply migration 0006. Found FKs: " + str(sorted(fks))
    )


def test_database_rejects_a_dangling_aml_transaction_id():
    """CockroachDB — not the writer — must refuse a decision citing a nonexistent AML transaction.

    This is the whole reason the FK was kept as a real database constraint rather than traded for a
    writer-enforced convention.
    """

    async def _run():
        async with engine.connect() as c:
            agent_id = (
                await c.execute(text("SELECT id FROM agents LIMIT 1"))
            ).scalar_one()
            # A uuid that is certainly not in aml_transactions.
            bogus = uuid.uuid4()
            await c.execute(
                text(
                    "INSERT INTO decisions "
                    "(agent_id, txn_ref, amount, verdict, decided_at, is_fraud, aml_transaction_id) "
                    "VALUES (:a, 'seam-guard-probe', 1, 'approve', now(), false, :t)"
                ),
                {"a": agent_id, "t": bogus},
            )
            await c.commit()  # must never be reached

    with pytest.raises(IntegrityError):
        asyncio.run(_run())
