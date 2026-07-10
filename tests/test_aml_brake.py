"""Roadmap Item 4 — the grounded AML agent's STRICT-BRAKE and its verdict validator.

Every outcome below is reached on rows ALREADY in the live cluster (Item 1's ingestion), not
on fixtures invented for the test. The three-way verdict is exercised on:

  FLAG                  — a real CYCLE member edge; the witness is the real 6/7/10-edge cycle.
  NO_FLAG (uncorroborated) — a real benign, non-self-loop edge whose search closes inside the extract.
  NO_FLAG (contradicted)   — SCATTER-GATHER claimed on an edge whose structure is a CYCLE.
  INSUFFICIENT (boundary)  — an edge whose search runs into a sink (220 of 648 accounts are sinks).
  INSUFFICIENT (citation)  — a typology retrieval never returned.
  INSUFFICIENT (undecidable) — STACK, which has no sound witness in this extract.
  INSUFFICIENT (unfaithful)  — real structure, but the model's cited path does not form it.

CI-SAFE / NON-DESTRUCTIVE: no OpenAI (claims are constructed directly; the retrieval query is a
document's OWN stored embedding, the same trick tests/test_corpus.py uses), and every query is a
read. Unlike the replay/consistency tests this never calls run_seed, so it cannot wipe the moat.

GROUND TRUTH IS THE ORACLE, NEVER AN INPUT. `aml_pattern_members` and `is_laundering` are read
here only to SCORE the label-free witnesses. app/services/aml_graph.py never selects them.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import text

from app.corpus_models import SOURCE
from app.db import engine
from app.services import aml_graph
from app.services.aml_agent import neighbourhood
from app.services.aml_graph import FLAG_CAPABLE, Outcome, load_graph
from app.services.corpus import retrieve_typology
from app.services.verdict_guard import Claim, Verdict, evaluate_claim

# Measured against the live extract (1,500 edges, 300 labeled / 1,200 benign). These are
# ASSERTED, not merely reported: `cross` is what makes a typology FLAG-capable, and `benign` is
# the number that actually matters for a real detector — a future change that quietly doubles
# the benign false-positive count must fail here rather than be discovered in Item 7's eval.
#
#                     own_hits/own_total  cross_hits/cross_total  benign_hits/benign_total
ORACLE = {
    "CYCLE":          ((43, 43), (0, 257), (14, 1200)),
    "SCATTER-GATHER": ((39, 96), (0, 204), (3, 1200)),
    "GATHER-SCATTER": ((64, 77), (6, 223), (37, 1200)),
    "STACK":          ((6, 84), (27, 216), (2, 1200)),
}


async def _oracle_labels() -> dict[uuid.UUID, str]:
    async with engine.connect() as c:
        rows = (
            await c.execute(
                text(
                    """
                    SELECT m.transaction_id, i.typology
                    FROM aml_pattern_members m
                    JOIN aml_pattern_instances i ON i.id = m.pattern_instance_id
                    """
                )
            )
        ).all()
    return {r[0]: r[1] for r in rows}


async def _retrieved_from_cycle_doc(k: int = 3) -> list[dict]:
    """Real retrieval, no OpenAI: query with the CYCLE document's own stored embedding."""
    async with engine.connect() as c:
        emb = (
            await c.execute(
                text("SELECT embedding FROM typology_corpus WHERE typology='CYCLE' AND source=:s"),
                {"s": SOURCE},
            )
        ).scalar()
    vec = [float(x) for x in str(emb).strip("[]").split(",")]
    return await retrieve_typology(vec, k=k, source=SOURCE)


def _first(edges, pred):
    """Deterministic pick: lowest transaction id satisfying `pred`."""
    return next(e for e in sorted(edges, key=lambda x: str(x.id)) if pred(e))


