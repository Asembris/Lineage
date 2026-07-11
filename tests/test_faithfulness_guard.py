"""Roadmap Item E — the live explanation-faithfulness guard.

PURER THAN THE BRAKE TESTS: this suite touches NO cluster, NO OpenAI, and NO live judge. The
money-flow graph is a hand-built in-memory 3-account cycle, the model claim is constructed
directly, and the judge is a stub returning a canned score or raising. Like tests/
test_certificate.py it is ZERO-I/O and cannot wipe the moat (it never calls run_seed).

What is locked here — the two decisions a wrong call would corrupt:

  1. THE VERDICT IS IDENTICAL BEFORE AND AFTER THE GUARD. Asserted by real field equality on
     verdict / reason / witness_txn_ids / corpus_doc, on every status — not by trusting that the
     guard "doesn't touch it". An unfaithful rationale withholds PROSE, never moves the verdict.
  2. FAIL CLOSED. A judge that raises, or returns no parseable score, yields UNAVAILABLE and the
     prose is withheld — never shown unguarded.

Plus: a withheld rationale degrades to the faithful deterministic reconstruction (which names
only row contents), and an empty rationale asserts nothing so it needs no judge.
"""

import uuid

from app.services.aml_graph import Edge, Graph
from app.services.faithfulness_guard import (
    FaithfulnessStatus,
    JudgeVerdict,
    check_rationale,
)
from app.services.verdict_guard import Claim, Verdict, evaluate_claim
from app.services.faithfulness import build_grounding, build_input

# --- a hand-built, fully deterministic 3-account cycle: A -> B -> C -> A -------------------
A = uuid.UUID(int=0xA)
B = uuid.UUID(int=0xB)
C = uuid.UUID(int=0xC)
E1 = uuid.UUID(int=0x1)  # A -> B  (the subject)
E2 = uuid.UUID(int=0x2)  # B -> C
E3 = uuid.UUID(int=0x3)  # C -> A

_EDGES = [
    Edge(id=E1, src=A, dst=B, ts=1, amount=1000, payment_format="ACH"),
    Edge(id=E2, src=B, dst=C, ts=2, amount=1000, payment_format="ACH"),
    Edge(id=E3, src=C, dst=A, ts=3, amount=1000, payment_format="ACH"),
]

# A synthetic retrieval result with CYCLE present (Gate 0) — no OpenAI, no corpus read.
_RETRIEVED = [
    {"typology": "CYCLE", "title": "Cycle", "body": "funds return to the originating account", "distance": 0.40},
    {"typology": "SCATTER-GATHER", "title": "SG", "body": "one source fans out then regathers", "distance": 0.46},
    {"typology": "STACK", "title": "Stack", "body": "two bipartite layers", "distance": 0.50},
]

# A model rationale carrying an ADDITIVE hallucination (timing + entity form) the rows never
# support — the exact failure Item E guards against.
_HALLUCINATED = (
    "The cited transactions form a cycle returning to the source within 24 hours through a shell "
    "company, confirming laundering."
)


class _StubJudge:
    """A judge that never calls anything: returns a fixed score, or raises. The whole point is
    that CI exercises the guard's decision layer without a live gemma/nemotron/OpenAI call."""

    def __init__(self, *, score=None, reason="stub reason", raises=None):
        self._score = score
        self._reason = reason
        self._raises = raises

    def score_faithfulness(self, *, input, actual_output, retrieval_context):
        if self._raises is not None:
            raise self._raises
        return JudgeVerdict(self._score, self._reason)


def _graph():
    return Graph(list(_EDGES))


def _flag_outcome():
    """Drive the REAL deterministic brake to a FLAG on the real cycle — the guard's input."""
    g = _graph()
    subject = g.by_id[E1]
    claim = Claim(typology="CYCLE", evidence_txn_ids=[E1, E2, E3], rationale=_HALLUCINATED)
    outcome = evaluate_claim(subject=subject, graph=g, retrieved=_RETRIEVED, claim=claim)
    assert outcome.verdict is Verdict.FLAG  # precondition: a real witnessed flag
    return g, subject, outcome


def _inputs(g, subject):
    summary = "a neutral structural summary"  # the judge inputs; content is irrelevant to the stub
    grounding = build_grounding(g, subject, summary, [(r["typology"], r["body"]) for r in _RETRIEVED])
    return build_input(subject, summary), grounding


