"""The grounding seam's guards (migrations 0006 + 0007).

Migration 0007's `ck_decisions_kind` CHECK is guarded at the bottom of this file. It exists because
0006's `DROP NOT NULL` on merchant/confidence was OVER-BROAD: the absence is load-bearing only for
AML rows, but it was dropped for EVERY row, so the CARD path silently lost a Phase-1 guarantee
(measured: a card decision with no merchant and no confidence was ACCEPTED). 0007 restores it for
card rows and additionally makes the AML-side fabrication — Item 4's fake merchant, Item D's
fabricated confidence — IMPOSSIBLE TO WRITE rather than merely discouraged in a comment.


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


# ============================ THE FK GUARD'S PROBE ROW, SHARED ============================
# ONE definition, used by BOTH tests below, so they CANNOT drift apart. The FK guard needs a row
# that is valid in every way EXCEPT its aml_transaction_id — otherwise some other constraint rejects
# it first and the foreign key is never exercised at all.
#
# THIS HAS BEEN SPRUNG TWICE, and a docstring did not stop either one:
#   * 0007 made the probe CHECK-invalid (it omitted `amount_currency`);
#   * 0008 did it again (it required the txn_ref basis tag; the probe said 'seam-guard-probe').
# Both times the FK guard went red on a CheckViolation while proving NOTHING about the foreign key.
#
# So the warning is now a GUARD, not a comment: `test_the_fk_guards_probe_row_is_still_a_valid_row`
# inserts EXACTLY this shape with a REAL transaction and asserts the database ACCEPTS it. Tighten
# ck_decisions_kind without updating this dict and that test fails, and its message tells you to fix
# the PROBE — not to relax the FK assertion, which is the tempting wrong move that would silently
# retire the foreign-key check.
_FK_PROBE_SQL = (
    "INSERT INTO decisions (id, agent_id, txn_ref, amount, verdict, decided_at, "
    "is_fraud, aml_transaction_id, amount_currency) "
    "VALUES (:i, :a, :r, 1, 'approve', now(), false, :t, 'US Dollar')"
)
_FK_PROBE_TXN_REF = "aml:INCONCLUSIVE"  # a real basis tag (0008); merchant/confidence omitted (0007)


async def _insert_fk_probe(txn_id: uuid.UUID) -> uuid.UUID:
    """The FK guard's exact row, citing `txn_id`. Real -> must be accepted; bogus -> FK must reject."""
    probe_id = uuid.uuid4()
    async with engine.connect() as c:
        agent_id = (await c.execute(text("SELECT id FROM agents LIMIT 1"))).scalar_one()
        await c.execute(
            text(_FK_PROBE_SQL),
            {"i": probe_id, "a": agent_id, "r": _FK_PROBE_TXN_REF, "t": txn_id},
        )
        await c.commit()
    return probe_id


def test_the_fk_guards_probe_row_is_still_a_valid_row():
    """THE GUARD THAT KEEPS THE FK GUARD HONEST. Not a docstring — a test.

    The FK guard below can only prove anything if its probe row is rejected by the FOREIGN KEY and
    by nothing else. That is a property of the probe versus EVERY OTHER CONSTRAINT ON `decisions`,
    and it has silently broken twice (0007, then 0008), each time converting the FK guard into a
    test that proved nothing while still looking like it did.

    So: insert the FK guard's EXACT row shape, citing a REAL transaction. The database must ACCEPT
    it. If a future migration tightens `ck_decisions_kind` (or adds any other constraint) such that
    this shape is no longer valid, THIS test fails first — and the fix is to update `_FK_PROBE_SQL`
    / `_FK_PROBE_TXN_REF`, never to weaken the FK guard's assertion.
    """

    async def _run():
        async with engine.connect() as c:
            real = (await c.execute(text("SELECT id FROM aml_transactions LIMIT 1"))).scalar_one()
        try:
            pid = await _insert_fk_probe(real)
        except IntegrityError as e:
            raise AssertionError(
                "THE FK GUARD'S PROBE ROW IS NO LONGER A VALID DECISION. Some constraint on "
                "`decisions` now rejects it even with a REAL aml_transaction_id — so "
                "test_database_rejects_a_dangling_aml_transaction_id is no longer testing the "
                "FOREIGN KEY at all; it is being rejected by something else.\n\n"
                "FIX THE PROBE (_FK_PROBE_SQL / _FK_PROBE_TXN_REF above), do NOT relax the FK "
                "guard's assertion. This has happened twice already (migrations 0007 and 0008).\n\n"
                f"The database said: {e.orig}"
            ) from e
        async with engine.begin() as c:  # clean up: it is a real, valid row
            await c.execute(text("DELETE FROM decisions WHERE id = :i"), {"i": pid})

    asyncio.run(_run())


