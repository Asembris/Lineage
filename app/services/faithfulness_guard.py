"""The LIVE explanation-faithfulness guard (Roadmap Item E).

Item 8 built the OFFLINE half of this: a batch eval proving a corrected GEval rubric catches
additive hallucinations — claims a rationale ASSERTS that the retrieved evidence never supports
("funds returned within 24 hours through a shell company") — which DeepEval's built-in
Faithfulness metric is structurally blind to. This module turns that instrument into a live
check: given the model's narrated explanation and the exact evidence it was shown, it decides
whether the prose is SUPPORTED, and if not, the prose is withheld and a faithful deterministic
reconstruction is shown in its place.

TWO DESIGN DECISIONS, stated because getting either wrong would be worst:

1. THE VERDICT IS NEVER TOUCHED — only the RATIONALE. `verdict_guard.evaluate_claim` decides
   FLAG / NO_FLAG / INSUFFICIENT_COVERAGE from DETERMINISTIC structural evidence and never reads
   the rationale prose (Item 4's invariant: "FLAG is unreachable without a witness"). An
   unfaithful rationale means the EXPLANATION of the verdict is untrustworthy, which is a
   different fact from the verdict being wrong: the witness is real whether or not the model
   narrated it faithfully. Downgrading a structurally-proven FLAG because a probabilistic prose
   judge distrusted the prose would let an LLM judge override a deterministic proof — inverting
   the whole reason the brake exists. So this guard consumes a `VerdictOutcome` and returns a
   faithfulness result ALONGSIDE it; it MUST NOT mutate it. (tests/test_faithfulness_guard.py
   asserts field-level equality of verdict/reason/witness_txn_ids/corpus_doc across the guard.)
   Note this is orthogonal to `verdict_guard`'s own `unfaithful_citation` path: that is a
   deterministic STRUCTURAL check (the cited edges don't form the structure) and legitimately
   moves the verdict; this guard governs PROSE entailment only and never moves it.

2. FAIL CLOSED. If the judge is unreachable (Ollama down, NVIDIA credits gone, timeout, a parse
   failure yielding no score), the rationale is WITHHELD and marked UNAVAILABLE — never shown
   unguarded. Every "can't determine" in this project resolves to the safe side (Item 5's
   INSUFFICIENT_COVERAGE-on-uncertainty; Item 6's "a missing counterparty never reads as a
   pass"; the brake's "uncertainty never resolves to fraud"). What makes fail-closed cheap here:
   the deterministic `VerdictOutcome` and the deterministic reconstruction are ALWAYS available
   regardless of judge state, so a withheld rationale still leaves a fully-usable finding — the
   supervisor loses only the LLM's prose gloss, degraded to the deterministic truth.

JUDGE: gemma only (Ollama), NOT gemma+nemotron. Item 8 measured gemma as decisively the better
single judge (8/10 labeled vs nemotron 4/10) and found nemotron's value is ONLY as an
independent cross-check for CALIBRATION — plus nemotron carries a systematic anti-CYCLE bias
(category mean 0.27) that would false-flag faithful cycle rationales live. A live guard emits ONE
decision, so it uses the better single judge; nemotron stays the offline cross-check instrument
in scripts/eval_grounding.py. The rubric, threshold, grounding and judge-inputs are the SHARED
Item-8 instrument (app/services/faithfulness.py), so the live guard scores exactly what the eval
validated — including its two disclosed misses (fabricated-hop 0.50; faithful SG anchor 0.40).

INSTRUMENT LIMITS TRAVEL WITH EVERY RESULT: SUPPORTED means "passed the faithfulness check", NOT
"verified faithful" — the judge has a nonzero, documented false-negative rate on dense structural
prose. This is a probabilistic guard on top of the deterministic verdict, never a proof.

NOT WIRED INTO THE SERVER THIS SESSION (deferred): DeepEval's LocalModel builds its own OpenAI-
compatible client internally, so it cannot be handed a scoped `http_client` the way
openai_client.py / aws_client.py configure the clients THEY build. scripts/eval_grounding.py
works around this machine's AVG-antivirus TLS interception with a PROCESS-GLOBAL
truststore.inject_into_ssl(), which is safe only because that process is a short-lived script.
A guard running inside long-lived uvicorn cannot use a global TLS patch; it needs a scoped
solution first. So Item E ships as a callable + a manual demo (scripts/demo_faithfulness_guard.py
does the global inject, exactly like the eval), and any HTTP route is a separate, later decision —
the same way evaluate_transaction() shipped as a callable, never a paid GET.

THREAT TAXONOMY (verified against the primary OWASP PDF; cite correctly or not at all):
  * PRIMARY  — OWASP Top 10 for LLM Applications 2025, LLM09:2025 Misinformation (which absorbed
    the former "Overreliance"): "the model states false things with confidence, and other systems
    [here, a human supervisor] act on them." Additive citation-spoofing caught before display is
    exactly this.
  * SECONDARY (weaker fit) — LLM05:2025 Improper Output Handling: the supervisor is the downstream
    consumer of model output, and this guard is the validation gate on it.
  * EXPLICITLY NOT CLAIMED — retrieval/memory poisoning (LLM08:2025 Vector and Embedding
    Weaknesses; OWASP ASI06:2026 Memory & Context Poisoning, Item A's citation). This guard
    compares prose against the RETRIEVED rows; if those rows were themselves poisoned it would
    PASS a claim faithful to the poison. It defends against the model fabricating BEYOND its
    evidence, not against the evidence being poisoned — a different control. Do not upgrade this
    to a poisoning defense in any doc without actually building one.

See NOTES.md "Roadmap Item 8" (the offline eval this operationalizes) and "Roadmap Item E".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from app.services.aml_graph import Edge, Graph
from app.services.faithfulness import (
    FAITHFULNESS_THRESHOLD,
    GEVAL_STEPS,
    build_actual_output,
    deterministic_rationale,
)
from app.services.verdict_guard import VerdictOutcome


class FaithfulnessStatus(str, Enum):
    """Three states, mirroring the house style (Item 4/5/6 three-way verdicts).

    A caller decides display on this enum ALONE, never by parsing a reason string.
    """

    SUPPORTED = "SUPPORTED"      # the prose passed the check — show the model's rationale.
    UNSUPPORTED = "UNSUPPORTED"  # the judge scored it below threshold — withhold, show the fallback.
    UNAVAILABLE = "UNAVAILABLE"  # the judge could not decide — FAIL CLOSED, withhold, show the fallback.


@dataclass(frozen=True)
class JudgeVerdict:
    """A judge's raw output. `score` is None when the judge produced no parseable score."""

    score: float | None
    reason: str | None = None


