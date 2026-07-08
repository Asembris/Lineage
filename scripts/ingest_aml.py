"""scripts/ingest_aml.py — load a bounded, verified AML subgraph into defaultdb (Roadmap Item 1).

Deterministic and IDEMPOTENT (safe to re-run): identities are uuid5 of natural keys and every
insert is ON CONFLICT DO NOTHING, so a re-run converges without wiping. Wired into NO reseed
path — run it by hand. Static reference data; seed.seed()/backfill never touch aml_* (no inbound
FKs, so their TRUNCATE ... CASCADE can't reach these).

REUSES the spike's solved hard parts (scripts/probe_aml.py): the positional CSV parse around the
duplicate "Account" header (probe_aml.parse) and the exact-tuple Patterns<->Trans join. It only
adds header-param capture, instance selection, benign-neighbour sampling, and the DB load.

Selection (see NOTES.md "Roadmap Item 1"):
  * 4 structurally-rich, zero-degenerate typologies: CYCLE, SCATTER-GATHER, GATHER-SCATTER, STACK.
  * non-degenerate floor: >= MIN_ACCOUNTS distinct accounts (filters single-txn blocks AND the
    2-node ping-pong artifacts whose "Max N" label lies about size).
  * mutually account-disjoint across ALL selected instances (a genuinely bounded labeled subgraph).
  * up to PER_TYPOLOGY per typology; realized count reported (some typologies may yield fewer).
Benign noise: is_laundering=0 transactions TOUCHING the selected accounts (the meaningful,
adversarial precision negatives — they share nodes with the positives), per-account capped and
globally capped at ~RATIO_TARGET:1 benign:fraud.

Run:  PYTHONPATH=. .venv/Scripts/python.exe scripts/ingest_aml.py
"""

import asyncio
import csv
import datetime as dt
import importlib.util
import pathlib
import sys
import time
import uuid
from collections import Counter, defaultdict
from decimal import Decimal

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import text  # noqa: E402

from app.aml_models import SOURCE  # noqa: E402
from app.db import engine  # noqa: E402

# --- reuse probe_aml (scripts/ is not a package): load it by file path -----------------------
_spec = importlib.util.spec_from_file_location(
    "probe_aml", pathlib.Path(__file__).with_name("probe_aml.py")
)
probe_aml = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe_aml)  # type: ignore[union-attr]
PATTERNS, TRANS = probe_aml.PATTERNS, probe_aml.TRANS

# --- selection knobs (stated design intent; verification re-derives the ACTUAL values) -------
TARGET_TYPOLOGIES = ["CYCLE", "SCATTER-GATHER", "GATHER-SCATTER", "STACK"]
MIN_ACCOUNTS = 4          # non-degenerate floor on ACTUAL distinct accounts
PER_TYPOLOGY = 5          # target isolated instances per typology
RATIO_TARGET = 4          # target benign:fraud transaction ratio
PER_ACCOUNT_BENIGN_CAP = 8  # cap benign rows charged to any one account (no hub domination)

_NS = uuid.uuid5(uuid.NAMESPACE_DNS, "lineage.aml")


def acct_id(node: tuple[str, str]) -> uuid.UUID:
    return uuid.uuid5(_NS, f"acct:{node[0]}/{node[1]}")


def raw_key(row10: tuple[str, ...]) -> str:
    return "\x1f".join(row10)  # unit-separator join of the 10 non-label columns


def txn_id(rk: str) -> uuid.UUID:
    return uuid.uuid5(_NS, "txn:" + rk)


def inst_id(instance_index: int) -> uuid.UUID:
    return uuid.uuid5(_NS, f"inst:{SOURCE}:{instance_index}")


def member_id(instance: uuid.UUID, txn: uuid.UUID) -> uuid.UUID:
    return uuid.uuid5(_NS, f"member:{instance}:{txn}")


def parse_ts(s: str) -> dt.datetime:
    # Source is minute-resolution 'YYYY/MM/DD HH:MM', no tz -> stored UTC by convention.
    return dt.datetime.strptime(s, "%Y/%m/%d %H:%M").replace(tzinfo=dt.timezone.utc)