def test_database_rejects_a_dangling_aml_transaction_id():
    """CockroachDB — not the writer — must refuse a decision citing a nonexistent AML transaction.

    This is the whole reason the FK was kept as a real database constraint rather than traded for a
    writer-enforced convention.

    The row is otherwise a PERFECTLY VALID AML decision (see `_FK_PROBE_SQL` above), so the ONLY
    thing that can reject it is the foreign key — and the failure is asserted to be a FOREIGN KEY
    violation BY NAME. Without both of those, this test would go green on a CheckViolation and prove
    nothing. `test_the_fk_guards_probe_row_is_still_a_valid_row` is what keeps the first half true.
    """
    with pytest.raises(IntegrityError) as exc:
        asyncio.run(_insert_fk_probe(uuid.uuid4()))  # certainly not in aml_transactions

    assert "foreign key" in str(exc.value).lower(), (
        "The insert was rejected, but NOT by the foreign key — so this test is not proving what it "
        "claims. If ck_decisions_kind was just tightened, the fix is the PROBE (see _FK_PROBE_SQL), "
        f"not this assertion. Got: {exc.value}"
    )


# ---------------------------------------------------------------------------------------------
# 4. LIVE — migration 0007: the two-kinds taxonomy, enforced by the database.
#
# Migration 0006 dropped NOT NULL on merchant/confidence so AML rows need not fabricate them — but
# it dropped them for EVERY row, so the CARD path silently lost a constraint it had since Phase 1.
# (Measured: a card decision with no merchant and no confidence was ACCEPTED.) 0007's CHECK restores
# that guarantee AND makes the AML-side fabrication impossible to write.
# ---------------------------------------------------------------------------------------------

def _assert_check_violation(err) -> None:
    """CockroachDB reports a CHECK failure by its EXPRESSION, not its name, so we assert the
    violation CLASS. This still distinguishes it from a foreign-key or NOT NULL rejection — which
    is the whole point: these tests must fail for the reason they claim."""
    msg = str(err).lower()
    assert "checkviolation" in msg or "check constraint" in msg, (
        f"Expected the ck_decisions_kind CHECK to reject this row; got instead: {err}"
    )


def _insert(**cols) -> None:
    """INSERT one decision with exactly the given columns. Rolls back / cleans up either way.

    `txn_ref` defaults to a CARD-shaped probe ref and is overridable, because migration 0008 made
    the basis tag structural: an AML probe row MUST carry a real `aml:*` tag or the CHECK rejects it
    for that reason instead of the one under test. Cleanup is BY ID, not by txn_ref — the id is the
    only marker that survives both shapes.
    """

    async def _run():
        probe_id = uuid.uuid4()
        async with engine.connect() as c:
            agent_id = (await c.execute(text("SELECT id FROM agents LIMIT 1"))).scalar_one()
            payload = {
                "id": probe_id,
                "agent_id": agent_id,
                "txn_ref": "ck-kind-probe",  # card-shaped; AML cases override it
                "amount": 42,
                "verdict": "approve",
                "decided_at": None,
                "is_fraud": False,
                **cols,
            }
            names = [k for k in payload if k != "decided_at"]
            binds = ", ".join(f":{k}" for k in names)
            try:
                await c.execute(
                    text(
                        f"INSERT INTO decisions ({', '.join(names)}, decided_at) "
                        f"VALUES ({binds}, now())"
                    ),
                    {k: payload[k] for k in names},
                )
                await c.commit()
            finally:
                await c.rollback()
                async with engine.connect() as c2:
                    await c2.execute(
                        text("DELETE FROM decisions WHERE id = :i"), {"i": probe_id}
                    )
                    await c2.commit()

    asyncio.run(_run())


def test_a_card_decision_may_not_omit_merchant_or_confidence():
    """The Phase-1 guarantee the seam's DROP NOT NULL gave away, restored for card rows."""
    with pytest.raises(IntegrityError) as exc:
        _insert()  # no merchant, no confidence, no AML citation
    _assert_check_violation(exc.value)


def test_an_aml_decision_may_not_fabricate_a_merchant_or_a_confidence():
    """Item 4 refused the fake merchant and Item D condemned the fabricated confidence.

    0007 makes both UNWRITABLE rather than merely discouraged in a comment.

    Every probe below carries a REAL basis tag (`aml:INCONCLUSIVE`), so it satisfies migration
    0008's clause and the only thing left that can reject it is the merchant/confidence clause this
    test is actually about. Same discipline as the FK guard above, and for the same reason.
    """

    async def _real_txn():
        async with engine.connect() as c:
            return (await c.execute(text("SELECT id FROM aml_transactions LIMIT 1"))).scalar_one()

    txn = asyncio.run(_real_txn())
    aml = {"aml_transaction_id": txn, "amount_currency": "Euro", "txn_ref": "aml:INCONCLUSIVE"}

    with pytest.raises(IntegrityError) as exc:
        _insert(**aml, merchant="Bank of Nowhere")
    _assert_check_violation(exc.value)

    with pytest.raises(IntegrityError) as exc:
        _insert(**aml, confidence=0.87)
    _assert_check_violation(exc.value)

    # ...and the honest shape is accepted (or the constraint would be a wall, not a brake).
    _insert(**aml)