def test_supported_shows_the_model_prose():
    g, subject, outcome = _flag_outcome()
    input_text, grounding = _inputs(g, subject)
    r = check_rationale(
        outcome=outcome, rationale=_HALLUCINATED, grounding=grounding, input_text=input_text,
        subject=subject, graph=g, judge=_StubJudge(score=0.9),
    )
    assert r.status is FaithfulnessStatus.SUPPORTED
    assert r.shown is True
    assert r.display_rationale == _HALLUCINATED
    assert r.score == 0.9


def test_unsupported_withholds_prose_and_shows_the_deterministic_reconstruction():
    g, subject, outcome = _flag_outcome()
    input_text, grounding = _inputs(g, subject)
    r = check_rationale(
        outcome=outcome, rationale=_HALLUCINATED, grounding=grounding, input_text=input_text,
        subject=subject, graph=g, judge=_StubJudge(score=0.2),
    )
    assert r.status is FaithfulnessStatus.UNSUPPORTED
    assert r.withheld is True
    # the model's hallucinated prose is NOT what gets displayed ...
    assert r.display_rationale != _HALLUCINATED
    assert "24 hours" not in r.display_rationale
    assert "shell company" not in r.display_rationale
    # ... but it IS retained for audit.
    assert r.model_rationale == _HALLUCINATED
    # the fallback is the faithful deterministic ring, naming only row contents.
    assert "Deterministic reconstruction (CYCLE)" in r.display_rationale
    for frag in (str(A)[:6], str(B)[:6], str(C)[:6]):
        assert frag in r.display_rationale
    assert "after 3 transfers" in r.display_rationale


def test_unavailable_fails_closed_when_the_judge_raises():
    g, subject, outcome = _flag_outcome()
    input_text, grounding = _inputs(g, subject)
    r = check_rationale(
        outcome=outcome, rationale=_HALLUCINATED, grounding=grounding, input_text=input_text,
        subject=subject, graph=g, judge=_StubJudge(raises=RuntimeError("ollama down")),
    )
    assert r.status is FaithfulnessStatus.UNAVAILABLE
    assert r.withheld is True
    assert r.score is None
    assert "judge unavailable" in (r.judge_reason or "")
    assert "RuntimeError" in (r.judge_reason or "")
    # fail-closed still leaves a usable finding: the deterministic reconstruction.
    assert "Deterministic reconstruction (CYCLE)" in r.display_rationale


def test_unavailable_fails_closed_when_the_judge_returns_no_score():
    g, subject, outcome = _flag_outcome()
    input_text, grounding = _inputs(g, subject)
    r = check_rationale(
        outcome=outcome, rationale=_HALLUCINATED, grounding=grounding, input_text=input_text,
        subject=subject, graph=g, judge=_StubJudge(score=None, reason="parse failure"),
    )
    assert r.status is FaithfulnessStatus.UNAVAILABLE
    assert r.withheld is True
    assert r.display_rationale != _HALLUCINATED


def test_empty_rationale_asserts_nothing_and_needs_no_judge():
    g, subject, outcome = _flag_outcome()
    input_text, grounding = _inputs(g, subject)

    class _NeverCalled:
        def score_faithfulness(self, **_):
            raise AssertionError("the judge must not be called on an empty rationale")

    r = check_rationale(
        outcome=outcome, rationale="", grounding=grounding, input_text=input_text,
        subject=subject, graph=g, judge=_NeverCalled(),
    )
    assert r.status is FaithfulnessStatus.SUPPORTED
    assert "Deterministic reconstruction (CYCLE)" in r.display_rationale


def test_the_verdict_is_field_identical_before_and_after_the_guard_on_every_status():
    """The load-bearing invariant: the guard is verdict-preserving. Real equality, every branch."""
    judges = {
        "supported": _StubJudge(score=0.9),
        "unsupported": _StubJudge(score=0.1),
        "unavailable_raise": _StubJudge(raises=TimeoutError("judge timed out")),
        "unavailable_none": _StubJudge(score=None),
    }
    for name, judge in judges.items():
        g, subject, outcome = _flag_outcome()
        before = (outcome.verdict, outcome.reason, list(outcome.witness_txn_ids), outcome.corpus_doc)

        check_rationale(
            outcome=outcome, rationale=_HALLUCINATED, grounding=_inputs(g, subject)[1],
            input_text=_inputs(g, subject)[0], subject=subject, graph=g, judge=judge,
        )

        after = (outcome.verdict, outcome.reason, list(outcome.witness_txn_ids), outcome.corpus_doc)
        assert before == after, f"guard mutated the verdict on the {name} branch"
        assert outcome.verdict is Verdict.FLAG, f"verdict changed on the {name} branch"