class FaithfulnessJudge(Protocol):
    """The one thing the guard needs from a judge. Production wraps a gemma DeepEval GEval metric;
    tests pass a stub. May RAISE (transport/timeout) — the guard treats that as UNAVAILABLE."""

    def score_faithfulness(
        self, *, input: str, actual_output: str, retrieval_context: list[str]
    ) -> JudgeVerdict: ...


@dataclass(frozen=True)
class FaithfulnessResult:
    """The guard's output. Rides ALONGSIDE the VerdictOutcome and never replaces it."""

    status: FaithfulnessStatus
    score: float | None
    threshold: float
    judge_reason: str | None
    # What a UI should render as the explanation: the model's prose iff SUPPORTED, else the
    # faithful deterministic reconstruction. NEVER the withheld prose when not SUPPORTED.
    display_rationale: str
    # The original model prose, always retained for audit/logging — but not shown when withheld.
    model_rationale: str
    validation_notes: list[str] = field(default_factory=list)

    @property
    def shown(self) -> bool:
        """True iff the model's own rationale is what gets displayed."""
        return self.status is FaithfulnessStatus.SUPPORTED

    @property
    def withheld(self) -> bool:
        return not self.shown


def check_rationale(
    *,
    outcome: VerdictOutcome,
    rationale: str,
    grounding: list[str],
    input_text: str,
    subject: Edge,
    graph: Graph,
    judge: FaithfulnessJudge,
    threshold: float = FAITHFULNESS_THRESHOLD,
) -> FaithfulnessResult:
    """Judge one model rationale against the evidence it was shown. Verdict-preserving, fail-closed.

    `outcome` is READ, never mutated. `rationale` is the model's prose; `grounding`/`input_text`
    are the shared-instrument judge inputs (build_grounding / build_input); `subject`/`graph` are
    used only to render the faithful deterministic fallback (build on Item 5, asserts row contents
    only).
    """
    fallback = deterministic_rationale(graph, subject, outcome)
    actual_output = build_actual_output(outcome.claimed_typology, rationale)

    # A rationale with no prose asserts nothing, so there is nothing to be unfaithful. Show the
    # deterministic reconstruction (the model declined to narrate) and never bother the judge.
    if not rationale.strip():
        return FaithfulnessResult(
            FaithfulnessStatus.SUPPORTED, None, threshold, None,
            display_rationale=fallback, model_rationale=rationale,
            validation_notes=["empty rationale asserts nothing; deterministic reconstruction shown"],
        )

    try:
        verdict = judge.score_faithfulness(
            input=input_text, actual_output=actual_output, retrieval_context=grounding
        )
    except Exception as e:  # transport / timeout / any judge failure -> FAIL CLOSED
        return FaithfulnessResult(
            FaithfulnessStatus.UNAVAILABLE, None, threshold,
            f"judge unavailable: {type(e).__name__}: {str(e)[:200]}",
            display_rationale=fallback, model_rationale=rationale,
        )

    if verdict.score is None:  # judge returned but produced no parseable score -> FAIL CLOSED
        return FaithfulnessResult(
            FaithfulnessStatus.UNAVAILABLE, None, threshold,
            verdict.reason or "judge returned no score",
            display_rationale=fallback, model_rationale=rationale,
        )

    if verdict.score >= threshold:
        return FaithfulnessResult(
            FaithfulnessStatus.SUPPORTED, verdict.score, threshold, verdict.reason,
            display_rationale=rationale, model_rationale=rationale,
        )
    return FaithfulnessResult(
        FaithfulnessStatus.UNSUPPORTED, verdict.score, threshold, verdict.reason,
        display_rationale=fallback, model_rationale=rationale,
    )


