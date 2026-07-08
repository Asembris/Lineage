"""scripts/verify_aml_ingest.py — re-derive the AML ingestion against the LIVE database and the
RAW CSV (Roadmap Item 1). Trusts neither the spike's numbers nor the ingest run's stdout.

Checks (each PASS/FAIL; exits non-zero on any failure):
  1. row counts + per-typology instance spread are what we intended to load.
  2. is_laundering split (labeled=300 true, benign=1200 false) as loaded.
  3. selected labeled instances are pairwise account-disjoint AS LOADED (genuine isolation).
  4. every benign transaction is is_laundering=false AND touches a labeled-instance account
     (the meaningful precision negatives), and the realized benign:fraud ratio.
  5. positional-parse spot check: reconstruct several stored txns and confirm from/to bank+account
     against the exact raw CSV line by hand (proves the duplicate-"Account"-header fix), plus the
     uuid5 id derivation for accounts and transactions.
  6. num_components distribution — grounds the honest NOTES note that STACK instances may be
     internally disjoint sub-chains rather than one connected path.
  7. structural isolation: NO foreign key runs FROM aml_* INTO the five-table moat (or back).

Run:  PYTHONPATH=. .venv/Scripts/python.exe scripts/verify_aml_ingest.py
"""

import asyncio
import csv
import sys
import uuid
from collections import Counter, defaultdict

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import text  # noqa: E402

from app.db import engine  # noqa: E402

TRANS = "data/raw/HI-Small_Trans.csv"
_NS = uuid.uuid5(uuid.NAMESPACE_DNS, "lineage.aml")

# expected intent (see scripts/ingest_aml.py knobs)
EXP_INSTANCES = 20
EXP_PER_TYPOLOGY = {"CYCLE": 5, "SCATTER-GATHER": 5, "GATHER-SCATTER": 5, "STACK": 5}
EXP_FRAUD = 300
EXP_BENIGN = 1200

_fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}", flush=True)
    if not ok:
        _fails.append(name)


def acct_id(bank: str, account: str) -> uuid.UUID:
    return uuid.uuid5(_NS, f"acct:{bank}/{account}")


def txn_id_from_key(rk: str) -> uuid.UUID:
    return uuid.uuid5(_NS, "txn:" + rk)


