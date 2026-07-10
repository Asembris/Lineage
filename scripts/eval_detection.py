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

import numpy as np

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

def per_instance_detection(ext: Extract) -> dict:
    """For each selected instance, how many of its labeled edges its OWN-typology witness fires on.
    A different, more intuitive evaluation unit than per-edge: 'caught N/5 rings'."""
    g = ext.graph
    res: dict = {}
    for b in ext.instances:
        typ = b["typology"]
        fn = aml_graph.WITNESS[typ]
        ids = [ingest.txn_id(ingest.raw_key(tuple(r["n_rows"][0:10]))) for r in b["rows"]]
        fired = sum(1 for tid in ids if fn(g, g.by_id[tid]).outcome is Outcome.MATCH)
        res.setdefault(typ, []).append((b["instance_index"], len(ids), fired))
    return res


def report_set(name: str, scores: dict, per_inst: dict, dev_scores: dict | None = None) -> None:
    print(f"\n=== {name} ===", flush=True)
    print("per-edge precision / recall (95% Wilson CI), all four typologies:", flush=True)
    for typ in TARGET:
        (own, own_t), (cross, cross_t), (benign, benign_t) = scores[typ]
        precision, recall = precision_recall(own, cross, benign, own_t)
        _, pl, ph = wilson_ci(own, own + cross + benign)
        _, rl, rh = wilson_ci(own, own_t)
        cap = "FLAG-capable" if typ in aml_graph.FLAG_CAPABLE else "not flag-capable"
        line = (f"  {typ:16s} recall {recall:5.1%} [{rl:.1%},{rh:.1%}] ({own}/{own_t})   "
                f"precision {precision:5.1%} [{pl:.1%},{ph:.1%}] ({own}/{own + cross + benign})   "
                f"cross={cross}/{cross_t} benign_FP={benign}/{benign_t}  [{cap}]")
        print(line, flush=True)

    sound = scores["_sound"]
    verdict = "REPLICATES" if sound == set(aml_graph.FLAG_CAPABLE) else "DIVERGES"
    print(f"soundness replication: measured-sound (0 cross-typology false witness) = {sorted(sound)} "
          f"vs FLAG_CAPABLE {sorted(aml_graph.FLAG_CAPABLE)} -> {verdict}", flush=True)

    print("per-instance detection (own-typology witness fires on how many of each ring's edges):", flush=True)
    for typ in TARGET:
        rows = per_inst.get(typ, [])
        any_hit = sum(1 for _, n, f in rows if f > 0)
        all_hit = sum(1 for _, n, f in rows if f == n and n > 0)
        edges_fired = sum(f for _, _, f in rows)
        edges_total = sum(n for _, n, _ in rows)
        detail = ", ".join(f"i{idx}:{f}/{n}" for idx, n, f in rows)
        print(f"  {typ:16s} >=1 edge: {any_hit}/{len(rows)} rings   all edges: {all_hit}/{len(rows)}   "
              f"edges {edges_fired}/{edges_total}   [{detail}]", flush=True)


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


# --- non-structural baseline (fit per set, oracle-advantaged, best-F1 operating point) -------
# Deliberately the FAIREST possible strawman: it sees the labels of its OWN set and is scored at
# its best threshold, so it cannot be accused of being rigged to lose. Fit INDEPENDENTLY within
# each set -- the dev fit is never transferred to score the hold-out (Item 7 confirmation #3).

def _best_f1(scores: np.ndarray, y: np.ndarray) -> tuple[float, float, float]:
    """Best precision/recall/F1 over all thresholds `flag if score >= t`. Deterministic."""
    P = int(y.sum())
    if P == 0:
        return (0.0, 0.0, 0.0)
    order = np.argsort(-scores, kind="mergesort")
    ys, ss = y[order], scores[order]
    tp = fp = 0
    best = (0.0, 0.0, 0.0)
    i, n = 0, len(ys)
    while i < n:
        j = i
        while j < n and ss[j] == ss[i]:
            if ys[j] == 1:
                tp += 1
            else:
                fp += 1
            j += 1
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / P
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        if f1 > best[2]:
            best = (prec, rec, f1)
        i = j
    return best