class GEvalFaithfulnessJudge:
    """Production judge: a gemma (or any OpenAI-compatible) DeepEval GEval metric over the SHARED
    Item-8 rubric. DeepEval is imported LAZILY so importing this module needs no judge stack (the
    hermetic tests never touch this class — they pass a stub).

    `model` is an already-built DeepEval judge (e.g. deepeval.models.LocalModel for gemma). TLS to
    the judge endpoint is the caller's concern — see the module docstring's server-deferral note;
    the demo does a process-global truststore inject before constructing the LocalModel.
    """

    def __init__(self, model: object) -> None:
        self._model = model

    def score_faithfulness(
        self, *, input: str, actual_output: str, retrieval_context: list[str]
    ) -> JudgeVerdict:
        from deepeval.metrics import GEval
        from deepeval.test_case import LLMTestCase, LLMTestCaseParams

        metric = GEval(
            name="Grounded Faithfulness",
            evaluation_steps=GEVAL_STEPS,
            evaluation_params=[
                LLMTestCaseParams.INPUT,
                LLMTestCaseParams.ACTUAL_OUTPUT,
                LLMTestCaseParams.RETRIEVAL_CONTEXT,
            ],
            model=self._model,
            threshold=FAITHFULNESS_THRESHOLD,
            async_mode=False,
        )
        metric.measure(
            LLMTestCase(input=input, actual_output=actual_output, retrieval_context=retrieval_context)
        )
        return JudgeVerdict(metric.score, metric.reason)
