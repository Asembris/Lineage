"""Item E — live demonstration of the explanation-faithfulness guard against the REAL gemma judge.

This is the ONLY place the live judge runs; the test suite never does (it uses a stub). It spends
NO OpenAI: the rationales are read from Item 8's cached golden set / adversarial negatives, and the
only live call is to the free gemma judge via Ollama. It reads the money-flow graph from the
cluster (like every demo) but writes NOTHING and never calls run_seed.

It shows three things end to end:
  1. SUPPORTED  — a verified-faithful CYCLE anchor: the judge passes it, the model's prose is shown.
  2. UNSUPPORTED — an authored additive hallucination (a '24 hours' / shell-company claim over the
     SAME real cycle): the judge scores it below threshold, the prose is WITHHELD, and the faithful
     deterministic reconstruction is shown instead.
  3. UNAVAILABLE — the same faithful anchor judged by a deliberately-unreachable endpoint: the guard
     FAILS CLOSED, withholds the prose, and still shows the deterministic reconstruction.

In all three the VERDICT is printed before and after and is identical — the guard governs prose,
never the verdict.

TLS: this machine's AVG antivirus MITMs outbound TLS with a root certifi doesn't carry, and
DeepEval's LocalModel builds its own client we cannot hand a scoped context — so we verify against
the OS trust store with a PROCESS-GLOBAL truststore.inject_into_ssl(), exactly as
scripts/eval_grounding.py does. That is safe here because this is a short-lived script; it is
precisely why the guard is NOT wired into the long-lived server this session (see faithfulness_guard
module docstring).

Run:
  PYTHONIOENCODING=utf-8 PYTHONPATH=. .venv/Scripts/python.exe scripts/demo_faithfulness_guard.py
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import uuid

import truststore  # noqa: E402 -- MUST run before any TLS client is built (see module docstring)

truststore.inject_into_ssl()

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from deepeval.models import LocalModel  # noqa: E402

from app.services.aml_graph import load_graph  # noqa: E402
from app.services.corpus import TYPOLOGY_DOCS  # noqa: E402
from app.services.faithfulness_guard import (  # noqa: E402
    GEvalFaithfulnessJudge,
    check_rationale,
)
from app.services.verdict_guard import Claim, evaluate_claim  # noqa: E402

EVAL_DIR = pathlib.Path(__file__).resolve().parents[1] / "eval" / "grounding"
_BODY = {d["typology"]: d["body"] for d in TYPOLOGY_DOCS}

# The two verified-faithful anchors Item 8 hand-confirmed (label FAITHFUL).
FAITHFUL_CYCLE_ANCHOR = "185f748d-f99e-58f4-bcab-e1b7114a8d3a"
# An authored additive-hallucination negative over that same anchor's real cycle (timing claim).
TIMING_NEGATIVE = "185f748d-f99e-58f4-bcab-e1b7114a8d3a::adv-timing"


def _retrieved(typologies: list[str]) -> list[dict]:
    """Reconstruct the retrieved-candidate dicts. Distances are placeholders: the brake uses them
    only as ungating provenance, so the verdict is unaffected (see verdict_guard)."""
    return [{"typology": t, "body": _BODY[t], "distance": 0.40 + 0.03 * i} for i, t in enumerate(typologies)]


def _outcome_from_golden(g, tuple_) -> tuple:
    """Re-derive the deterministic VerdictOutcome for a cached real tuple, no OpenAI."""
    subject = g.by_id[uuid.UUID(tuple_["subject_id"])]
    c = tuple_["claim"]
    claim = Claim(
        typology=c["typology"],
        evidence_txn_ids=[uuid.UUID(i) for i in c["evidence_txn_ids"]],
        rationale=c["rationale"],
    )
    outcome = evaluate_claim(
        subject=subject, graph=g, retrieved=_retrieved(tuple_["retrieved_typologies"]), claim=claim
    )
    return subject, claim, outcome


def _prose_from_actual_output(actual_output: str) -> str:
    """The adversarial negatives store the perturbed prose inside `actual_output` after the
    'Claimed typology: X.' line. Recover just the rationale."""
    parts = actual_output.split("\n", 1)
    return parts[1] if len(parts) > 1 else ""


def _report(title: str, outcome, result) -> None:
    print(f"\n=== {title} ===")
    print(f"  VERDICT (deterministic, unchanged by the guard): {outcome.verdict.value} ({outcome.reason})")
    print(f"  FAITHFULNESS: {result.status.value}"
          + (f"  score={result.score:.2f} (threshold {result.threshold})" if result.score is not None else ""))
    if result.judge_reason:
        print(f"  judge said: {result.judge_reason[:220]}")
    label = "SHOWN (model prose)" if result.shown else "WITHHELD -> deterministic reconstruction shown"
    print(f"  DISPLAY [{label}]:")
    print(f"    {result.display_rationale}")


async def main() -> None:
    goldenset = json.loads((EVAL_DIR / "goldenset.json").read_text(encoding="utf-8"))
    negatives = json.loads((EVAL_DIR / "adversarial_negatives.json").read_text(encoding="utf-8"))
    by_id = {t["subject_id"]: t for t in goldenset}
    neg_by_id = {n["subject_id"]: n for n in negatives}

    g = await load_graph()
    live = GEvalFaithfulnessJudge(
        LocalModel(
            model="gemma4:31b-cloud",
            base_url="http://localhost:11434/v1",
            api_key="ollama",
            temperature=0,
            generation_kwargs={},
        )
    )

    # 1) A verified-faithful CYCLE anchor -> should be SUPPORTED, prose shown.
    anchor = by_id[FAITHFUL_CYCLE_ANCHOR]
    subject, claim, outcome = _outcome_from_golden(g, anchor)
    r1 = check_rationale(
        outcome=outcome, rationale=claim.rationale, grounding=anchor["retrieval_context"],
        input_text=anchor["input"], subject=subject, graph=g, judge=live,
    )
    _report("1) VERIFIED-FAITHFUL CYCLE ANCHOR", outcome, r1)

    # 2) An additive hallucination over the SAME real cycle -> should be UNSUPPORTED, prose withheld.
    neg = neg_by_id[TIMING_NEGATIVE]
    base = by_id[neg["base_subject_id"]]
    subject2, _, outcome2 = _outcome_from_golden(g, base)
    prose = _prose_from_actual_output(neg["actual_output"])
    r2 = check_rationale(
        outcome=outcome2, rationale=prose, grounding=neg["retrieval_context"],
        input_text=neg["input"], subject=subject2, graph=g, judge=live,
    )
    _report("2) AUTHORED ADDITIVE HALLUCINATION (timing / shell-company)", outcome2, r2)

    # 3) Fail-closed: the SAME faithful anchor judged by an unreachable endpoint -> UNAVAILABLE.
    dead = GEvalFaithfulnessJudge(
        LocalModel(
            model="gemma4:31b-cloud",
            base_url="http://127.0.0.1:9/v1",  # nothing listens here; the judge call fails
            api_key="ollama",
            temperature=0,
            generation_kwargs={},
        )
    )
    r3 = check_rationale(
        outcome=outcome, rationale=claim.rationale, grounding=anchor["retrieval_context"],
        input_text=anchor["input"], subject=subject, graph=g, judge=dead,
    )
    _report("3) JUDGE UNREACHABLE -> FAIL CLOSED", outcome, r3)

    print("\nNote: SUPPORTED means 'passed the faithfulness check', NOT 'verified faithful' — the")
    print("judge has a documented false-negative rate on dense structural prose (Item 8's two")
    print("disclosed misses). The deterministic verdict and reconstruction are always available.")


if __name__ == "__main__":
    asyncio.run(main())