def load_blocks_with_header():
    """Like probe_aml.load_blocks but ALSO captures the generator's 'Max N' header param and the
    block's global position (instance_index). Row parsing reuses probe_aml.parse verbatim."""
    blocks, cur, idx = [], None, -1
    with open(PATTERNS, encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if line.startswith("BEGIN LAUNDERING ATTEMPT"):
                idx += 1
                header = line.split(" - ", 1)[1]  # e.g. "FAN-OUT:  Max 16-degree Fan-Out"
                typ = header.split(":", 1)[0].strip()
                max_param = header.split(":", 1)[1].strip() if ":" in header else ""
                cur = {"typology": typ, "max_param": max_param, "instance_index": idx,
                       "rows": [], "nodes": set()}
            elif line.startswith("END LAUNDERING ATTEMPT"):
                blocks.append(cur)
                cur = None
            elif line.strip() and cur is not None:
                p = probe_aml.parse(line)
                cur["rows"].append(p)
                cur["nodes"].add(p["from_node"])
                cur["nodes"].add(p["to_node"])
    return blocks


def count_components(rows) -> int:
    """Weakly-connected components among an instance's edges (from_node -> to_node)."""
    idx: dict = {}
    parent: list[int] = []

    def node(n):
        if n not in idx:
            idx[n] = len(parent)
            parent.append(len(parent))
        return idx[n]

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for r in rows:
        a, b = node(r["from_node"]), node(r["to_node"])
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    return len({find(i) for i in range(len(parent))})


def select_instances(blocks):
    """Greedy, file-order, mutually account-disjoint selection across the target typologies."""
    used_nodes: set = set()
    selected = []
    per_typ = Counter()
    for typ in TARGET_TYPOLOGIES:
        for b in blocks:  # file order == instance_index order
            if b["typology"] != typ:
                continue
            if per_typ[typ] >= PER_TYPOLOGY:
                break
            if len(b["nodes"]) < MIN_ACCOUNTS:
                continue
            if b["nodes"] & used_nodes:
                continue  # overlaps an already-selected instance -> not isolated, skip
            selected.append(b)
            used_nodes |= b["nodes"]
            per_typ[typ] += 1
    return selected, per_typ


def txn_row_record(row10: tuple[str, ...], is_laundering: bool) -> dict:
    """Build an aml_transactions insert dict from the 10 non-label columns."""
    rk = raw_key(row10)
    return {
        "id": txn_id(rk),
        "ts": parse_ts(row10[0]),
        "from_account_id": acct_id((row10[1], row10[2])),
        "to_account_id": acct_id((row10[3], row10[4])),
        "amount_received": Decimal(row10[5]),
        "receiving_currency": row10[6],
        "amount_paid": Decimal(row10[7]),
        "payment_currency": row10[8],
        "payment_format": row10[9],
        "is_laundering": is_laundering,
        "raw_key": rk,
    }


def stream_csv(selected_keys: set, target_accounts: set, global_benign_cap: int):
    """ONE pass over the 470MB CSV: (1) confirm the join integrity of the selected labeled keys,
    (2) sample benign (is_laundering=0) rows touching a target account, per-account + globally
    capped. Never loads the file whole."""
    hits = Counter()                       # selected_key -> CSV match count (expect exactly 1 each)
    per_acct = Counter()                   # target_account -> benign rows charged so far
    benign_rows: list[tuple[str, ...]] = []  # collected 10-col tuples
    seen_benign_key: set = set()           # dedup benign by raw_key
    n_csv = 0
    t0 = time.perf_counter()
    with open(TRANS, encoding="utf-8", newline="") as f:
        rdr = csv.reader(f)
        next(rdr)  # header
        for row in rdr:
            n_csv += 1
            if n_csv % 1_000_000 == 0:
                print(f"  ...scanned {n_csv:,} rows ({time.perf_counter() - t0:.0f}s)", flush=True)
            k10 = tuple(row[0:10])
            key = "\x1f".join(k10)
            if key in selected_keys:
                hits[key] += 1
            if row[10] == "1":
                continue  # laundering rows are never benign noise
            if len(benign_rows) >= global_benign_cap:
                continue
            frm, to = (row[1], row[2]), (row[3], row[4])
            touched = [a for a in (frm, to) if a in target_accounts]
            if not touched:
                continue
            if all(per_acct[a] >= PER_ACCOUNT_BENIGN_CAP for a in touched):
                continue  # every account this row touches is already at its cap
            if key in seen_benign_key:
                continue
            seen_benign_key.add(key)
            benign_rows.append(k10)
            for a in touched:
                per_acct[a] += 1
    return hits, benign_rows, n_csv


async def load(accounts: dict, txns: dict, instances: list, members: list) -> None:
    """Insert in FK order, all ON CONFLICT DO NOTHING (idempotent). Small volume -> one txn."""
    acct_rows = [{"id": aid, "bank": node[0], "account": node[1], "source": SOURCE}
                 for node, aid in accounts.items()]
    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO aml_accounts (id, bank, account, source) "
                 "VALUES (:id, :bank, :account, :source) "
                 "ON CONFLICT (bank, account) DO NOTHING"),
            acct_rows,
        )
        await conn.execute(
            text("INSERT INTO aml_transactions "
                 "(id, ts, from_account_id, to_account_id, amount_received, receiving_currency, "
                 " amount_paid, payment_currency, payment_format, is_laundering, raw_key) "
                 "VALUES (:id, :ts, :from_account_id, :to_account_id, :amount_received, "
                 " :receiving_currency, :amount_paid, :payment_currency, :payment_format, "
                 " :is_laundering, :raw_key) "
                 "ON CONFLICT (raw_key) DO NOTHING"),
            list(txns.values()),
        )
        await conn.execute(
            text("INSERT INTO aml_pattern_instances "
                 "(id, typology, max_param, source, instance_index, num_rows, num_accounts, "
                 " num_components) "
                 "VALUES (:id, :typology, :max_param, :source, :instance_index, :num_rows, "
                 " :num_accounts, :num_components) "
                 "ON CONFLICT (source, instance_index) DO NOTHING"),
            instances,
        )
        await conn.execute(
            text("INSERT INTO aml_pattern_members "
                 "(id, pattern_instance_id, transaction_id, hop_index) "
                 "VALUES (:id, :pattern_instance_id, :transaction_id, :hop_index) "
                 "ON CONFLICT (pattern_instance_id, transaction_id) DO NOTHING"),
            members,
        )


