"""scripts/eval_detection.py — Item 7 forensic-detection eval (offline, deterministic, read-only).

Measures the AML structural-witness detector (app/services/aml_graph.py — NOT the agents/
belief_inheritance graph; terminology collision resolved by Items 4 & 5) as precision/recall
against IBM's pattern-typology ground truth, on TWO sets:

  * DEVELOPMENT set  — Item 1's original 20 instances (the extract every design decision was made
                       against). Reproduced in-memory here purely as a FIDELITY GATE: if the
                       reconstruction yields Item 4's asserted counts exactly, the pipeline is
                       faithful to the persisted extract and can be trusted on unseen data.
  * HOLD-OUT set     — fresh, account-disjoint instances that NO design decision ever saw
                       (disjoint from the original 20 and from each other). This is the genuinely
                       never-tuned number. [added in a later commit]

Everything is DETERMINISTIC and READ-ONLY: instance selection walks Patterns.txt in file order
(reusing Item 1's exact select logic); benign noise is sampled in CSV file order under fixed caps
(no randomness); ids are uuid5 of natural keys; witnesses sort by str(id) internally. NOTHING is
written to aml_* — aml_graph.load_graph() reads the whole table, so persisting fresh rows would
change the graph the dev numbers/console/interrogate all operate on. The extract is built in memory
from the CSV; Item 1's already-pushed ingestion is never touched.

GROUND TRUTH IS THE ORACLE, NEVER AN INPUT: the witness functions select no label column. Labels
(pattern membership) are used only to SCORE, exactly as tests/test_aml_brake.py does.

Run:  PYTHONIOENCODING=utf-8 PYTHONPATH=. .venv/Scripts/python.exe scripts/eval_detection.py
"""

from __future__ import annotations

import importlib.util
import math
import pathlib
import sys
from collections import Counter
from decimal import Decimal

if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# --- reuse Item 1's ingestion methodology verbatim (scripts/ is not a package) ---------------
_spec = importlib.util.spec_from_file_location(
    "ingest_aml", pathlib.Path(__file__).with_name("ingest_aml.py")
)
ingest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ingest)  # type: ignore[union-attr]

from app.services import aml_graph  # noqa: E402
from app.services.aml_graph import Edge, Graph, Outcome  # noqa: E402

TARGET = ingest.TARGET_TYPOLOGIES          # ["CYCLE","SCATTER-GATHER","GATHER-SCATTER","STACK"]
MIN_ACCOUNTS = ingest.MIN_ACCOUNTS         # 4
PER_TYPOLOGY = ingest.PER_TYPOLOGY         # 5
RATIO_TARGET = ingest.RATIO_TARGET         # 4
PER_ACCOUNT_BENIGN_CAP = ingest.PER_ACCOUNT_BENIGN_CAP  # 8

# Item 4's asserted development-set counts (tests/test_aml_brake.py ORACLE). The dev reconstruction
# below MUST reproduce these byte-for-byte, or the pipeline is not faithful and nothing downstream
# can be trusted.  typology -> (own_hits,own_total), (cross_hits,cross_total), (benign_hits,benign_total)
DEV_ORACLE = {
    "CYCLE":          ((43, 43), (0, 257), (14, 1200)),
    "SCATTER-GATHER": ((39, 96), (0, 204), (3, 1200)),
    "GATHER-SCATTER": ((64, 77), (6, 223), (37, 1200)),
    "STACK":          ((6, 84), (27, 216), (2, 1200)),
}


# --- pure helpers (unit-tested, no app/CSV/OpenAI) -------------------------------------------

def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    """95% Wilson score interval for a binomial proportion. Returns (point, lo, hi)."""
    if n == 0:
        return (0.0, 0.0, 0.0)
    p = k / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (p, max(0.0, center - half), min(1.0, center + half))


def precision_recall(own: int, cross: int, benign: int, own_t: int) -> tuple[float, float]:
    """precision = correct fires / all fires; recall = own fires / own labeled total."""
    fires = own + cross + benign
    precision = own / fires if fires else 0.0
    recall = own / own_t if own_t else 0.0
    return precision, recall


# --- extract reconstruction (in-memory, from the CSV; writes nothing) ------------------------

class Extract:
    """A reconstructed labeled+benign money-flow graph and its scoring oracle."""

    def __init__(self, name: str, graph: Graph, labels: dict, benign_ids: set,
                 features: dict, instances: list) -> None:
        self.name = name
        self.graph = graph
        self.labels = labels          # edge.id -> typology (labeled edges only)
        self.benign_ids = benign_ids  # edge.id set (is_laundering=0)
        self.features = features       # edge.id -> raw non-structural fields (for the baseline)
        self.instances = instances     # selected blocks, for per-instance detection later


def _edge_from_row10(row10) -> tuple[Edge, dict]:
    """Build an aml_graph.Edge and a raw-feature dict from the 10 non-label CSV columns, reusing
    Item 1's exact field derivation (ingest.txn_row_record)."""
    rec = ingest.txn_row_record(row10, is_laundering=False)  # is_laundering unused for Edge/features
    edge = Edge(
        id=rec["id"],
        src=rec["from_account_id"],
        dst=rec["to_account_id"],
        ts=rec["ts"],
        amount=rec["amount_paid"],
        payment_format=rec["payment_format"],
    )
    features = {
        "amount_paid": float(row10[7]),
        "amount_received": float(row10[5]),
        "payment_currency": row10[8],
        "receiving_currency": row10[6],
        "payment_format": row10[9],
    }
    return edge, features