def test_witness_soundness_and_benign_false_positive_rates():
    """Two DIFFERENT properties, both locked.

    (a) SOUNDNESS: a typology's witness must never fire on an edge belonging to a different
        typology. This is what selects FLAG_CAPABLE — it is measured here, not hand-picked.
    (b) BENIGN FALSE POSITIVES: soundness says nothing about firing on benign data, which is
        the number a real detector lives or dies by. Asserted separately.

    Precision/recall implied by these counts (see NOTES.md Item 4):
        CYCLE           recall 43/43 = 100%   precision 43/57 = 75.4%
        SCATTER-GATHER  recall 39/96 = 40.6%  precision 39/42 = 92.9%
        GATHER-SCATTER  recall 64/77 = 83.1%  precision 64/107 = 59.8%
        STACK           recall  6/84 =  7.1%  precision  6/35 = 17.1%
    """

    async def _run():
        g = await load_graph()
        labels = await _oracle_labels()
        return g, labels

    g, labels = asyncio.run(_run())

    sound = set()
    for typology, (own_exp, cross_exp, benign_exp) in ORACLE.items():
        fn = aml_graph.WITNESS[typology]
        own = cross = benign = 0
        own_t = cross_t = benign_t = 0
        for e in g.by_id.values():
            fired = fn(g, e).outcome is Outcome.MATCH
            lab = labels.get(e.id)
            if lab is None:
                benign_t += 1
                benign += fired
            elif lab == typology:
                own_t += 1
                own += fired
            else:
                cross_t += 1
                cross += fired

        assert (own, own_t) == own_exp, f"{typology} recall moved: {(own, own_t)} != {own_exp}"
        assert (cross, cross_t) == cross_exp, f"{typology} cross-typology moved: {(cross, cross_t)} != {cross_exp}"
        assert (benign, benign_t) == benign_exp, (
            f"{typology} BENIGN false positives moved: {(benign, benign_t)} != {benign_exp}. "
            "This is the number that matters for a real detector — do not silently re-baseline it."
        )
        if cross == 0:
            sound.add(typology)

    # The allowlist IS the measurement. If a future ingestion changes graph density, this fails
    # and turning a typology's FLAG capability on becomes a deliberate decision.
    assert sound == set(FLAG_CAPABLE), f"FLAG_CAPABLE is stale: measured-sound = {sound}"


def test_neighbourhood_contains_every_flag_capable_witness():
    """The model can only cite edges the prompt shows it. If a witness edge falls outside the
    neighbourhood, the brake rejects a TRUE structure as an unfaithful citation — a silent false
    negative. That bug shipped once (a 2-hop neighbourhood vs a 10-edge cycle) and this is what
    would have caught it.

    NEIGHBOURHOOD_HOPS is derived (ceil(MAX_CYCLE_HOPS/2)), but NEIGHBOURHOOD_LIMIT is a prompt-
    size cap: today the largest neighbourhood hits it exactly (120 edges) and nothing is lost,
    because edges are ordered by distance from the subject. A denser graph could truncate a
    witness away. This test is the guard on that, not a proof it cannot happen.
    """

    async def _run():
        g = await load_graph()
        escaped = []
        for e in g.by_id.values():
            for typology in sorted(FLAG_CAPABLE):
                res = aml_graph.WITNESS[typology](g, e)
                if not res.matched:
                    continue
                citable = {x.id for x in neighbourhood(g, e)}
                missing = [t for t in res.witness_txn_ids if t not in citable]
                if missing:
                    escaped.append((str(e.id), typology, len(missing)))
        assert not escaped, (
            f"{len(escaped)} witness(es) are not fully citable from the prompt neighbourhood — "
            f"the brake would reject true structures as unfaithful citations: {escaped[:5]}"
        )

    asyncio.run(_run())


def test_flag_requires_a_real_witness_on_a_real_cycle():
    async def _run():
        g = await load_graph()
        labels = await _oracle_labels()
        retrieved = await _retrieved_from_cycle_doc()

        subject = _first(g.by_id.values(), lambda e: labels.get(e.id) == "CYCLE")
        witness = aml_graph.cycle_witness(g, subject)
        assert witness.matched

        out = evaluate_claim(
            subject=subject,
            graph=g,
            retrieved=retrieved,
            claim=Claim("CYCLE", witness.witness_txn_ids, "funds return to the source account"),
        )
        assert out.verdict is Verdict.FLAG
        assert out.corpus_doc["typology"] == "CYCLE"
        # The evidence path is real rows, and it closes.
        assert len(out.witness_txn_ids) >= 3
        assert all(tid in g.by_id for tid in out.witness_txn_ids)
        assert subject.id in out.witness_txn_ids

    asyncio.run(_run())


