"""Shared instrument for explanation-faithfulness — the ONE rubric, ONE grounding renderer, and
ONE deterministic fallback that both the offline eval (Item 8) and the live guard (Item E) use.

Why this module exists at all: Item 8 built an offline eval that proved a corrected GEval rubric
catches additive hallucinations (claims asserted-but-not-in-data) that DeepEval's built-in
Faithfulness metric is structurally blind to. Item E turns that same instrument into a LIVE
guard. If the eval and the guard each carried their OWN copy of the rubric text and their OWN
copy of the evidence renderer, the two would silently diverge the first time one was edited, and
the guard would stop measuring what the eval validated. That is the exact failure mode Item 6
called out when it forced the certificate canonicalizer to be SHARED rather than reimplemented on
each side ("two independently-implemented canonicalizers that happen to agree today make the
check a false guarantee waiting to silently diverge"). So the rubric, the grounding rendering,
and the judge-input formatting live here, once, and both callers import them.

Everything in this module is PURE: no DeepEval, no judge, no OpenAI, no DB. The live judge adapter
lives in app/services/faithfulness_guard.py; the offline judge wiring lives in
scripts/eval_grounding.py. This module only knows how to render evidence and how the rubric reads.

See NOTES.md "Roadmap Item 8" (the offline eval + the calibration that produced this rubric) and
"Roadmap Item E" (the live guard built on top).
"""

from __future__ import annotations

import uuid

from app.services.aml_agent import _frag, neighbourhood, structure_text
from app.services.aml_graph import Edge, Graph
from app.services.aml_interrogate import TraversalKind, Witness, interrogate
from app.services.verdict_guard import VerdictOutcome

# ---------------------------------------------------------------------------------------
# THE RUBRIC — moved verbatim from scripts/eval_grounding.py (Item 8). Do not paraphrase.
# ---------------------------------------------------------------------------------------
# A GEval faithfulness rubric that — unlike the built-in FaithfulnessMetric — penalizes claims
# that are merely UNSUPPORTED, not only claims that directly contradict. Calibration showed the
# built-in metric scores injected-but-non-contradicting hallucinations (a 24h window, a fabricated
# account, an external advisory) as 1.00 faithful, because "no info" is not a contradiction. The
# prose-entailment gap this targets is exactly those unsupported additions, so the rubric makes
# "every claim must be explicitly supported by the context" the bar.
#
# IN-SAMPLE PROVENANCE (Item 8, kept with the rubric wherever it travels): these steps were
# ITERATED on the 8-tuple calibration set AFTER watching the built-in metric fail, not
# pre-registered. On the full run the rubric caught 7/8 authored hallucinations and 5/5 FRESH
# (never-iterated) ones, so it generalizes — but two disclosed misses stand and travel with every
# number produced by this instrument: the fabricated-hop negative at 0.50 (exactly at threshold)
# and a verified-faithful SCATTER-GATHER anchor at 0.40 (a false negative on dense-id SG prose).
GEVAL_STEPS = [
    "Identify every factual claim in the actual output: accounts, transaction ids, amounts, the "
    "structure/typology, flow direction, timing, entities, counts, and any external references.",
    "For each claim, check whether it is explicitly stated in, or directly entailed by, the "
    "retrieval context (the structural facts, the neighbourhood edge list, the typology "
    "definitions).",
    "Penalize any claim that is not supported by the context — including claims that merely ADD "
    "information the context never provides (e.g. timing like 'within 24 hours', entity form like "
    "'shell company', a specific total, an account id absent from the edge list, an external "
    "advisory reference, or a claimed recurrence). Absence of support is a failure, not only "
    "direct contradiction.",
    "A fully faithful output makes only claims the context supports. Score down in proportion to "
    "how many claims are unsupported or contradicted, weighting outright contradictions most.",
]

# The GEval decision threshold, shared so the eval and the guard agree on where "supported" ends.
# 0.5 is Item 8's calibrated boundary; at it the fabricated-hop miss sits (0.50 reads as faithful).
FAITHFULNESS_THRESHOLD = 0.5


# ---------------------------------------------------------------------------------------
# JUDGE INPUTS — the three strings a judge sees. Moved from scripts/build_grounding_goldenset.py
# so the LIVE guard feeds the judge byte-identically to how the cached golden set was built.
# ---------------------------------------------------------------------------------------


