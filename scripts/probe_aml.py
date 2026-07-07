"""
scripts/probe_aml.py — AML dataset modeling spike (Roadmap Item 1 pre-work, 2026-07-07).

READ-ONLY diagnostic. Answers the go/no-go question: does the IBM AML transaction
typology data fit the existing five-table belief-inheritance model, or does it need a
separate evidence layer? Verifies the Patterns.txt <-> Trans.csv join, degeneracy,
hub/component structure, and one CYCLE vs SCATTER-GATHER node overlap.

NEVER loads the 470MB CSV into memory — streams it once, line by line.
Run from repo root:  python scripts/probe_aml.py
Expects the gitignored data at data/raw/HI-Small_{Trans.csv,Patterns.txt}.
"""
import csv
import io
import statistics
from collections import defaultdict, Counter

RAW = "data/raw"
PATTERNS = f"{RAW}/HI-Small_Patterns.txt"
TRANS = f"{RAW}/HI-Small_Trans.csv"

# Column layout (0-indexed). NOTE: the CSV header names BOTH account columns "Account"
# (duplicate header) -> a DictReader/pandas would collapse them; parse positionally.
# 0 ts, 1 fromBank, 2 fromAcct, 3 toBank, 4 toAcct, 5 amtRecv, 6 recvCur,
# 7 amtPaid, 8 payCur, 9 fmt, 10 isLaundering
def parse(line):
    row = next(csv.reader(io.StringIO(line)))
    return {
        "from_node": (row[1], row[2]), "to_node": (row[3], row[4]),
        "key": tuple(row[0:10]),  # exact-tuple join key (everything but the label)
        "n_rows": row,
    }


def load_blocks():
    blocks, cur = [], None
    with open(PATTERNS, encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if line.startswith("BEGIN LAUNDERING ATTEMPT"):
                typ = line.split(" - ", 1)[1].split(":")[0].strip()
                cur = {"typology": typ, "rows": [], "nodes": set()}
            elif line.startswith("END LAUNDERING ATTEMPT"):
                blocks.append(cur); cur = None
            elif line.strip() and cur is not None:
                p = parse(line)
                cur["rows"].append(p)
                cur["nodes"].add(p["from_node"]); cur["nodes"].add(p["to_node"])
    return blocks


def main():
    blocks = load_blocks()
    by_typ = Counter(b["typology"] for b in blocks)
    total_rows = sum(len(b["rows"]) for b in blocks)
    print(f"[blocks] total={len(blocks)}  by_typology={dict(sorted(by_typ.items()))}")
    print(f"[blocks] labeled data rows={total_rows}")

    degen = [b for b in blocks if len(b["rows"]) == 1]
    print(f"[degenerate] single-txn blocks={len(degen)} "
          f"({100*len(degen)/len(blocks):.1f}%) "
          f"by_typology={dict(sorted(Counter(b['typology'] for b in degen).items()))}")
    for typ in sorted(by_typ):
        s = sorted(len(b["rows"]) for b in blocks if b["typology"] == typ)
        print(f"[hops] {typ:16s} n={len(s):3d} min={s[0]:2d} "
              f"median={int(statistics.median(s)):2d} max={s[-1]:2d}")

    # hub reuse + block-sharing connected components (how "bounded" a subgraph is)
    node_to_blocks = defaultdict(set)
    for i, b in enumerate(blocks):
        for nd in b["nodes"]:
            node_to_blocks[nd].add(i)
    parent = list(range(len(blocks)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x

    for _, bs in node_to_blocks.items():
        bs = list(bs)
        for j in range(1, len(bs)):
            ra, rb = find(bs[0]), find(bs[j])
            if ra != rb:
                parent[ra] = rb
    comps = defaultdict(list)
    for i in range(len(blocks)):
        comps[find(i)].append(i)
    sizes = sorted((len(v) for v in comps.values()), reverse=True)
    isolated = sum(1 for s in sizes if s == 1)
    print(f"[structure] distinct nodes={len(node_to_blocks)} "
          f"shared-across->1-block={sum(1 for _, bs in node_to_blocks.items() if len(bs) > 1)}")
    print(f"[structure] components={len(comps)} sizes_top10={sizes[:10]} "
          f"fully_isolated_blocks={isolated} ({100*isolated/len(blocks):.0f}%)")
    giant = max(comps.values(), key=len)
    gnodes = set()
    for i in giant:
        gnodes |= blocks[i]["nodes"]
    print(f"[structure] largest component={len(giant)} blocks but only "
          f"{len(gnodes)} distinct nodes (a ping-pong account-pair artifact if tiny)")

    # THE JOIN: stream the CSV once. Every labeled pattern key should hit exactly one row.
    pk = Counter(r["key"] for b in blocks for r in b["rows"])
    print(f"[join] pattern keys={len(pk)} dup-within-patterns={sum(1 for c in pk.values() if c > 1)}")
    keys = set(pk)
    hits, n_csv, laund = Counter(), 0, 0
    fmts, curs = Counter(), Counter()
    with open(TRANS, encoding="utf-8", newline="") as f:
        rdr = csv.reader(f)
        next(rdr)  # header
        for row in rdr:
            n_csv += 1
            if row[10] == "1":
                laund += 1; fmts[row[9]] += 1; curs[row[6]] += 1
            k = tuple(row[0:10])
            if k in keys:
                hits[k] += 1
    matched = sum(1 for k in keys if hits[k] >= 1)
    collisions = sum(1 for k in keys if hits[k] > 1)
    print(f"[csv] rows={n_csv} laundering=1 rows={laund} "
          f"(labeled-by-typology={len(keys)}; UNLABELED laundering={laund-len(keys)})")
    print(f"[csv] laundering formats={dict(fmts)} top_currencies={curs.most_common(5)}")
    print(f"[JOIN] matched={matched}/{len(keys)} ({100*matched/len(keys):.2f}%) "
          f"zero_match={len(keys)-matched} collisions={collisions}")

    # one non-degenerate CYCLE vs SCATTER-GATHER node overlap
    def first(typ, minrows):
        return next((b for b in blocks if b["typology"] == typ and len(b["rows"]) >= minrows), None)
    cyc, sg = first("CYCLE", 4), first("SCATTER-GATHER", 6)
    if cyc and sg:
        print(f"[overlap] CYCLE({len(cyc['rows'])} rows/{len(cyc['nodes'])} nodes) vs "
              f"SCATTER-GATHER({len(sg['rows'])} rows/{len(sg['nodes'])} nodes): "
              f"shared_nodes={sorted(cyc['nodes'] & sg['nodes'])}")


if __name__ == "__main__":
    main()