def test_no_flag_when_a_benign_edge_has_no_corroborating_structure():
    async def _run():
        g = await load_graph()
        labels = await _oracle_labels()
        retrieved = await _retrieved_from_cycle_doc()

        # Benign, a real transfer (not a self-loop), and the search CLOSED inside the extract —
        # so the absence of a cycle is a fact about the world, not about our slice.
        subject = _first(
            g.by_id.values(),
            lambda e: e.id not in labels
            and not e.is_self_loop
            and aml_graph.cycle_witness(g, e).outcome is Outcome.CONCLUSIVE_NO,
        )
        out = evaluate_claim(
            subject=subject, graph=g, retrieved=retrieved,
            claim=Claim("CYCLE", [subject.id], "looks like a loop to me"),
        )
        assert out.verdict is Verdict.NO_FLAG
        assert out.reason == "no_corroborating_structure"

    asyncio.run(_run())


def test_no_flag_downgrade_when_evidence_supports_a_different_typology():
    """The 'contradicted' branch: SCATTER-GATHER is claimed, but the edge's real structure is a
    CYCLE. 40 such edges exist. The brake downgrades rather than flagging the wrong story."""

    async def _run():
        g = await load_graph()
        retrieved = await _retrieved_from_cycle_doc()
        assert any(r["typology"] == "SCATTER-GATHER" for r in retrieved)

        subject = _first(
            g.by_id.values(),
            lambda e: aml_graph.scatter_gather_witness(g, e).outcome is Outcome.CONCLUSIVE_NO
            and aml_graph.cycle_witness(g, e).matched,
        )
        out = evaluate_claim(
            subject=subject, graph=g, retrieved=retrieved,
            claim=Claim("SCATTER-GATHER", [subject.id], "one source, many intermediaries"),
        )
        assert out.verdict is Verdict.NO_FLAG
        assert out.reason == "contradicted_by_competing_structure"
        assert "CYCLE" in out.validation_errors[0]

    asyncio.run(_run())


def test_insufficient_coverage_when_the_search_reaches_the_extract_boundary():
    async def _run():
        g = await load_graph()
        retrieved = await _retrieved_from_cycle_doc()

        subject = _first(
            g.by_id.values(),
            lambda e: aml_graph.cycle_witness(g, e).outcome is Outcome.INCONCLUSIVE,
        )
        out = evaluate_claim(
            subject=subject, graph=g, retrieved=retrieved,
            claim=Claim("CYCLE", [subject.id], "probably a cycle"),
        )
        assert out.verdict is Verdict.INSUFFICIENT_COVERAGE
        assert out.reason == "search_reached_extract_boundary"
        # The account where our knowledge of the graph stops is named, not hidden.
        assert out.boundary_account is not None
        assert g.is_sink(out.boundary_account)

    asyncio.run(_run())


def test_insufficient_coverage_on_a_typology_retrieval_never_returned():
    async def _run():
        g = await load_graph()
        labels = await _oracle_labels()
        retrieved = await _retrieved_from_cycle_doc()
        returned = {r["typology"] for r in retrieved}
        # k=3 of a 4-document corpus, so at least one real typology is genuinely excluded.
        assert "GATHER-SCATTER" not in returned

        subject = _first(g.by_id.values(), lambda e: labels.get(e.id) == "CYCLE")
        out = evaluate_claim(
            subject=subject, graph=g, retrieved=retrieved,
            claim=Claim("GATHER-SCATTER", [subject.id], "a hub gathers then scatters"),
        )
        assert out.verdict is Verdict.INSUFFICIENT_COVERAGE
        assert out.reason == "typology_not_retrieved"

        # And a typology that exists nowhere at all.
        out2 = evaluate_claim(
            subject=subject, graph=g, retrieved=retrieved,
            claim=Claim("FAN-IN", [subject.id], "funnel account"),
        )
        assert out2.verdict is Verdict.INSUFFICIENT_COVERAGE
        assert out2.reason == "typology_not_retrieved"

    asyncio.run(_run())


def test_insufficient_coverage_on_a_typology_with_no_sound_witness():
    """STACK is retrieved and real, but every ingested STACK instance is internally disjoint
    (num_components 4..11), so it has no single connected path to witness. It can never FLAG."""

    async def _run():
        g = await load_graph()
        labels = await _oracle_labels()
        retrieved = await _retrieved_from_cycle_doc()
        assert "STACK" in {r["typology"] for r in retrieved}
        assert "STACK" not in FLAG_CAPABLE

        subject = _first(g.by_id.values(), lambda e: labels.get(e.id) == "STACK")
        out = evaluate_claim(
            subject=subject, graph=g, retrieved=retrieved,
            claim=Claim("STACK", [subject.id], "two bipartite layers"),
        )
        assert out.verdict is Verdict.INSUFFICIENT_COVERAGE
        assert out.reason == "typology_not_decidable"

    asyncio.run(_run())


