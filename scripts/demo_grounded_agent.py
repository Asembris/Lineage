"""scripts/demo_grounded_agent.py — the grounded AML agent, end to end, with a REAL OpenAI call.

Picks one real transaction per brake outcome, runs the true pipeline (neutral structural
summary -> embed -> CockroachDB cosine retrieval -> gpt-4o-mini strict-schema claim -> brake),
and prints what the model claimed alongside what the graph actually supports.

Ground truth (is_laundering / aml_pattern_members) is fetched ONLY to print an "oracle" column
at the end, so you can see when the model and the brake were right or wrong. It never enters
the prompt and never enters the decision.

Read-only: writes nothing, so it cannot disturb the console's backfill.

Run:  PYTHONIOENCODING=utf-8 PYTHONPATH=. .venv/Scripts/python.exe scripts/demo_grounded_agent.py
"""

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import text  # noqa: E402

from app.db import engine  # noqa: E402
from app.services import aml_graph  # noqa: E402
from app.services.aml_agent import claim_for_transaction  # noqa: E402
from app.services.aml_graph import Outcome, load_graph  # noqa: E402
from app.services.verdict_guard import evaluate_claim  # noqa: E402


async def _labels() -> dict:
    async with engine.connect() as c:
        rows = (await c.execute(text("""
            SELECT m.transaction_id, i.typology FROM aml_pattern_members m
            JOIN aml_pattern_instances i ON i.id = m.pattern_instance_id"""))).all()
    return {r[0]: r[1] for r in rows}


def _pick(g, pred):
    return next(e for e in sorted(g.by_id.values(), key=lambda x: str(x.id)) if pred(e))


async def main() -> None:
    g = await load_graph()
    labels = await _labels()

    # Of the real CYCLE members, take one on the SHORTEST cycle (6 edges, not 10). The model has
    # to trace the path out of the neighbourhood listing itself, and a shorter path is a fairer
    # test of the grounding than a longer one — the brake behaves identically either way.
    cycles = [(len(aml_graph.cycle_witness(g, e).witness_txn_ids), str(e.id), e)
              for e in g.by_id.values() if labels.get(e.id) == "CYCLE"]
    shortest_cycle_edge = min(cycles)[2]

    subjects = [
        ("a real CYCLE member on the shortest ingested cycle (structure is there)",
         shortest_cycle_edge),
        ("a benign transfer, search closes inside the extract",
         _pick(g, lambda e: e.id not in labels and not e.is_self_loop
               and aml_graph.cycle_witness(g, e).outcome is Outcome.CONCLUSIVE_NO)),
        ("a STACK member — no sound witness in this extract",
         _pick(g, lambda e: labels.get(e.id) == "STACK")),
        ("an edge whose search runs into a sink (extract boundary)",
         _pick(g, lambda e: aml_graph.cycle_witness(g, e).outcome is Outcome.INCONCLUSIVE)),
    ]

    for note, subject in subjects:
        print("=" * 96)
        print(f"SUBJECT {subject.id}  ({note})")
        _, _, retrieved, claim, summary = await claim_for_transaction(subject.id)
        out = evaluate_claim(subject=subject, graph=g, retrieved=retrieved, claim=claim)

        print(f"  structural summary : {summary[:150]}...")
        print("  retrieved (k=3)    : " + ", ".join(
            f"{r['typology']}@{r['distance']:.3f}" for r in retrieved)
            + f"   [top-2 margin {retrieved[1]['distance'] - retrieved[0]['distance']:.4f} "
              "— provenance only, gates nothing]")
        print(f"  MODEL claimed      : {claim.typology}  citing {len(claim.evidence_txn_ids)} txn(s)")
        print(f"    rationale        : {claim.rationale[:120]}")
        print(f"  BRAKE verdict      : {out.verdict.value}   ({out.reason})")
        if out.witness_txn_ids:
            print(f"    evidence path    : {[str(t)[:8] for t in out.witness_txn_ids]}")
        if out.boundary_account:
            print(f"    knowledge stops at account {str(out.boundary_account)[:8]} (a sink)")
        if out.validation_errors:
            print(f"    validator        : {out.validation_errors[0]}")
        oracle = labels.get(subject.id, "BENIGN (unlabeled)")
        print(f"  ORACLE (never seen by model or brake): {oracle}")

    print("=" * 96)
    print("Note: the brake's outcome depends on what the model CLAIMS, so a given subject does not")
    print("map to a fixed verdict here — e.g. a subject whose CYCLE search hits the extract boundary")
    print("yields NO_FLAG, not INSUFFICIENT, if the model claims SCATTER-GATHER instead. Each branch")
    print("is exercised deterministically, on these same rows, in tests/test_aml_brake.py.")

    await engine.dispose()


asyncio.run(main())
