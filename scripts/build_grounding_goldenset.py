"""Build the Item-8 RAG-grounding golden set: cached agent rationales to score for faithfulness.

Two subcommands, deliberately split so the FREE part is reviewable before any OpenAI spend:

  * `select`   — pure, deterministic, NO OpenAI. Loads the money-flow graph, classifies every
                 real edge by which typology witnesses it, and picks a fixed spread of subject
                 transactions across the brake's branches. Writes the plan to eval/grounding/
                 subjects.json. Re-run freely.
  * `generate` — reads that plan and, ONCE per subject, calls claim_for_transaction() (exactly
                 one gpt-4o-mini chat + one text-embedding-3-small embedding) then evaluate_claim()
                 (PURE — no second model call). Persists each tuple to eval/grounding/
                 goldenset.json so DeepEval can score it against the free NVIDIA/Ollama judge
                 repeatedly without ever re-hitting OpenAI.

The object being scored is the ONLY LLM-generated prose in the AML pipeline: Claim.rationale
(app/services/aml_agent.py). VerdictOutcome carries none of it — hence claim_for_transaction(),
not evaluate_transaction(). See NOTES.md "Roadmap Item 8".

This script writes NOTHING to the database and touches no aml_* / typology_corpus row. The graph
and corpus are read-only inputs; the golden set is a flat JSON artifact.

Run (select, free):
  PYTHONIOENCODING=utf-8 PYTHONPATH=. .venv/Scripts/python.exe scripts/build_grounding_goldenset.py select
Run (generate, spends the approved 32 chat + 32 embedding OpenAI calls, ONCE):
  PYTHONIOENCODING=utf-8 PYTHONPATH=. .venv/Scripts/python.exe scripts/build_grounding_goldenset.py generate
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
from dataclasses import asdict

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.services.aml_agent import claim_for_transaction, structure_text  # noqa: E402
from app.services.aml_graph import WITNESS, Edge, Graph, Outcome, check, load_graph  # noqa: E402
from app.services.corpus import TYPOLOGY_DOCS  # noqa: E402
# The grounding renderer + judge-input formatters are SHARED with the live guard (Item E), so the
# cached golden set is built with byte-identical evidence to what the guard feeds the live judge.
from app.services.faithfulness import (  # noqa: E402
    build_actual_output,
    build_grounding,
    build_input,
)
from app.services.verdict_guard import Claim, evaluate_claim  # noqa: E402

# Static typology -> definition body, so grounding can be rebuilt offline (no OpenAI) for the
# typologies a cached tuple recorded as retrieved. The bodies are query-independent constants.
_BODY = {d["typology"]: d["body"] for d in TYPOLOGY_DOCS}

OUT_DIR = pathlib.Path(__file__).resolve().parents[1] / "eval" / "grounding"
SUBJECTS_PATH = OUT_DIR / "subjects.json"
GOLDENSET_PATH = OUT_DIR / "goldenset.json"

# The spread. 32 real subjects, chosen to exercise every branch the brake can take AND to
# produce a range of prose — from rich structure-describing rationales (CYCLE/SG) to the
# terse "no structure here" negatives that a faithfulness judge must also get right.
#   CYCLE_WITNESS       -> model should claim CYCLE -> FLAG; the meatiest prose to fact-check.
#   SG_WITNESS          -> model should claim SCATTER-GATHER -> FLAG; two-leg prose.
#   GS_OR_STACK_WITNESS -> only a non-flag-capable typology witnesses -> INSUFFICIENT_COVERAGE
#                          or NO_FLAG; the model still narrates a gather/scatter it can't flag.
#   NO_WITNESS          -> nothing witnesses; the model should decline and cite nothing.
QUOTA = {
    "CYCLE_WITNESS": 12,
    "SG_WITNESS": 10,
    "GS_OR_STACK_WITNESS": 5,
    "NO_WITNESS": 5,
}


def _witness_profile(g: Graph, e: Edge) -> dict[str, str]:
    """Every typology's deterministic outcome on this edge — the ground context, no model."""
    return {t: check(g, e, t).outcome.value for t in sorted(WITNESS)}


def _category(profile: dict[str, str]) -> str | None:
    """Bucket an edge by its witness profile. Returns None if it fits no bucket we sample."""
    matched = {t for t, o in profile.items() if o == Outcome.MATCH.value}
    if "CYCLE" in matched:
        return "CYCLE_WITNESS"
    if "SCATTER-GATHER" in matched:
        return "SG_WITNESS"
    if matched:  # only GATHER-SCATTER and/or STACK match — never flag-capable
        return "GS_OR_STACK_WITNESS"
    return "NO_WITNESS"


def select_subjects(g: Graph) -> list[dict]:
    """Deterministically pick QUOTA subjects per category. Sorted by id, so the choice is stable."""
    buckets: dict[str, list[Edge]] = {k: [] for k in QUOTA}
    for e in sorted(g.by_id.values(), key=lambda x: str(x.id)):
        if e.is_self_loop:
            continue  # a self-loop is not a transfer; the agent is never asked about one here
        cat = _category(_witness_profile(g, e))
        if cat is not None and len(buckets[cat]) < QUOTA[cat]:
            buckets[cat].append(e)

    chosen: list[dict] = []
    for cat, want in QUOTA.items():
        got = buckets[cat]
        if len(got) < want:
            print(f"  WARNING: only {len(got)}/{want} available for {cat}", file=sys.stderr)
        for e in got:
            chosen.append(
                {
                    "subject_id": str(e.id),
                    "category": cat,
                    "witness_profile": _witness_profile(g, e),
                }
            )
    return chosen