def build_grounding(g: Graph, subject: Edge, summary: str, defs: list[tuple[str, str]]) -> list[str]:
    """The evidence the rationale must follow from, rendered EXACTLY as the agent saw it.

    CRITICAL (Item 8's grounding-representation bug): accounts use the SAME 6-char `_frag` the
    agent's prompt and rationale use. An earlier version rendered 8-char account prefixes here;
    the judge then read the rationale's `41ce7e` and the context's `41ce7e96` as a contradiction
    and scored every faithful rationale 0.00. The grounding must be the agent's OWN evidence
    representation, not a re-rendering — else the eval/guard measures a truncation mismatch, not
    prose faithfulness.
    """
    edge_facts = [
        f"transaction {x.id}: account {_frag(x.src)} sends {x.amount} ({x.payment_format}) "
        f"to account {_frag(x.dst)}"
        for x in neighbourhood(g, subject)
    ]
    definitions = [f"{typ}: {body}" for typ, body in defs]
    return [f"Structural facts: {summary}"] + edge_facts + definitions


def build_input(subject: Edge, summary: str) -> str:
    """The DeepEval `input` field: the question posed to the agent, for the judge's context."""
    return (
        f"Given these neutral structural facts about transaction {subject.id}'s neighbourhood "
        f"in a money-flow graph, which laundering typology (if any) does the structure match, "
        f"and why?\n\n{summary}"
    )


def build_actual_output(typology: str, rationale: str) -> str:
    """The DeepEval `actual_output` field: the model's claim as prose to be scored for faithfulness."""
    return f"Claimed typology: {typology}.\n{rationale}" if rationale else f"Claimed typology: {typology}."


# ---------------------------------------------------------------------------------------
# DETERMINISTIC FALLBACK — what to SHOW when the model's prose is withheld (Item E addition #2).
#
# Reuses Item 5's traversal derivation (interrogate / ring_order / scatter_gather_legs, via the
# `Witness` it returns) rather than a bare "[rationale withheld]" marker. This is a stronger,
# more useful degrade AND it inherits Item 5's core discipline verbatim: a client can render the
# witness as text by formatting column values, "which asserts nothing beyond what the rows
# literally contain and needs no faithfulness check". So the fallback is, by construction, the
# one narration that can never itself be unfaithful.
# ---------------------------------------------------------------------------------------


def render_witness_text(g: Graph, subject: Edge, witness: Witness) -> str:
    """A faithful-by-construction rendering of a re-derived witness. Names only what the rows hold:
    6-char account frags, amounts, and payment formats — never timing, entity form, or intent."""
    if witness.kind is TraversalKind.RING:
        edges = [g.by_id[i] for i in witness.transaction_ids if i in g.by_id]
        hops = "; ".join(
            f"{_frag(e.src)} sends {e.amount} ({e.payment_format}) to {_frag(e.dst)}" for e in edges
        )
        return (
            f"Deterministic reconstruction (CYCLE): {hops}. "
            f"The funds return to account {_frag(subject.src)} after {len(edges)} transfers."
        )
    if witness.kind is TraversalKind.LEGS and witness.legs is not None:
        scatter = [g.by_id[i] for i in witness.legs.get("scatter", []) if i in g.by_id]
        gather = [g.by_id[i] for i in witness.legs.get("gather", []) if i in g.by_id]
        scatter_txt = "; ".join(
            f"{_frag(e.src)} sends {e.amount} ({e.payment_format}) to {_frag(e.dst)}" for e in scatter
        )
        gather_txt = "; ".join(
            f"{_frag(e.src)} sends {e.amount} ({e.payment_format}) to {_frag(e.dst)}" for e in gather
        )
        return (
            f"Deterministic reconstruction (SCATTER-GATHER): one source scatters — {scatter_txt} — "
            f"and those intermediaries gather into one destination — {gather_txt}."
        )
    # BUNDLE / NONE: no single traversal to narrate. Name the cited rows and nothing more.
    ids = [g.by_id[i] for i in witness.transaction_ids if i in g.by_id]
    if ids:
        rows = "; ".join(
            f"{_frag(e.src)} sends {e.amount} ({e.payment_format}) to {_frag(e.dst)}" for e in ids
        )
        return f"Deterministic reconstruction ({witness.typology}): the cited transactions are {rows}."
    return ""


def deterministic_rationale(g: Graph, subject: Edge, outcome: VerdictOutcome) -> str:
    """The display text to show in place of a withheld model rationale.

    Picks the witness for the verdict's OWN claimed typology and renders it faithfully. If there is
    no such matching witness (a NO_FLAG / INSUFFICIENT_COVERAGE verdict where the model narrated a
    structure the graph denies — exactly the naturally-occurring confabulations Item 8 found on
    NO_WITNESS edges), it states the deterministic verdict and asserts NOTHING structural, because
    asserting structure that isn't there is the very failure being guarded against.
    """
    witnesses, _ = interrogate(g, subject)
    w = next((x for x in witnesses if x.typology == outcome.claimed_typology), None)
    if w is not None and w.matched:
        rendered = render_witness_text(g, subject, w)
        if rendered:
            return rendered
    return (
        "The model's explanation was withheld as unverifiable against the retrieved evidence. "
        f"The deterministic verdict stands: {outcome.verdict.value} "
        f"({outcome.reason}); no structural witness is asserted."
    )