@pytest.mark.parametrize(
    "mutate, expect_error",
    [
        (lambda ids, subj: [uuid.uuid4()] + ids[1:], "does not exist"),
        (lambda ids, subj: [subj.id], "closed cycle"),
        (lambda ids, subj: [i for i in ids if i != subj.id], "does not contain the subject"),
        (lambda ids, subj: [], "no evidence transactions cited"),
    ],
    ids=["fabricated-id", "path-does-not-close", "subject-missing", "no-citation"],
)
def test_validator_withholds_the_flag_when_the_cited_path_does_not_hold_up(mutate, expect_error):
    """Real structure exists, but the model's OWN citation is unfaithful. The graph is not
    consulted to excuse the model: an unsupported assertion never resolves to 'fraud'."""

    async def _run():
        g = await load_graph()
        labels = await _oracle_labels()
        retrieved = await _retrieved_from_cycle_doc()

        subject = _first(g.by_id.values(), lambda e: labels.get(e.id) == "CYCLE")
        real = aml_graph.cycle_witness(g, subject)
        assert real.matched  # the structure genuinely IS there

        out = evaluate_claim(
            subject=subject, graph=g, retrieved=retrieved,
            claim=Claim("CYCLE", mutate(list(real.witness_txn_ids), subject), "trust me"),
        )
        assert out.verdict is Verdict.INSUFFICIENT_COVERAGE
        assert out.reason == "unfaithful_citation"
        assert expect_error in out.validation_errors[0]

    asyncio.run(_run())


def test_a_correct_citation_cannot_manufacture_a_flag_on_a_structureless_edge():
    """The graph, not the citation, is the authority. Citing a genuinely real cycle from
    elsewhere in the extract does not make the SUBJECT part of a cycle."""

    async def _run():
        g = await load_graph()
        labels = await _oracle_labels()
        retrieved = await _retrieved_from_cycle_doc()

        cycle_edge = _first(g.by_id.values(), lambda e: labels.get(e.id) == "CYCLE")
        real_path = aml_graph.cycle_witness(g, cycle_edge).witness_txn_ids

        inert = _first(
            g.by_id.values(),
            lambda e: e.id not in labels
            and not e.is_self_loop
            and aml_graph.cycle_witness(g, e).outcome is Outcome.CONCLUSIVE_NO,
        )
        out = evaluate_claim(
            subject=inert, graph=g, retrieved=retrieved,
            claim=Claim("CYCLE", real_path, "these transactions form a cycle"),
        )
        assert out.verdict is Verdict.NO_FLAG  # not FLAG, and not excused by the real path

    asyncio.run(_run())


def test_a_degenerate_retrieval_margin_does_not_block_a_witnessed_flag():
    """Retrieval distance is provenance, never a gate.

    The agent queries with a NEUTRAL structural summary, and against the live corpus those
    separate the top-2 documents by as little as 0.0005 — a real STACK subject measured exactly
    that. If a margin floor gated the verdict, the brake would reject subjects whose structure
    is fully witnessed, for a reason with no bearing on whether the structure exists. The margin
    is recorded so a reader can see how weak the ranking was; it decides nothing."""

    async def _run():
        g = await load_graph()
        labels = await _oracle_labels()
        subject = _first(g.by_id.values(), lambda e: labels.get(e.id) == "CYCLE")
        witness = aml_graph.cycle_witness(g, subject)

        # A ranking so flat it is effectively noise (the real nonsense-query margin: 0.0014).
        flat = [
            {"typology": "CYCLE", "title": "t", "body": "b", "distance": 0.8822},
            {"typology": "SCATTER-GATHER", "title": "t", "body": "b", "distance": 0.8836},
        ]
        out = evaluate_claim(
            subject=subject, graph=g, retrieved=flat,
            claim=Claim("CYCLE", witness.witness_txn_ids, "funds return to origin"),
        )
        # The structure is really there and really cited, so the flag stands.
        assert out.verdict is Verdict.FLAG
        assert out.retrieval_margin == pytest.approx(0.0014, abs=1e-6)

    asyncio.run(_run())
