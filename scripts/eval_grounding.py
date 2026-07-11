"""Item 8 — the RAG-grounding eval. Scores cached agent rationales for prose faithfulness.

WHAT THIS MEASURES, AND WHY IT IS NOT REDUNDANT WITH verdict_guard.py: the brake validates that
a rationale's CITATIONS resolve to real rows that really form the claimed structure. It is blind
to whether the PROSE wrapped around those citations is accurate — "funds returned within 24 hours
through a shell company" cites nothing an edge supports. This eval judges exactly that prose
entailment (the deferred half of Item E). It reads the cached golden set (eval/grounding/) and
never calls the AML pipeline or OpenAI; the JUDGE is NVIDIA NIM (nemotron) or Ollama (gemma),
never OpenAI. See NOTES.md "Roadmap Item 8".

TLS NOTE — why a process-global inject here, unlike the app's scoped SSL contexts: DeepEval's
LocalModel builds its own openai.OpenAI client internally, which we cannot hand a scoped
`http_client=httpx.Client(verify=ctx)` the way app/services/openai_client.py and aws_client.py do
for the clients THEY construct. This machine's AVG antivirus MITMs outbound TLS with a root that
certifi doesn't carry (but the Windows store does), so we verify against the OS store via
truststore.inject_into_ssl(). That patch is process-global by design, but the process is THIS
short-lived eval script alone — it cannot leak into the FastAPI server or the Lambda, which run
as separate invocations and keep their own scoped fixes. Same posture as the app (real
verification against the OS-trusted chain, never verify=False), applied globally only because the
client isn't ours to configure.

Judge is a PARAMETER, never hardcoded:
  --judge nvidia   -> nvidia/nemotron-3-super-120b-a12b @ integrate.api.nvidia.com (thinking off)
  --judge ollama   -> gemma4:31b-cloud @ localhost:11434 (OpenAI-compatible endpoint)

Run (full generation-metric scoring, NVIDIA judge):
  PYTHONIOENCODING=utf-8 PYTHONPATH=. .venv/Scripts/python.exe scripts/eval_grounding.py --judge nvidia
Run (calibration subset on both judges):
  ... scripts/eval_grounding.py --judge nvidia --calibration
  ... scripts/eval_grounding.py --judge ollama --calibration
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

import truststore  # noqa: E402  -- MUST run before any TLS client is built (see module docstring)

truststore.inject_into_ssl()

os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")
os.environ.setdefault("DEEPEVAL_DISABLE_PROGRESS_BAR", "YES")

if sys.platform == "win32":
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from deepeval.metrics import (  # noqa: E402
    AnswerRelevancyMetric,
    FaithfulnessMetric,
    GEval,
    HallucinationMetric,
)
from deepeval.models import LocalModel  # noqa: E402
from deepeval.test_case import LLMTestCase, LLMTestCaseParams  # noqa: E402

# THE RUBRIC IS SHARED, NOT LOCAL. Item E turns this offline eval into a live guard, and both must
# score against the identical rubric text and threshold — a divergent copy would make the live
# guard stop measuring what this eval validated (the Item-6 shared-canonicalizer discipline). So
# GEVAL_STEPS + FAITHFULNESS_THRESHOLD live in app.services.faithfulness and are imported here.
from app.services.faithfulness import FAITHFULNESS_THRESHOLD, GEVAL_STEPS  # noqa: E402

EVAL_DIR = pathlib.Path(__file__).resolve().parents[1] / "eval" / "grounding"
GOLDENSET_PATH = EVAL_DIR / "goldenset.json"
NEGATIVES_PATH = EVAL_DIR / "adversarial_negatives.json"

# A fixed calibration subset: faithful anchors + natural confabulations + hand-authored negatives,
# so we can see whether a judge separates faithful from unfaithful. `expect` is MY OWN read, the
# human baseline the judges are compared against. FAITHFUL = the prose follows from the grounding.
CALIBRATION: list[tuple[str, str, str]] = [
    ("185f748d-f99e-58f4-bcab-e1b7114a8d3a", "FAITHFUL", "verified cycle-of-6; prose matches the cited ring"),
    ("0bc572e6-694a-53e7-ae62-6e3731d3d5f5", "FAITHFUL", "verified 7-intermediary scatter-gather; prose matches"),
    ("008d48c6-e003-5f93-93a5-6e96bcbfe4c3", "UNFAITHFUL", "NO_WITNESS edge; model confabulated a scatter-gather"),
    ("00ba6e26-aa25-57ed-9016-26e6576cf47d", "UNFAITHFUL", "NO_WITNESS edge; claimed structure the graph denies"),
    ("185f748d-f99e-58f4-bcab-e1b7114a8d3a::adv-timing", "UNFAITHFUL", "injected 24h window; no timestamps in grounding"),
    ("185f748d-f99e-58f4-bcab-e1b7114a8d3a::adv-fabricated-hop", "UNFAITHFUL", "injected account not in evidence"),
    ("0bc572e6-694a-53e7-ae62-6e3731d3d5f5::adv-reversed-direction", "UNFAITHFUL", "reversed the stated flow direction"),
    ("045adfd2-a822-566f-9cd2-6a17fc150539", "BORDERLINE", "unfaithful_citation, but prose describes a generic cycle"),
]

JUDGES = {
    "nvidia": dict(
        model="nvidia/nemotron-3-super-120b-a12b",
        base_url="https://integrate.api.nvidia.com/v1",
        api_key_env="NVIDIA_API_KEY",
        # Nemotron is a reasoning model; suppress thinking so the judge returns clean JSON and
        # burns ~3 not ~50+ completion tokens per call (measured in the Step-0 smoke test).
        # response_format json_object curbs its habit of wrapping answers in {analysis, final},
        # which breaks DeepEval's schema.model_validate.
        generation_kwargs={
            "extra_body": {"chat_template_kwargs": {"thinking": False}},
            "response_format": {"type": "json_object"},
        },
    ),
    "ollama": dict(
        model="gemma4:31b-cloud",
        base_url="http://localhost:11434/v1",
        api_key_env=None,  # Ollama ignores the key; cloud auth is handled by the local daemon
        generation_kwargs={},
    ),
}


def build_judge(name: str) -> LocalModel:
    spec = JUDGES[name]
    api_key = os.environ[spec["api_key_env"]] if spec["api_key_env"] else "ollama"
    return LocalModel(
        model=spec["model"],
        base_url=spec["base_url"],
        api_key=api_key,
        temperature=0,
        generation_kwargs=spec["generation_kwargs"],
    )


def load_tuples(include_adversarial: bool) -> list[dict]:
    rows = json.loads(GOLDENSET_PATH.read_text(encoding="utf-8"))
    if include_adversarial and NEGATIVES_PATH.exists():
        rows = rows + json.loads(NEGATIVES_PATH.read_text(encoding="utf-8"))
    return rows


def to_testcase(t: dict) -> LLMTestCase:
    return LLMTestCase(
        input=t["input"],
        actual_output=t["actual_output"],
        retrieval_context=t.get("retrieval_context"),
        context=t.get("context"),
    )


def _geval_faithfulness(judge: LocalModel) -> GEval:
    return GEval(
        name="Grounded Faithfulness",
        evaluation_steps=GEVAL_STEPS,
        evaluation_params=[
            LLMTestCaseParams.INPUT,
            LLMTestCaseParams.ACTUAL_OUTPUT,
            LLMTestCaseParams.RETRIEVAL_CONTEXT,
        ],
        model=judge,
        threshold=FAITHFULNESS_THRESHOLD,
        async_mode=False,
    )


def score_one(t: dict, judge: LocalModel, metrics: list[str]) -> dict:
    tc = to_testcase(t)
    out: dict = {"subject_id": t["subject_id"], "category": t.get("category"),
                 "label_faithful": t.get("label_faithful")}
    if "geval" in metrics:
        try:
            m = _geval_faithfulness(judge)
            m.measure(tc)
            out["geval"] = {"score": m.score, "reason": m.reason}
        except Exception as e:
            out["geval"] = {"score": None, "error": f"{type(e).__name__}: {str(e)[:160]}"}
    if "faithfulness" in metrics:
        try:
            m = FaithfulnessMetric(model=judge, threshold=0.5, include_reason=True, async_mode=False)
            m.measure(tc)
            out["faithfulness"] = {"score": m.score, "reason": m.reason}
        except Exception as e:  # judge emitted non-conforming JSON etc. — record, don't crash
            out["faithfulness"] = {"score": None, "error": f"{type(e).__name__}: {str(e)[:160]}"}
    if "hallucination" in metrics:
        try:
            m = HallucinationMetric(model=judge, threshold=0.5, include_reason=True, async_mode=False)
            m.measure(tc)
            out["hallucination"] = {"score": m.score, "reason": m.reason}
        except Exception as e:
            out["hallucination"] = {"score": None, "error": f"{type(e).__name__}: {str(e)[:160]}"}
    if "answer_relevancy" in metrics:
        try:
            m = AnswerRelevancyMetric(model=judge, threshold=0.5, include_reason=True, async_mode=False)
            m.measure(tc)
            out["answer_relevancy"] = {"score": m.score, "reason": m.reason}
        except Exception as e:
            out["answer_relevancy"] = {"score": None, "error": f"{type(e).__name__}: {str(e)[:160]}"}
    return out


def run_calibration(judge_name: str, metric: str = "faithfulness") -> None:
    judge = build_judge(judge_name)
    by_id = {t["subject_id"]: t for t in load_tuples(include_adversarial=True)}
    print(f"\n=== CALIBRATION — judge={judge_name} ({JUDGES[judge_name]['model']}), metric={metric} ===")
    print(f"{'subject':<38} {'my_read':<11} {'score':>6}  note")
    results = []
    for sid, expect, note in CALIBRATION:
        t = by_id.get(sid)
        if t is None:
            print(f"{sid[:36]:<38} MISSING")
            continue
        r = score_one(t, judge, [metric])
        fs = r[metric].get("score")
        results.append({"subject_id": sid, "my_read": expect, "score": fs,
                        "reason": r[metric].get("reason") or r[metric].get("error"), "note": note})
        disp = f"{fs:>6.2f}" if isinstance(fs, (int, float)) else f"{'ERR':>6}"
        print(f"{sid[:36]:<38} {expect:<11} {disp}  {note}")
    out_path = EVAL_DIR / f"calibration_{judge_name}_{metric}.json"
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"-> {out_path}")


def _mean(xs: list[float]) -> float | None:
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(sum(xs) / len(xs), 3) if xs else None


def _summarize(results: list[dict], metrics: list[str]) -> None:
    """Report json-parse failure counts and separation on the LABELED subset only.

    We have ground-truth labels for: the 8 authored negatives (label_faithful=False) and the two
    verified-FLAG faithful anchors. Everything else is unlabeled real prose (descriptive only). No
    aggregate is allowed to hide the known 0.60 fabricated-account miss, so labeled negatives are
    printed one per line, not averaged away.
    """
    faithful_anchors = {
        "185f748d-f99e-58f4-bcab-e1b7114a8d3a",
        "0bc572e6-694a-53e7-ae62-6e3731d3d5f5",
    }
    print("\n--- json-parse / call failures per metric (the real full-run count) ---")
    for metric in metrics:
        errs = [r for r in results if isinstance(r.get(metric), dict) and r[metric].get("score") is None]
        print(f"  {metric:16s} failures: {len(errs)}/{len(results)}"
              + (f"  e.g. {errs[0][metric].get('error')}" if errs else ""))

    print("\n--- LABELED negatives (authored, label_faithful=False) — per tuple, NOT averaged ---")
    for r in results:
        if r.get("label_faithful") is False:
            row = "  ".join(
                f"{m}={r[m]['score']:.2f}" if isinstance(r.get(m), dict) and isinstance(r[m].get("score"), (int, float))
                else f"{m}=ERR" for m in metrics
            )
            print(f"  {r['subject_id'][-28:]:<28} {row}")

    print("\n--- verified-FAITHFUL anchors (should score HIGH on geval/faithfulness) ---")
    for r in results:
        if r["subject_id"] in faithful_anchors:
            row = "  ".join(
                f"{m}={r[m]['score']:.2f}" if isinstance(r.get(m), dict) and isinstance(r[m].get("score"), (int, float))
                else f"{m}=ERR" for m in metrics
            )
            print(f"  {r['subject_id'][:12]:<12} {row}")

    print("\n--- mean score by category (descriptive; unlabeled real prose) ---")
    cats: dict[str, list[dict]] = {}
    for r in results:
        cats.setdefault(r.get("category") or "?", []).append(r)
    for cat, rs in sorted(cats.items()):
        cells = "  ".join(
            f"{m}~{_mean([x[m]['score'] for x in rs if isinstance(x.get(m), dict)])}" for m in metrics
        )
        print(f"  {cat:22s} n={len(rs):<3} {cells}")


def run_full(judge_name: str, metrics: list[str], include_adversarial: bool) -> None:
    judge = build_judge(judge_name)
    tuples = load_tuples(include_adversarial=include_adversarial)
    print(f"\n=== FULL SCORING — judge={judge_name}, metrics={metrics}, n={len(tuples)} ===")
    results = []
    for i, t in enumerate(tuples, 1):
        print(f"[{i}/{len(tuples)}] {t['subject_id'][:44]} ...", file=sys.stderr)
        results.append(score_one(t, judge, metrics))
    out_path = EVAL_DIR / f"results_{judge_name}.json"
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"-> {out_path}  ({len(results)} scored)")
    _summarize(results, metrics)


def main() -> None:
    ap = argparse.ArgumentParser(description="Item 8 RAG-grounding eval (judge parameterized).")
    ap.add_argument("--judge", choices=list(JUDGES), required=True)
    ap.add_argument("--calibration", action="store_true", help="run the fixed calibration subset")
    ap.add_argument("--metric", default="faithfulness",
                    help="calibration metric: faithfulness | geval | hallucination | answer_relevancy")
    ap.add_argument("--metrics", default="faithfulness,hallucination,answer_relevancy")
    ap.add_argument("--no-adversarial", action="store_true", help="exclude the hand-authored negatives")
    args = ap.parse_args()

    if args.calibration:
        run_calibration(args.judge, args.metric)
    else:
        run_full(args.judge, args.metrics.split(","), include_adversarial=not args.no_adversarial)


if __name__ == "__main__":
    main()
