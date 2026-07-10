"""The STRICT-BRAKE: a three-way verdict that can never flag without a witness (Item 4).

This module is pure. It takes a claim the model made, the corpus rows that were actually
retrieved, and the money-flow graph, and it decides. It calls no model and no database, so
every outcome is re-derivable from its inputs.

The discipline this project has held since Phase 2 is "verify with real data, don't trust the
model's self-report", so the validation layer here is deterministic. A second LLM asked "are
you sure?" would not meet that bar and is not used. What deterministic checks genuinely catch:

  * a typology cited that retrieval never returned (a hallucinated citation),
  * a transaction id cited that does not exist,
  * a cited evidence path that does not actually form the claimed structure — re-derived from
    the rows themselves, NOT compared against our own search's answer,
  * a FLAG asserted where the graph yields no witness.

What they cannot catch is unfaithful PROSE — "funds returned within 24 hours through a shell
company" is a claim about timing and corporate form that no cited edge supports. That is the
genuinely different half of Item E (explanation-faithfulness), and it is where an LLM judge
would earn its place. This module is the citation-and-structure half of Item E, built now and
meant to be subsumed later, not a parallel design. Until then the response schema keeps the
rationale in evidence-referencing slots to shrink the surface for drift.

Deliberately NOT checked: whether a cited `aml_pattern_instances` id matches the claimed
typology. Verifying that means reading the ground-truth label. Instance ids are never put in
the prompt, so the model can only produce one by hallucination — the check would be vacuous or
failing, and putting them in the prompt would hand over the answer key. Evidence the agent
cites is unlabeled transaction rows, and the structure is recomputed. See NOTES.md Item 4.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum

from app.services.aml_graph import (
    FLAG_CAPABLE,
    WITNESS,
    Check,
    Edge,
    Graph,
    Outcome,
    check,
    verify_witness_path,
)

# Retrieval distance is REPORTED as provenance and gates NOTHING. Two measurements forced this,
# and together they are the strongest form of "only structure may authorize a flag":
#
#  1. Distance is not a coverage signal. A description of a FAN-IN funnel typology the corpus
#     does NOT contain retrieves GATHER-SCATTER at distance 0.391 — closer than EVERY in-corpus
#     query (0.463-0.505). A "distance < tau => confident" rule would have confidently grounded
#     a verdict on a typology the corpus cannot cover.
#  2. The margin is near-degenerate for real subjects. The agent's query is a NEUTRAL structural
#     summary (degrees, path lengths), not typology-flavoured prose, and against the live corpus
#     those queries separate the top-2 documents by only 0.0005-0.02. An early version of this
#     module gated on a 0.01 margin floor and rejected a real STACK subject at margin 0.0005 —
#     for a reason that has no bearing on whether the structure exists. Gating on it would make
#     the brake a wall rather than a brake.
#
# The genuine protections are: the typology must have been retrieved, it must be structurally
# decidable, the graph must witness it, and the model's cited path must independently verify.


class Verdict(str, Enum):
    FLAG = "FLAG"
    NO_FLAG = "NO_FLAG"
    INSUFFICIENT_COVERAGE = "INSUFFICIENT_COVERAGE"


@dataclass(frozen=True)
class Claim:
    """What the model asserted. Note there is no confidence field: a confidence number the
    brake would then override is theatre. The verdict is decided by evidence."""

    typology: str
    evidence_txn_ids: list[uuid.UUID]
    rationale: str = ""


@dataclass
class VerdictOutcome:
    verdict: Verdict
    reason: str
    claimed_typology: str
    corpus_doc: dict | None = None
    witness_txn_ids: list[uuid.UUID] = field(default_factory=list)
    boundary_account: uuid.UUID | None = None
    validation_errors: list[str] = field(default_factory=list)
    structural_check: Check | None = None
    # Provenance only — see the note on retrieval distance above. Never gates the verdict.
    retrieval_margin: float | None = None
    # Every OTHER typology that independently witnesses this subject (Item 5). Provenance on
    # every branch; see competing_witnesses() for why it must never gate.
    competing_typologies: list[str] = field(default_factory=list)

    @property
    def flagged(self) -> bool:
        return self.verdict is Verdict.FLAG


def _doc_for(retrieved: list[dict], typology: str) -> dict | None:
    return next((r for r in retrieved if r["typology"] == typology), None)


def competing_witnesses(g: Graph, subject: Edge, claimed: str) -> list[str]:
    """Every OTHER typology that independently witnesses this edge (Item 5).

    Widened from the original flag-capable-only, first-hit-wins helper, for two reasons the
    live extract makes concrete. Measured over all 1,500 edges:

      * 26 edges carry >= 2 competing witnesses, and NONE of them is a CYCLE/SCATTER-GATHER
        pair — the two flag-capable typologies never co-witness (asserted in
        tests/test_aml_interrogate.py). Restricting to FLAG_CAPABLE therefore hides every
        real conflict this extract actually contains.
      * The old helper ran only on the CONCLUSIVE_NO branch, so on the 42 SCATTER-GATHER
        witnesses (where CYCLE is INCONCLUSIVE, never CONCLUSIVE_NO) a live competing witness
        sat on the subject and was never mentioned.

    SURFACING IS NOT GATING. This result is attached to every outcome as provenance and is
    read by exactly one decision: the pre-existing CONCLUSIVE_NO downgrade below, which still
    requires a FLAG_CAPABLE contradictor. Letting a non-flag-capable witness withhold a FLAG
    would silently repeat the MARGIN_FLOOR mistake — evidence that gates nothing must not
    start gating.
    """
    return [t for t in sorted(WITNESS) if t != claimed and check(g, subject, t).matched]


def evaluate_claim(*, subject: Edge, graph: Graph, retrieved: list[dict], claim: Claim) -> VerdictOutcome:
    """Decide FLAG / NO_FLAG / INSUFFICIENT_COVERAGE. Never flags without a witness."""
    typology = claim.typology
    doc = _doc_for(retrieved, typology)
    margin = retrieved[1]["distance"] - retrieved[0]["distance"] if len(retrieved) >= 2 else None
    # Provenance for EVERY branch, including the two that never consult the graph to decide.
    # What the graph independently witnesses here is worth reporting even when the claim is
    # rejected on retrieval grounds alone.
    competing = competing_witnesses(graph, subject, typology)

    # Gate 0 — the cited typology must be one retrieval actually returned. Retrieve with k=3
    # against a 4-document corpus, otherwise the "retrieved set" is the whole corpus and this
    # gate cannot fail.
    if doc is None:
        return VerdictOutcome(
            Verdict.INSUFFICIENT_COVERAGE,
            "typology_not_retrieved",
            typology,
            validation_errors=[f"{typology!r} is not among the retrieved candidates"],
            retrieval_margin=margin,
            competing_typologies=competing,
        )

    # Gate 1a — some typologies are not structurally decidable in this extract. GATHER-SCATTER's
    # witness fires on 37/1200 benign rows (Item 1's benign noise is anchored to the labeled
    # accounts, manufacturing hub structure around exactly what a hub-detector examines) and
    # retrieval cannot separate it from SCATTER-GATHER. Every ingested STACK instance is
    # internally disjoint, so it has no single connected path to witness. Claims on those can
    # never reach FLAG here.
    if typology not in FLAG_CAPABLE:
        return VerdictOutcome(
            Verdict.INSUFFICIENT_COVERAGE,
            "typology_not_decidable",
            typology,
            corpus_doc=doc,
            validation_errors=[f"{typology} has no sound structural witness in this evidence layer"],
            retrieval_margin=margin,
            competing_typologies=competing,
        )

    # Gate 1b — the graph is the authority on whether the structure is there.
    result = check(graph, subject, typology)

    if result.outcome is Outcome.INCONCLUSIVE:
        return VerdictOutcome(
            Verdict.INSUFFICIENT_COVERAGE,
            "search_reached_extract_boundary",
            typology,
            corpus_doc=doc,
            boundary_account=result.boundary_account,
            structural_check=result,
            retrieval_margin=margin,
            # On the 42 SCATTER-GATHER witnesses CYCLE is INCONCLUSIVE, so a live competing
            # witness reaches this branch. It is now reported instead of silently dropped.
            competing_typologies=competing,
        )

    if result.outcome is Outcome.CONCLUSIVE_NO:
        # UNCHANGED semantics: only a FLAG_CAPABLE competitor turns "no corroboration" into an
        # actual contradiction. A GATHER-SCATTER/STACK witness is reported (above) but is not
        # sound enough in this extract to contradict anything.
        contradictors = [t for t in competing if t in FLAG_CAPABLE]
        reason = "contradicted_by_competing_structure" if contradictors else "no_corroborating_structure"
        errors = (
            [f"evidence supports {', '.join(contradictors)}, not {typology}"] if contradictors else []
        )
        return VerdictOutcome(
            Verdict.NO_FLAG, reason, typology, corpus_doc=doc,
            validation_errors=errors, structural_check=result, retrieval_margin=margin,
            competing_typologies=competing,
        )

    # A witness exists. Now the model's OWN cited path must independently hold up, or we do
    # not certify the flag: real evidence plus an unfaithful citation is still an unsupported
    # assertion, and the brake's rule is that uncertainty never resolves to "fraud".
    err = verify_witness_path(graph, typology, claim.evidence_txn_ids, subject)
    if err is not None:
        return VerdictOutcome(
            Verdict.INSUFFICIENT_COVERAGE,
            "unfaithful_citation",
            typology,
            corpus_doc=doc,
            witness_txn_ids=result.witness_txn_ids,
            validation_errors=[err],
            structural_check=result,
            retrieval_margin=margin,
            competing_typologies=competing,
        )

    # A witness exists and the citation holds. `competing_typologies` may be non-empty here (10
    # edges FLAG on CYCLE while STACK and/or GATHER-SCATTER also witness) and DOES NOT gate:
    # neither is flag-capable, so neither may withhold a corroborated flag. It is disclosed so a
    # reviewer sees the structure was not unambiguous.
    return VerdictOutcome(
        Verdict.FLAG,
        result.detail or "structure corroborated",
        typology,
        corpus_doc=doc,
        witness_txn_ids=claim.evidence_txn_ids,
        structural_check=result,
        retrieval_margin=margin,
        competing_typologies=competing,
    )
