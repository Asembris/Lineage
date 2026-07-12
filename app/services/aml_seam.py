# READS NO LABEL COLUMN, and CANNOT. Its entire input is `aml_graph.Graph`, which is built by
# `load_graph()` from a SELECT that projects no label and whose `Edge` dataclass has no label
# field. The verdict is therefore a pure function of the unlabeled money-flow graph — not as a
# convention, but by type. tests/test_oracle_boundary.py pins both halves of that claim.
"""The grounding seam's DECIDER (G4): a label-free verdict for one AML transaction.

This is the module that decides what the living azure-7 does with each real IBM money-flow edge,
acting on the laundering belief its ancestor azure-0 formed and it never met. It is deliberately
tiny, deliberately pure, and deliberately separate from the backfill that calls it.

WHY IT IS ITS OWN MODULE. `seed/backfill_aml_decisions.py` must read
`aml_transactions.is_laundering` — that is the whole point, it writes the ground truth into
`decisions.is_fraud`. If the decision logic lived in that same module, "the decider cannot see a
label" would be a claim about discipline, enforceable only by a reviewer's attention. Split out
here, it is a claim about TYPES: `decide()` takes a `Graph` and an `Edge`, neither of which has
anywhere to put a label. The backfill computes EVERY verdict through this module first, and only
then opens the label query. This mirrors `seed/backfill_decisions.py`, where `_decision_from`
computes the verdict from `txn.on_pattern` (an observable feature) before the row is stamped with
`is_fraud=txn.is_fraud` (ground truth the rule never saw).

THE VERDICT MAPPING, AND ITS ONE HONEST COMPROMISE
--------------------------------------------------
The frozen `cycle_witness` returns one of three outcomes, and the payment vocabulary
(`approve|decline|blocked`) has only two useful slots. The mapping:

    MATCH          -> blocked   the witness re-derived a real directed cycle. Corroborated.
    CONCLUSIVE_NO  -> approve   the search closed WITHOUT touching the edge of the extract.
                                The absence of a cycle is a fact about the world.
    INCONCLUSIVE   -> approve   the search ran into a SINK. "No cycle" and "the cycle leaves our
                                extract" are indistinguishable, so the honest answer is
                                "cannot determine" — and we let the payment through.

**THAT THIRD LINE IS A DISCLOSED MODELING CHOICE AND IT IS NOT A CORNER CASE.** Measured over all
1,500 ingested edges, and asserted in
`tests/test_decision_read_surface.py::test_the_witness_census_over_the_real_extract_is_57_463_980`
(which runs THIS decider over the real extract and then reads the oracle to score it):

(An earlier version of this docstring cited `scripts/verify_seam.py` and `tests/test_grounding_seam.py`.
NEITHER contained these numbers and the first does not exist — so until G5 the project's single most
important caveat was defended by a docstring pointing at a missing file. Found by going to look.)

    MATCH           57  (3.8%)    43 laundering / 14 benign
    CONCLUSIVE_NO  463  (30.9%)    5 laundering / 458 benign
    INCONCLUSIVE   980  (65.3%)  252 laundering / 728 benign   <-- approved

So `INCONCLUSIVE -> approve` is **65.3% of the extract**, and it silently approves **252 of the
300 laundering rows**. Letting a payment through absent evidence is what a real system does, but
the price is most of the fraud. This proportion must travel with every quote of the seam's
decisions — it is the single most important caveat about this belief, not a footnote.

(Do NOT restate this as "728 / 48.5%". 728 is the BENIGN-ONLY inconclusive subset. That
understated figure has now been introduced into this project twice and corrected twice; it is
carried here, in the decider itself, precisely so it stops being easy to get wrong.)

BECAUSE THE THIRD LINE EXISTS, THE VERDICT ALONE DOES NOT SAY WHY. So `SeamDecision` carries the
witness outcome, and the backfill persists it in `decisions.txn_ref` — making the coverage split
queryable straight from the data, with no schema change and no re-derivation. See the backfill.

THE BASIS TAG IS STRUCTURAL, NOT A CONVENTION (migration 0008)
---------------------------------------------------------------
`txn_ref_for()` below is the SOLE writer of that tag and `TXN_REF_TAGS` is the whole legal
vocabulary. Migration 0008's `ck_decisions_kind` pins EXACTLY these three strings in the database,
so an AML decision **cannot be written without a valid basis tag** — a future backfill that wrote
`txn_ref = str(txn_id)` would be REJECTED by CockroachDB rather than silently destroying the only
in-data carrier of the 65.3% disclosure. That is not hypothetical caution: the understated "728 /
48.5%" figure has been introduced into this project twice and corrected twice. Prose corrections
demonstrably do not stick, so the number's carrier is defended by the schema instead.
(tests/test_decision_read_surface.py asserts the migration's three literals ARE these three.)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.services.aml_graph import Check, Edge, Graph, Outcome, cycle_witness

# The one typology this belief is about. CYCLE is FLAG_CAPABLE (measured-sound: zero
# cross-typology false witness, on the development set AND on the never-tuned hold-out —
# NOTES Items 4/7), which is why the belief may be stated as a decision rule at all.
TYPOLOGY = "CYCLE"

# outcome -> verdict. The full reasoning, and the 65.3% price of the third line, is in the module
# docstring. Kept as data so the mapping is inspectable rather than buried in a branch.
VERDICT_FOR: dict[Outcome, str] = {
    Outcome.MATCH: "blocked",
    Outcome.CONCLUSIVE_NO: "approve",
    Outcome.INCONCLUSIVE: "approve",
}

# --- The basis tag: `decisions.txn_ref` for an AML decision --------------------------------
# Lives HERE, in the decider that defines the outcome vocabulary, so the backfill (which writes
# it), the read surface (which projects it), and migration 0008 (which enforces it) cannot drift
# apart. See the module docstring.

TXN_REF_PREFIX = "aml"


def txn_ref_for(outcome: Outcome) -> str:
    """`aml:MATCH` | `aml:CONCLUSIVE_NO` | `aml:INCONCLUSIVE` — the decision's BASIS, in the data."""
    return f"{TXN_REF_PREFIX}:{outcome.value}"