def build_extract(name: str, selected: list) -> Extract:
    """Reconstruct the in-memory extract for a set of selected instances: labeled edges from the
    instances + benign noise sampled from the CSV, anchored to the labeled accounts (same caps as
    Item 1). One CSV pass. Deterministic; nothing persisted."""
    edges: dict = {}        # id -> Edge (dedup by id)
    labels: dict = {}       # id -> typology
    features: dict = {}     # id -> raw fields
    selected_keys: set = set()
    target_accounts: set = set()  # node tuples (bank, account)

    for b in selected:
        target_accounts |= b["nodes"]
        for r in b["rows"]:
            row10 = tuple(r["n_rows"][0:10])
            rk = ingest.raw_key(row10)
            selected_keys.add(rk)
            edge, feat = _edge_from_row10(row10)
            edges[edge.id] = edge
            labels[edge.id] = b["typology"]
            features[edge.id] = feat

    fraud_rows = sum(len(b["rows"]) for b in selected)
    global_benign_cap = RATIO_TARGET * fraud_rows
    hits, benign_rows, n_csv = ingest.stream_csv(selected_keys, target_accounts, global_benign_cap)

    # Join integrity: every labeled key resolves to exactly one CSV row (mirrors Item 1's 300/300).
    matched = sum(1 for k in selected_keys if hits[k] >= 1)
    collisions = sum(1 for k in selected_keys if hits[k] > 1)
    if matched != len(selected_keys) or collisions:
        raise SystemExit(f"[{name}] JOIN INTEGRITY FAILED: matched={matched}/{len(selected_keys)} "
                         f"collisions={collisions}")

    benign_ids: set = set()
    for row10 in benign_rows:
        edge, feat = _edge_from_row10(tuple(row10))
        edges[edge.id] = edge
        features[edge.id] = feat
        benign_ids.add(edge.id)

    graph = Graph(list(edges.values()))
    print(f"[{name}] built in-memory: edges={len(edges)} labeled={len(labels)} benign={len(benign_ids)} "
          f"accounts={len(graph.accounts)} (csv rows scanned={n_csv:,}, benign cap={global_benign_cap})",
          flush=True)
    return Extract(name, graph, labels, benign_ids, features, selected)


def score_witnesses(ext: Extract) -> dict:
    """Run every typology's witness over every edge; tally own/cross/benign fires against the
    oracle. Exactly the tests/test_aml_brake.py soundness loop, on the reconstructed graph."""
    g = ext.graph
    out: dict = {}
    sound = set()
    for typology in TARGET:
        fn = aml_graph.WITNESS[typology]
        own = cross = benign = 0
        own_t = cross_t = benign_t = 0
        for e in g.by_id.values():
            fired = fn(g, e).outcome is Outcome.MATCH
            lab = ext.labels.get(e.id)
            if lab is None:
                benign_t += 1
                benign += fired
            elif lab == typology:
                own_t += 1
                own += fired
            else:
                cross_t += 1
                cross += fired
        out[typology] = ((own, own_t), (cross, cross_t), (benign, benign_t))
        if cross == 0:
            sound.add(typology)
    out["_sound"] = sound
    return out


# --- selection ------------------------------------------------------------------------------

def select_disjoint(blocks, per_typology: int, reserved=frozenset()):
    """Greedy, file-order, mutually account-disjoint selection across TARGET, excluding any block
    that touches a `reserved` account. reserved=frozenset() reproduces Item 1's original 20."""
    used = set(reserved)
    per = Counter()
    sel = []
    for typ in TARGET:
        for b in blocks:
            if b["typology"] != typ:
                continue
            if per[typ] >= per_typology:
                break
            if len(b["nodes"]) < MIN_ACCOUNTS:
                continue
            if b["nodes"] & used:
                continue
            sel.append(b)
            used |= b["nodes"]
            per[typ] += 1
    return sel, per


def main() -> None:
    print("=== Item 7 detection eval ===", flush=True)
    blocks = ingest.load_blocks_with_header()

    # ORIGINAL 20 — reuse Item 1's exact selector so the dev set is identical to the persisted one.
    dev_sel, dev_per = ingest.select_instances(blocks)
    print(f"[dev] selected {len(dev_sel)} instances per_typology={dict(dev_per)}", flush=True)

    dev = build_extract("dev", dev_sel)

    # FIDELITY GATE: the reconstruction must reproduce Item 4's asserted counts exactly.
    dev_scores = score_witnesses(dev)
    print("[dev] fidelity gate vs Item 4 constants:", flush=True)
    ok = True
    for typ in TARGET:
        got = dev_scores[typ]
        exp = DEV_ORACLE[typ]
        match = got == exp
        ok = ok and match
        print(f"  {typ:16s} {'OK ' if match else 'MISMATCH'} got={got} exp={exp}", flush=True)
    if dev.graph.by_id and (len(dev.labels) != 300 or len(dev.benign_ids) != 1200):
        ok = False
        print(f"  EXTRACT SHAPE MISMATCH: labeled={len(dev.labels)} (exp 300) "
              f"benign={len(dev.benign_ids)} (exp 1200)", flush=True)
    if not ok:
        raise SystemExit("DEV FIDELITY GATE FAILED — reconstruction does not match the persisted "
                         "extract; do not trust hold-out numbers until this passes.")
    print("[dev] fidelity gate PASSED — in-memory reconstruction is faithful.", flush=True)


if __name__ == "__main__":
    main()