def _target_encode(values: list[str], y: np.ndarray) -> np.ndarray:
    """Category -> its positive rate on THIS set (oracle-advantaged encoding of a categorical)."""
    pos, tot = Counter(), Counter()
    for v, yi in zip(values, y):
        tot[v] += 1
        pos[v] += int(yi)
    rate = {v: pos[v] / tot[v] for v in tot}
    return np.array([rate[v] for v in values], dtype=float)


def best_single_feature_rule(feats: list[dict], y: np.ndarray) -> tuple[str, tuple]:
    """The literal 'raw amount/currency/format threshold' baseline: best single-feature rule."""
    best_name, best = "none", (0.0, 0.0, 0.0)
    ap = np.array([f["amount_paid"] for f in feats], dtype=float)
    ar = np.array([f["amount_received"] for f in feats], dtype=float)
    candidates = {
        "amount_paid>=t": ap, "amount_paid<=t": -ap,
        "amount_received>=t": ar, "amount_received<=t": -ar,
        "payment_format": _target_encode([f["payment_format"] for f in feats], y),
        "payment_currency": _target_encode([f["payment_currency"] for f in feats], y),
        "receiving_currency": _target_encode([f["receiving_currency"] for f in feats], y),
    }
    for name, score in candidates.items():
        r = _best_f1(score, y)
        if r[2] > best[2]:
            best_name, best = name, r
    return best_name, best


def logistic_regression_baseline(feats: list[dict], y: np.ndarray) -> tuple[float, float, float]:
    """A non-structural logistic regression over ALL raw fields (given every advantage: fit on
    this set, best-F1 threshold). Deterministic: zero init, fixed lr/iters, numpy only."""
    ap = np.log1p(np.array([f["amount_paid"] for f in feats], dtype=float))
    ar = np.log1p(np.array([f["amount_received"] for f in feats], dtype=float))

    def _std(x):
        s = x.std()
        return (x - x.mean()) / s if s > 0 else x * 0.0

    cols = [_std(ap), _std(ar)]
    for key in ("payment_format", "payment_currency", "receiving_currency"):
        vals = [f[key] for f in feats]
        for cat in sorted(set(vals)):
            cols.append(np.array([1.0 if v == cat else 0.0 for v in vals]))
    X = np.column_stack([np.ones(len(feats))] + cols)
    w = np.zeros(X.shape[1])
    m = len(y)
    for _ in range(3000):
        p = 1.0 / (1.0 + np.exp(-X @ w))
        w -= 0.2 * (X.T @ (p - y) / m + 1e-4 * w)
    scores = 1.0 / (1.0 + np.exp(-X @ w))
    return _best_f1(scores, y)


def head_to_head(ext: Extract) -> dict:
    """Binary task: separate FLAG-capable ring members (CYCLE/SG) from the benign noise, using
    (structure) vs (raw fields only). GATHER-SCATTER/STACK edges are excluded -- they are neither
    the target positive nor benign."""
    g = ext.graph
    y_list, feats = [], []
    tp = fp = fn = 0
    for e in g.by_id.values():
        lab = ext.labels.get(e.id)
        if lab in ("CYCLE", "SCATTER-GATHER"):
            yi = 1
        elif lab is None:
            yi = 0
        else:
            continue  # GS / STACK: out of the flag-capable positive class
        fired = (aml_graph.cycle_witness(g, e).outcome is Outcome.MATCH
                 or aml_graph.scatter_gather_witness(g, e).outcome is Outcome.MATCH)
        if fired and yi == 1:
            tp += 1
        elif fired and yi == 0:
            fp += 1
        elif not fired and yi == 1:
            fn += 1
        y_list.append(yi)
        feats.append(ext.features[e.id])
    y = np.array(y_list, dtype=float)
    det_p = tp / (tp + fp) if (tp + fp) else 0.0
    det_r = tp / (tp + fn) if (tp + fn) else 0.0
    det_f1 = 2 * det_p * det_r / (det_p + det_r) if (det_p + det_r) else 0.0
    return {
        "n_pos": int(y.sum()), "n_neg": int((1 - y).sum()),
        "structural": (det_p, det_r, det_f1),
        "single_rule": best_single_feature_rule(feats, y),
        "logreg": logistic_regression_baseline(feats, y),
    }