async def cmd_select() -> None:
    g = await load_graph()
    chosen = select_subjects(g)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    SUBJECTS_PATH.write_text(json.dumps(chosen, indent=2), encoding="utf-8")

    counts: dict[str, int] = {}
    for c in chosen:
        counts[c["category"]] = counts.get(c["category"], 0) + 1
    print(f"selected {len(chosen)} subjects -> {SUBJECTS_PATH}")
    for cat in QUOTA:
        print(f"  {cat:22s} {counts.get(cat, 0):2d}/{QUOTA[cat]}")


def _tuple_from(subject: Edge, g: Graph, retrieved: list[dict], claim: Claim, summary: str,
                plan: dict) -> dict:
    """Assemble one DeepEval-ready tuple. evaluate_claim() is PURE — no OpenAI here."""
    outcome = evaluate_claim(subject=subject, graph=g, retrieved=retrieved, claim=claim)
    grounding = build_grounding(g, subject, summary, [(r["typology"], r["body"]) for r in retrieved])

    return {
        "subject_id": str(subject.id),
        "category": plan["category"],
        "witness_profile": plan["witness_profile"],
        "provenance": "gpt-4o-mini@real",  # vs a Claude-Code-authored adversarial negative
        # --- the DeepEval fields (built by the SHARED formatters, identical to the live guard) ---
        "input": build_input(subject, summary),
        "actual_output": build_actual_output(claim.typology, claim.rationale),
        "retrieval_context": grounding,
        "context": grounding,
        # --- provenance / cross-checks (not fed to the judge; for the human review + report) ---
        "claim": {
            "typology": claim.typology,
            "evidence_txn_ids": [str(i) for i in claim.evidence_txn_ids],
            "rationale": claim.rationale,
        },
        "verdict": {
            "verdict": outcome.verdict.value,
            "reason": outcome.reason,
            "claimed_typology": outcome.claimed_typology,
            "validation_errors": outcome.validation_errors,
        },
        "retrieved_typologies": [r["typology"] for r in retrieved],
        # Left null for the real tuples; the calibration pass and human review fill it in. The
        # adversarial negatives ship with this pre-set to False by construction.
        "label_faithful": None,
    }


async def cmd_generate() -> None:
    if not SUBJECTS_PATH.exists():
        sys.exit(f"no selection at {SUBJECTS_PATH}; run `select` first")
    plans = json.loads(SUBJECTS_PATH.read_text(encoding="utf-8"))
    import uuid

    tuples: list[dict] = []
    for i, plan in enumerate(plans, 1):
        sid = uuid.UUID(plan["subject_id"])
        print(f"[{i}/{len(plans)}] {plan['category']:22s} {sid} ...", file=sys.stderr)
        subject, g, retrieved, claim, summary = await claim_for_transaction(sid)
        tuples.append(_tuple_from(subject, g, retrieved, claim, summary, plan))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    GOLDENSET_PATH.write_text(json.dumps(tuples, indent=2), encoding="utf-8")
    print(f"wrote {len(tuples)} real tuples -> {GOLDENSET_PATH}")
    print(f"OpenAI calls made: {len(tuples)} chat + {len(tuples)} embeddings = {2 * len(tuples)}")


async def cmd_rebuild() -> None:
    """Rebuild ONLY the grounding/input fields of the cached tuples — FREE, no OpenAI.

    The cached claim/rationale/verdict are kept verbatim; the retrieval_context, context and
    input are re-derived from the (unchanged) graph and the cached retrieved_typologies, so a fix
    to the evidence rendering can be applied without re-spending the one-time generation.
    """
    if not GOLDENSET_PATH.exists():
        sys.exit(f"no golden set at {GOLDENSET_PATH}; run `generate` first")
    tuples = json.loads(GOLDENSET_PATH.read_text(encoding="utf-8"))
    g = await load_graph()
    import uuid

    for t in tuples:
        subject = g.by_id[uuid.UUID(t["subject_id"])]
        summary = structure_text(g, subject)
        defs = [(typ, _BODY[typ]) for typ in t["retrieved_typologies"]]
        t["retrieval_context"] = t["context"] = build_grounding(g, subject, summary, defs)
        t["input"] = build_input(subject, summary)
    GOLDENSET_PATH.write_text(json.dumps(tuples, indent=2), encoding="utf-8")
    print(f"rebuilt grounding for {len(tuples)} tuples (no OpenAI) -> {GOLDENSET_PATH}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the Item-8 RAG-grounding golden set.")
    ap.add_argument("cmd", choices=["select", "generate", "rebuild"])
    args = ap.parse_args()
    asyncio.run(
        {"select": cmd_select, "generate": cmd_generate, "rebuild": cmd_rebuild}[args.cmd]()
    )


if __name__ == "__main__":
    main()