# The complete legal vocabulary. Migration 0008's CHECK contains exactly these, and a test pins it.
TXN_REF_TAGS: tuple[str, ...] = tuple(txn_ref_for(o) for o in Outcome)


def witness_outcome_of(txn_ref: str) -> str | None:
    """The witness outcome recorded in a decision's `txn_ref`. None for a CARD decision.

    A PROJECTION of persisted data, never a recomputation — so the field this backs can never
    drift from the row it describes (unlike re-running the witness, which would answer a subtly
    different question: what the graph says NOW, not what the agent recorded THEN).

    Total for AML rows by construction: migration 0008 makes any other tag unwritable.
    """
    prefix = f"{TXN_REF_PREFIX}:"
    if not txn_ref.startswith(prefix):
        return None
    tag = txn_ref[len(prefix):]
    return tag if tag in {o.value for o in Outcome} else None


@dataclass(frozen=True)
class SeamDecision:
    """One label-free decision about one real AML transaction.

    `witness_outcome` is the BASIS, and it is not decoration: with two outcomes mapping to
    `approve`, the verdict alone cannot distinguish "we searched and there is no cycle" from
    "we could not tell". Persisting it is what keeps the 65.3% honest in the data itself.
    """

    txn_id: uuid.UUID
    verdict: str
    witness_outcome: Outcome
    witness_txn_ids: list[uuid.UUID]
    boundary_account: uuid.UUID | None


def decide(g: Graph, e: Edge) -> SeamDecision:
    """Apply the laundering belief to ONE transaction. Pure; sees no label, by type."""
    chk: Check = cycle_witness(g, e)
    return SeamDecision(
        txn_id=e.id,
        verdict=VERDICT_FOR[chk.outcome],
        witness_outcome=chk.outcome,
        witness_txn_ids=list(chk.witness_txn_ids),
        boundary_account=chk.boundary_account,
    )


def decide_all(g: Graph) -> list[SeamDecision]:
    """Apply the belief to every edge in the extract, in a deterministic order.

    Sorted by `str(txn_id)` — the same total order `aml_graph`'s witnesses use — so the backfill
    is reproducible row-for-row across runs and machines.
    """
    return [decide(g, e) for e in sorted(g.by_id.values(), key=lambda x: str(x.id))]


def census(decisions: list[SeamDecision]) -> dict[Outcome, int]:
    """Outcome -> count. The label-free half of the disclosure (see the module docstring)."""
    out = {o: 0 for o in Outcome}
    for d in decisions:
        out[d.witness_outcome] += 1
    return out