def report_head_to_head(name: str, h2h: dict) -> None:
    print(f"\n--- {name}: structural vs non-structural baselines "
          f"(CYCLE/SG members vs benign; pos={h2h['n_pos']} neg={h2h['n_neg']}) ---", flush=True)
    p, r, f = h2h["structural"]
    print(f"  structural (CYCLE or SG witness)      precision {p:5.1%}  recall {r:5.1%}  F1 {f:5.1%}",
          flush=True)
    rn, (rp, rr, rf) = h2h["single_rule"]
    print(f"  best single raw-feature rule          precision {rp:5.1%}  recall {rr:5.1%}  F1 {rf:5.1%}"
          f"   [{rn}]", flush=True)
    lp, lrc, lf = h2h["logreg"]
    print(f"  logistic regression (all raw fields)  precision {lp:5.1%}  recall {lrc:5.1%}  F1 {lf:5.1%}",
          flush=True)


def report_format_crosstab(name: str, ext: Extract) -> None:
    """Root-cause diagnostic for the baseline's near-100% recall: payment_format x label on the
    eval set. If all positives concentrate in one format while benign spans many, the baseline's
    'signal' is that separation -- a synthetic-generation + sampling artifact, not real behaviour."""
    pos, neg = Counter(), Counter()
    for e in ext.graph.by_id.values():
        lab = ext.labels.get(e.id)
        fmt = ext.features[e.id]["payment_format"]
        if lab in ("CYCLE", "SCATTER-GATHER"):
            pos[fmt] += 1
        elif lab is None:
            neg[fmt] += 1
    print(f"\n--- {name}: payment_format x label (root-cause of the baseline's recall) ---", flush=True)
    print(f"  {'format':14s} {'CYCLE/SG pos':>12s} {'benign neg':>11s} {'pos-rate':>9s}", flush=True)
    for f in sorted(set(pos) | set(neg)):
        p, n = pos[f], neg[f]
        print(f"  {f:14s} {p:12d} {n:11d} {(p / (p + n) if p + n else 0):8.1%}", flush=True)
    only = [f for f in pos if pos[f]]
    print(f"  -> all {sum(pos.values())} positives use formats {only}; benign spans "
          f"{len([f for f in neg if neg[f]])} formats. Separation is a generator+sampling artifact, "
          f"not real behaviour (see NOTES Item 7).", flush=True)


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

    # HOLD-OUT — fresh, account-disjoint from the original 20 and from each other. Never tuned.
    dev_nodes = set().union(*(b["nodes"] for b in dev_sel))
    hold_sel, hold_per = select_disjoint(blocks, PER_TYPOLOGY, reserved=dev_nodes)
    hold_nodes = set()
    for b in hold_sel:
        assert not (b["nodes"] & dev_nodes), "hold-out instance overlaps the dev set — not a hold-out"
        assert not (b["nodes"] & hold_nodes), "hold-out instances overlap each other"
        hold_nodes |= b["nodes"]
    print(f"\n[hold-out] selected {len(hold_sel)} fresh instances per_typology={dict(hold_per)} "
          f"idx={{{', '.join(str(b['instance_index']) for b in hold_sel)}}}", flush=True)

    hold = build_extract("hold-out", hold_sel)
    hold_scores = score_witnesses(hold)

    report_set("DEVELOPMENT SET (in-sample — design decisions saw this; NOT a hold-out)",
               dev_scores, per_instance_detection(dev))
    report_set("HOLD-OUT (fresh, account-disjoint, NEVER tuned — the headline)",
               hold_scores, per_instance_detection(hold))

    # Structural detector vs a non-structural baseline, on the same head-to-head task, each set fit
    # independently (dev fit never transferred to the hold-out).
    report_head_to_head("DEVELOPMENT SET", head_to_head(dev))
    report_head_to_head("HOLD-OUT (never tuned)", head_to_head(hold))
    report_format_crosstab("DEVELOPMENT SET", dev)
    report_format_crosstab("HOLD-OUT (never tuned)", hold)

    print("\nNOTE: numbers are RING/TYPOLOGY detection against pattern-membership ground truth, on "
          "Item 1's deliberately adversarial benign set (noise anchored to the same accounts). Not "
          "general fraud detection, not absolute detector performance. FLAG-capable headline is "
          "CYCLE + SCATTER-GATHER; GATHER-SCATTER/STACK are reported only to re-test soundness.",
          flush=True)


if __name__ == "__main__":
    main()