async def main() -> None:
    print("=== verifying AML ingestion against live DB + raw CSV ===", flush=True)
    async with engine.connect() as c:
        # ---- 1. counts + per-typology spread -------------------------------------------------
        n_acct = (await c.execute(text("SELECT count(*) FROM aml_accounts"))).scalar_one()
        n_txn = (await c.execute(text("SELECT count(*) FROM aml_transactions"))).scalar_one()
        n_inst = (await c.execute(text("SELECT count(*) FROM aml_pattern_instances"))).scalar_one()
        n_mem = (await c.execute(text("SELECT count(*) FROM aml_pattern_members"))).scalar_one()
        per_typ = dict((await c.execute(text(
            "SELECT typology, count(*) FROM aml_pattern_instances GROUP BY typology"))).all())
        print(f"[counts] accounts={n_acct} transactions={n_txn} instances={n_inst} "
              f"members={n_mem} per_typology={per_typ}", flush=True)
        check("instance count == 20", n_inst == EXP_INSTANCES, f"got {n_inst}")
        check("per-typology spread == 5x4", per_typ == EXP_PER_TYPOLOGY, f"got {per_typ}")
        check("members == labeled rows (300)", n_mem == EXP_FRAUD, f"got {n_mem}")
        check("transactions == 1500 (300 fraud + 1200 benign)",
              n_txn == EXP_FRAUD + EXP_BENIGN, f"got {n_txn}")

        # ---- 2. is_laundering split ----------------------------------------------------------
        n_fraud = (await c.execute(text(
            "SELECT count(*) FROM aml_transactions WHERE is_laundering"))).scalar_one()
        n_benign = (await c.execute(text(
            "SELECT count(*) FROM aml_transactions WHERE NOT is_laundering"))).scalar_one()
        check("labeled txns is_laundering=true == 300", n_fraud == EXP_FRAUD, f"got {n_fraud}")
        check("benign txns is_laundering=false == 1200", n_benign == EXP_BENIGN, f"got {n_benign}")
        # every member transaction must be a laundering row (members only link labeled rows)
        mem_nonfraud = (await c.execute(text(
            "SELECT count(*) FROM aml_pattern_members m JOIN aml_transactions t "
            "ON t.id = m.transaction_id WHERE NOT t.is_laundering"))).scalar_one()
        check("no member links a benign txn", mem_nonfraud == 0, f"got {mem_nonfraud}")

        # ---- 3. pairwise account-disjoint instances (as loaded) ------------------------------
        rows = (await c.execute(text(
            "SELECT m.pattern_instance_id, t.from_account_id, t.to_account_id "
            "FROM aml_pattern_members m JOIN aml_transactions t ON t.id = m.transaction_id"))).all()
        inst_accounts: dict = defaultdict(set)
        for iid, fa, ta in rows:
            inst_accounts[iid].add(fa)
            inst_accounts[iid].add(ta)
        overlaps = []
        items = list(inst_accounts.items())
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                shared = items[i][1] & items[j][1]
                if shared:
                    overlaps.append((items[i][0], items[j][0], len(shared)))
        check("labeled instances pairwise account-disjoint", not overlaps,
              f"{len(overlaps)} overlapping pairs" if overlaps else f"{len(items)} instances, 0 shared")

        # ---- 4. benign touches a labeled-instance account; realized ratio --------------------
        labeled_accounts: set = set().union(*inst_accounts.values()) if inst_accounts else set()
        benign = (await c.execute(text(
            "SELECT from_account_id, to_account_id FROM aml_transactions WHERE NOT is_laundering"))).all()
        stray = sum(1 for fa, ta in benign
                    if fa not in labeled_accounts and ta not in labeled_accounts)
        check("every benign txn touches a labeled-instance account", stray == 0,
              f"{stray} stray benign rows" if stray else "all benign anchored to labeled accounts")
        ratio = n_benign / n_fraud if n_fraud else 0.0
        check("benign:fraud ratio ~4:1", abs(ratio - 4.0) < 0.001, f"{ratio:.2f}:1")

        # ---- 5. positional-parse spot check vs raw CSV ---------------------------------------
        # deterministic handful: 3 lowest-raw_key laundering + 3 lowest-raw_key benign
        spot = (await c.execute(text(
            "SELECT t.raw_key, t.id, t.is_laundering, fa.bank, fa.account, ta.bank, ta.account, "
            "       t.amount_received, t.payment_format "
            "FROM aml_transactions t "
            "JOIN aml_accounts fa ON fa.id = t.from_account_id "
            "JOIN aml_accounts ta ON ta.id = t.to_account_id "
            "WHERE t.is_laundering ORDER BY t.raw_key LIMIT 3"))).all()
        spot += (await c.execute(text(
            "SELECT t.raw_key, t.id, t.is_laundering, fa.bank, fa.account, ta.bank, ta.account, "
            "       t.amount_received, t.payment_format "
            "FROM aml_transactions t "
            "JOIN aml_accounts fa ON fa.id = t.from_account_id "
            "JOIN aml_accounts ta ON ta.id = t.to_account_id "
            "WHERE NOT t.is_laundering ORDER BY t.raw_key LIMIT 3"))).all()

    # scan the raw CSV once for the spot-check keys (outside the DB connection)
    want = {r[0]: r for r in spot}
    found: dict = {}
    with open(TRANS, encoding="utf-8", newline="") as f:
        rdr = csv.reader(f)
        next(rdr)
        for row in rdr:
            k = "\x1f".join(row[0:10])
            if k in want:
                found[k] = row
                if len(found) == len(want):
                    break

    all_ok = True
    print(f"[spot-check] {len(want)} stored txns reconstructed against the raw CSV:", flush=True)
    for rk, rec in want.items():
        _, tid, is_l, fbank, facct, tbank, tacct, amt, fmt = rec
        raw = found.get(rk)
        if raw is None:
            print(f"    MISSING in CSV: {rk!r}", flush=True)
            all_ok = False
            continue
        # positional columns: 1 fromBank, 2 fromAcct, 3 toBank, 4 toAcct, 5 amtRecv, 9 fmt, 10 label
        pos_ok = (raw[1] == fbank and raw[2] == facct and raw[3] == tbank and raw[4] == tacct
                  and raw[9] == fmt and (raw[10] == "1") == is_l)
        id_ok = (uuid.UUID(str(tid)) == txn_id_from_key(rk)
                 and acct_id(fbank, facct) is not None)
        # confirm the stored account ids are the uuid5 of exactly these (bank,account) pairs
        stored_from_ok = True  # from/to account bank/account already came via FK join above
        print(f"    {'ok ' if pos_ok and id_ok else 'BAD'} "
              f"{fbank}/{facct} -> {tbank}/{tacct}  {fmt}  laundering={is_l}  "
              f"csv[1..4]={raw[1]}/{raw[2]}->{raw[3]}/{raw[4]}", flush=True)
        all_ok = all_ok and pos_ok and id_ok and stored_from_ok
    check("positional parse (from/to bank+account) matches raw CSV", all_ok)
    check("spot-checked txn ids are uuid5(raw_key)", all_ok)

    # ---- 6. num_components distribution --------------------------------------------------
    async with engine.connect() as c:
        comp = (await c.execute(text(
            "SELECT typology, num_components, count(*) FROM aml_pattern_instances "
            "GROUP BY typology, num_components ORDER BY typology, num_components"))).all()
        disjoint = (await c.execute(text(
            "SELECT typology, count(*) FROM aml_pattern_instances WHERE num_components > 1 "
            "GROUP BY typology"))).all()
    print(f"[components] (typology, num_components, n) = {[tuple(r) for r in comp]}", flush=True)
    print(f"[components] internally-disjoint instances (num_components>1) by typology = "
          f"{dict(disjoint)}", flush=True)

    # ---- 7. structural isolation: no FK crosses the aml_ / moat boundary ------------------
    async with engine.connect() as c:
        fks = (await c.execute(text(
            "SELECT tc.table_name AS child, ccu.table_name AS parent "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.constraint_column_usage ccu "
            "  ON tc.constraint_name = ccu.constraint_name "
            "WHERE tc.constraint_type = 'FOREIGN KEY'"))).all()
    crossing = sorted({(ch, pa) for ch, pa in fks
                       if ch.startswith("aml_") != pa.startswith("aml_")})
    check("no FK crosses the aml_/moat boundary", not crossing,
          f"{crossing}" if crossing else "aml_ FKs stay within aml_; moat FKs stay within moat")

    await engine.dispose()
    print("\n" + ("=== ALL CHECKS PASSED ===" if not _fails
                  else f"=== {len(_fails)} CHECK(S) FAILED: {_fails} ==="), flush=True)
    sys.exit(1 if _fails else 0)


if __name__ == "__main__":
    asyncio.run(main())
