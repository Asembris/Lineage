"""What a CONCLUSIVE_NO decision actually IS — and it is not one thing.

READ-ONLY. Writes nothing, calls no model, costs nothing. Deterministic: same numbers every run.

    PYTHONIOENCODING=utf-8 PYTHONPATH=. .venv/Scripts/python.exe scripts/probe_conclusive_no.py

WHY THIS SCRIPT EXISTS
----------------------
The seam's frozen census is `MATCH 57 / CONCLUSIVE_NO 463 / INCONCLUSIVE 980`, and the gloss that
travelled with it — in the decider's docstring, in `DecisionOut`'s docstring (and therefore in
`/openapi.json`), in README, in DEMO's Bridge beat, in the honesty ledger — was:

    CONCLUSIVE_NO  463   "searched; there is no cycle"   <- FALSE for 447 of them: they are
                                                            SELF-LOOPS, and no search ever ran

**That gloss is true of 16 of them.** Measured here:

    self-loops                    447   an account paying itself. NOT a transfer between two
                                        accounts, so it is excluded from adjacency by construction
                                        and NO SEARCH EVER RAN. detail: "self-loop is not a
                                        transfer cycle"
    real transfers, search closed  16   the cycle search genuinely ran and closed inside the
                                        extract. detail: "no return path; search closed inside the
                                        extract"

So the gloss invited a reader to picture a region that was explored and closed, for 447 rows where
nothing was explored at all. The outcome COUNT was never wrong; its description of itself was.

THE BASIS IS FOUR-WAY, NOT THREE-WAY — and `detail` already carries it on the wire.
`GET /aml/transactions/{id}/interrogate` serves the `detail` string, so no schema change and no
new field is needed to tell the four apart. What is NOT four-way is the PERSISTED basis tag
(migration 0008 pins exactly three), because self-loop-vs-closed-search is a property of the
EVIDENCE — re-derived from the graph — and not of what the agent RECORDED. Those are different
objects, and the decision surface deliberately serves only the second. See NOTES.md → G5 and
"THE CONCLUSIVE_NO GLOSS".

THIS IS THE THIRD CORRUPTION ADJACENT TO THE 65.3%. The phantom "728 / 48.5%" misstated its value.
The phantom `scripts/verify_seam.py` invented its provenance. This one misdescribed its own
complement. That number attracts imprecision, and only executable things have ever protected it —
hence this script, and
`tests/test_decision_read_surface.py::test_the_conclusive_no_decomposition_is_447_selfloops_and_16_closed_searches`,
which asserts the decomposition rather than trusting this file's output.
"""

import asyncio
import sys
from collections import Counter

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import text

from app.db import engine
from app.services.aml_evidence import neighbourhood
from app.services.aml_graph import Outcome, load_graph
from app.services.aml_seam import decide

# The oracle. Read ONLY here, ONLY to score the decomposition AFTER it has been computed from the
# unlabeled graph — never fed to the decider, which cannot see it by type. (scripts/ is not on
# tests/test_oracle_boundary.py's DECIDING_PATH, for exactly this reason: a scoring script is not
# a deciding path.)
_LABELS_SQL = text("SELECT id, is_laundering AS ground_truth FROM aml_transactions")


async def main() -> None:
    async with engine.connect() as conn:
        graph = await load_graph(conn)  # projects no label, by construction
        labels = {r[0]: r[1] for r in (await conn.execute(_LABELS_SQL)).all()}

    edges = sorted(graph.by_id.values(), key=lambda e: str(e.id))
    print(f"extract: {len(edges)} edges, {len(graph.accounts)} accounts\n")

    # PHASE 1 — decide every edge from the unlabeled graph. The label query above has already run,
    # but `decide()` takes only (Graph, Edge) and neither carries a label, so it cannot have
    # influenced anything. Same argument as the backfill's two-phase order, enforced by type.
    census = Counter()
    conclusive: dict[str, list] = {"self-loop": [], "real transfer, search closed": []}
    details = Counter()

    for e in edges:
        d = decide(graph, e)
        census[d.witness_outcome] += 1
        if d.witness_outcome is Outcome.CONCLUSIVE_NO:
            kind = "self-loop" if e.is_self_loop else "real transfer, search closed"
            conclusive[kind].append(e)
            details[kind] += 1

    print("=== the frozen census (label-free) ===")
    for o in Outcome:
        n = census[o]
        print(f"  {o.value:15s} {n:5d}   ({n / len(edges) * 100:.1f}%)")

    print("\n=== CONCLUSIVE_NO IS TWO THINGS ===")
    total_cn = census[Outcome.CONCLUSIVE_NO]
    for kind, es in conclusive.items():
        n = len(es)
        print(f"  {kind:30s} {n:4d}   ({n / total_cn * 100:.1f}% of CONCLUSIVE_NO)")

    closed = conclusive["real transfer, search closed"]
    if closed:
        sizes = sorted(len(neighbourhood(graph, e)) for e in closed)
        print(
            f"\n  the {len(closed)} genuine closed searches explored "
            f"{sizes[0]}..{sizes[-1]} real edges around the subject "
            f"(median {sizes[len(sizes) // 2]})"
        )

    print("\n=== the `detail` string served by /interrogate (this is what makes it four-way) ===")
    seen = Counter()
    for e in edges:
        from app.services.aml_graph import check

        chk = check(graph, e, "CYCLE")
        seen[(chk.outcome.value, chk.detail)] += 1
    for (outcome, detail), n in sorted(seen.items(), key=lambda x: -x[1]):
        print(f"  {n:5d}x  {outcome:14s} {detail!r}")

    # PHASE 2 — only NOW is the label used, and only to score.
    print("\n=== [oracle, read after the fact — to SCORE, never to decide] ===")
    for kind, es in conclusive.items():
        fraud = sum(1 for e in es if labels.get(e.id))
        print(f"  {kind:30s} laundering: {fraud:3d} / {len(es)}")
    print(
        "\n  reconciles with the frozen census's 5 laundering CONCLUSIVE_NO. The gloss "
        "'searched; there is no cycle' is true of the 16 real transfers, and false of the "
        "447 self-loops — i.e. of 96.5% of the 463 it was written about."
    )


if __name__ == "__main__":
    asyncio.run(main())