async def main() -> None:
    print("=== AML ingestion (Roadmap Item 1) ===", flush=True)
    blocks = load_blocks_with_header()
    print(f"[patterns] {len(blocks)} labeled blocks parsed", flush=True)

    selected, per_typ = select_instances(blocks)
    fraud_rows = sum(len(b["rows"]) for b in selected)
    print(f"[select] {len(selected)} instances  per_typology={dict(per_typ)}  "
          f"labeled(fraud) rows={fraud_rows}", flush=True)

    # accounts + labeled transactions + instances + members from the selected blocks
    accounts: dict = {}
    txns: dict = {}     # raw_key -> insert dict
    instances: list = []
    members: list = []
    selected_keys: set = set()
    for b in selected:
        iid = inst_id(b["instance_index"])
        instances.append({
            "id": iid,
            "typology": b["typology"],
            "max_param": b["max_param"],
            "source": SOURCE,
            "instance_index": b["instance_index"],
            "num_rows": len(b["rows"]),
            "num_accounts": len(b["nodes"]),
            "num_components": count_components(b["rows"]),
        })
        for node in b["nodes"]:
            accounts.setdefault(node, acct_id(node))
        for hop, r in enumerate(b["rows"]):
            row10 = tuple(r["n_rows"][0:10])
            rk = raw_key(row10)
            selected_keys.add(rk)
            rec = txn_row_record(row10, is_laundering=True)
            txns.setdefault(rk, rec)
            members.append({
                "id": member_id(iid, rec["id"]),
                "pattern_instance_id": iid,
                "transaction_id": rec["id"],
                "hop_index": hop,
            })

    target_accounts = set(accounts)  # only labeled-instance accounts anchor benign sampling
    global_benign_cap = RATIO_TARGET * fraud_rows
    print(f"[csv] scanning {TRANS} once (benign cap: {PER_ACCOUNT_BENIGN_CAP}/account, "
          f"{global_benign_cap} total) ...", flush=True)
    hits, benign_rows, n_csv = stream_csv(selected_keys, target_accounts, global_benign_cap)

    # Join integrity — the labeled keys MUST each resolve to exactly one CSV row.
    matched = sum(1 for k in selected_keys if hits[k] >= 1)
    collisions = sum(1 for k in selected_keys if hits[k] > 1)
    print(f"[join] labeled keys={len(selected_keys)} matched={matched} collisions={collisions} "
          f"(csv rows scanned={n_csv:,})", flush=True)
    if matched != len(selected_keys) or collisions:
        raise SystemExit("JOIN INTEGRITY FAILED — selected labeled rows are not 1:1 with the CSV")

    # benign transactions + their (possibly new) counterparty accounts
    for row10 in benign_rows:
        rk = raw_key(row10)
        rec = txn_row_record(row10, is_laundering=False)
        txns.setdefault(rk, rec)
        accounts.setdefault((row10[1], row10[2]), acct_id((row10[1], row10[2])))
        accounts.setdefault((row10[3], row10[4]), acct_id((row10[3], row10[4])))

    n_benign = len(benign_rows)
    ratio = n_benign / fraud_rows if fraud_rows else 0.0
    print(f"[benign] collected={n_benign}  benign:fraud={ratio:.2f}:1  "
          f"accounts(total)={len(accounts)}  transactions(total)={len(txns)}", flush=True)

    print("[load] inserting (ON CONFLICT DO NOTHING) ...", flush=True)
    await load(accounts, txns, instances, members)
    await engine.dispose()
    print("=== ingestion complete ===", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
