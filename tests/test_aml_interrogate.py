"""Roadmap Item 5 — click-to-interrogate a real transaction, with conflict-surfacing.

Every subject below is a row ALREADY in the live cluster (Item 1's deterministic uuid5
ingestion), not a fixture invented for the test. What is locked here:

  * CYCLE's witness is a CLOSED RING — so `predecessor_of` is total and has no "first hop" case.
  * SCATTER-GATHER's witness is NOT a path — asking for a predecessor is a category error, and
    the API says so by status rather than by returning a bare None.
  * The two FLAG-capable typologies never co-witness the same edge (a LIVING INVARIANT, below).
  * Competing structure is SURFACED on every verdict branch and GATES nothing.

CI-SAFE / NON-DESTRUCTIVE: no OpenAI (retrieval queries with a document's OWN stored embedding,
the trick tests/test_corpus.py established), and every query is a read. Unlike the replay and
consistency tests this never calls run_seed, so it cannot wipe the moat backfill.

GROUND TRUTH IS THE ORACLE, NEVER AN INPUT. `is_laundering` and `aml_pattern_members` are read
here only to SCORE findings after the fact. app/services/aml_interrogate.py selects neither.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import text

from app.corpus_models import SOURCE
from app.db import engine
from app.services import aml_graph
from app.services.aml_graph import FLAG_CAPABLE, Outcome, load_graph
from app.services.aml_interrogate import (
    PredecessorStatus,
    TraversalKind,
    interrogate,
    interrogate_transaction,
    predecessor_of,
)
from app.services.corpus import retrieve_typology
from app.services.verdict_guard import Claim, Verdict, evaluate_claim

# Real transaction ids from the live extract. Deterministic uuid5, stable across re-ingestion.
CYCLE_SUBJECT = uuid.UUID("045adfd2-a822-566f-9cd2-6a17fc150539")  # oracle: laundering, CYCLE
CYCLE_WITH_STACK = uuid.UUID("1384b7bc-5a0b-5cc3-9fcb-773d254c7498")  # CYCLE + STACK witness
BENIGN_MULTI = uuid.UUID("3cda6d1d-f765-5001-9342-0478b1a92232")  # BENIGN, 3 witnesses


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


async def _is_laundering(txn_id: uuid.UUID) -> bool:
    """ORACLE ONLY — scores a finding after the fact; never an input to any check."""
    async with engine.connect() as c:
        return (
            await c.execute(
                text("SELECT is_laundering FROM aml_transactions WHERE id = :i"), {"i": txn_id}
            )
        ).scalar_one()


def test_the_two_flag_capable_typologies_never_co_witness_the_same_edge():
    """LIVING INVARIANT, not a permanent fact about the world.

    Measured over all 1,500 edges of the CURRENT extract: CYCLE witnesses 57 edges,
    SCATTER-GATHER witnesses 42, and the two sets are DISJOINT. Nothing about the graph
    guarantees this — it is a property of what Item 1 chose to ingest (20 account-disjoint
    instances), exactly like the FLAG_CAPABLE soundness counts in test_aml_brake.py.

    IF THIS TEST FAILS, IT IS A DECISION POINT, NOT A NUMBER TO UPDATE. A flag-capable
    conflict would have become reachable: one edge on which two sound, flag-authorizing
    typologies each produce a real witness. The brake would then be able to FLAG one story
    while an equally sound witness supports another, and the interrogation surface would need
    to say which — a question this extract never forced anyone to answer. Decide that
    deliberately (does the demo need updating? does the brake need a tie-break rule?) before
    touching the assertion.
    """

    async def _run():
        g = await load_graph()
        edges = list(g.by_id.values())
        cyc = {e.id for e in edges if aml_graph.cycle_witness(g, e).matched}
        sca = {e.id for e in edges if aml_graph.scatter_gather_witness(g, e).matched}

        assert len(cyc) == 57, f"CYCLE witness count moved: {len(cyc)} != 57"
        assert len(sca) == 42, f"SCATTER-GATHER witness count moved: {len(sca)} != 42"

        overlap = cyc & sca
        assert not overlap, (
            f"A FLAG-CAPABLE CONFLICT IS NOW REACHABLE on {len(overlap)} edge(s): "
            f"{sorted(str(i) for i in overlap)[:3]}. Two sound flag-authorizing typologies "
            "witness the same transaction. This invariant held for the whole extract Item 5 was "
            "built against, and the interrogation demo assumes it. Do not simply update this "
            "assertion — decide how the brake and the interrogation surface should present two "
            "competing sound witnesses. See NOTES.md 'Roadmap Item 5'."
        )

    asyncio.run(_run())


def test_cycle_witness_is_a_closed_ring_so_predecessor_is_total():
    """Every CYCLE witness is contiguous AND closes back to the subject's source.

    This is what makes `predecessor_of` total and what justifies PredecessorStatus having no
    FIRST_HOP state: on a closed ring the subject's predecessor is the final hop. If a future
    witness constructor ever emitted an open path, this fails loudly rather than the API
    silently growing a fourth case.
    """

    async def _run():
        g = await load_graph()
        rings = 0
        for e in g.by_id.values():
            c = aml_graph.cycle_witness(g, e)
            if not c.matched:
                continue
            rings += 1
            w = c.witness_txn_ids
            assert all(
                g.by_id[a].dst == g.by_id[b].src for a, b in zip(w, w[1:])
            ), f"cycle witness for {e.id} is not contiguous"
            assert g.by_id[w[-1]].dst == g.by_id[w[0]].src, f"cycle witness for {e.id} is not closed"

            witness = next(x for x in interrogate(g, e)[0] if x.typology == "CYCLE")
            assert witness.kind is TraversalKind.RING
            # The subject wraps to the closing hop — the case that would be "no predecessor"
            # on an open path.
            p = predecessor_of(witness, e.id)
            assert p.status is PredecessorStatus.RESOLVED
            assert p.transaction_id == witness.transaction_ids[-1]
        assert rings == 57

    asyncio.run(_run())


def test_interrogation_resolves_a_real_cycle_traversal_to_real_rows():
    """The click-through: subject -> ordered hops -> real transaction and account rows."""

    async def _run():
        result = await interrogate_transaction(CYCLE_SUBJECT)
        assert result is not None
        assert result["competing_typologies"] == ["CYCLE"]
        assert result["has_competing_structure"] is False

        ring = next(w for w in result["witnesses"] if w.typology == "CYCLE")
        assert ring.outcome is Outcome.MATCH
        assert ring.kind is TraversalKind.RING
        assert ring.flag_capable is True
        assert len(ring.transaction_ids) == 10
        assert ring.transaction_ids[0] == CYCLE_SUBJECT  # the walk starts at the subject

        txns, accts = result["transactions"], result["accounts"]
        hops = [txns[i] for i in ring.transaction_ids]
        # Every hop is a real row, chains into the next, and both its endpoints resolve.
        for a, b in zip(hops, hops[1:]):
            assert a["to_account_id"] == b["from_account_id"]
        assert hops[-1]["to_account_id"] == hops[0]["from_account_id"]
        for h in hops:
            assert accts[h["from_account_id"]]["bank"]
            assert accts[h["to_account_id"]]["account"]

        # "Trace ancestor": hop k's predecessor is hop k-1, a real previous transaction.
        for k in range(1, len(ring.transaction_ids)):
            p = predecessor_of(ring, ring.transaction_ids[k])
            assert p.status is PredecessorStatus.RESOLVED
            assert p.transaction_id == ring.transaction_ids[k - 1]

        # No label column is ever served.
        assert "is_laundering" not in result["subject"]

    asyncio.run(_run())


def test_scatter_gather_witness_has_no_linear_order_and_reports_it_by_status():
    """SG's witness is two scatter legs from one source + two gather legs into one destination.

    It is NOT a path, so `predecessor_of` returns NO_LINEAR_ORDER — distinguishable from
    NOT_IN_WITNESS without parsing any string.
    """

    async def _run():
        g = await load_graph()
        subject = next(
            e
            for e in sorted(g.by_id.values(), key=lambda x: str(x.id))
            if aml_graph.scatter_gather_witness(g, e).matched
        )
        witness = next(w for w in interrogate(g, subject)[0] if w.typology == "SCATTER-GATHER")

        assert witness.kind is TraversalKind.LEGS
        assert witness.legs is not None
        scatter = [g.by_id[i] for i in witness.legs["scatter"]]
        gather = [g.by_id[i] for i in witness.legs["gather"]]
        assert len({e.src for e in scatter}) == 1, "scatter legs must leave ONE source"
        assert len({e.dst for e in gather}) == 1, "gather legs must enter ONE destination"
        assert subject.id in witness.legs["scatter"], "the subject is one of the scatter legs"
        # The two legs are genuinely not contiguous — this is why RING would be a lie here.
        ids = witness.transaction_ids
        assert not all(g.by_id[a].dst == g.by_id[b].src for a, b in zip(ids, ids[1:]))

        p = predecessor_of(witness, subject.id)
        assert p.status is PredecessorStatus.NO_LINEAR_ORDER
        assert p.transaction_id is None

        # A different status entirely — a caller tells these apart on the enum alone.
        other = next(e for e in g.by_id.values() if e.id not in ids)
        assert predecessor_of(witness, other.id).status is PredecessorStatus.NOT_IN_WITNESS

    asyncio.run(_run())


def test_a_benign_transaction_carries_three_competing_witnesses():
    """The honest exhibit for CYCLE's 75.4% precision.

    This row is BENIGN by the oracle, yet CYCLE, GATHER-SCATTER and STACK all produce real
    structural witnesses on it. Interrogation surfaces all three rather than reporting the one
    that happens to be flag-capable.
    """

    async def _run():
        assert await _is_laundering(BENIGN_MULTI) is False  # ORACLE, after the fact

        result = await interrogate_transaction(BENIGN_MULTI)
        assert result["has_competing_structure"] is True
        assert result["competing_typologies"] == ["CYCLE", "GATHER-SCATTER", "STACK"]

        by_typ = {w.typology: w for w in result["witnesses"]}
        assert by_typ["CYCLE"].kind is TraversalKind.RING
        # Neither competitor is flag-capable — they explain, they do not authorize.
        assert by_typ["GATHER-SCATTER"].flag_capable is False
        assert by_typ["STACK"].flag_capable is False
        assert by_typ["GATHER-SCATTER"].kind is TraversalKind.BUNDLE

    asyncio.run(_run())


def test_competing_structure_is_surfaced_but_never_gates_a_flag():
    """A non-flag-capable competing witness must not withhold a corroborated FLAG.

    Letting it would repeat the MARGIN_FLOOR mistake: evidence that gates nothing must not
    start gating. The competitor is disclosed on the outcome instead.
    """

    async def _run():
        g = await load_graph()
        retrieved = await _retrieved_from_cycle_doc()
        subject = g.by_id[CYCLE_WITH_STACK]

        assert aml_graph.stack_witness(g, subject).matched  # a real competing witness exists

        faithful = aml_graph.cycle_witness(g, subject).witness_txn_ids
        out = evaluate_claim(
            subject=subject,
            graph=g,
            retrieved=retrieved,
            claim=Claim("CYCLE", list(faithful), "funds return to the source account"),
        )
        assert out.verdict is Verdict.FLAG, "a non-flag-capable competitor must not gate the flag"
        assert "STACK" in out.competing_typologies
        assert "CYCLE" not in out.competing_typologies  # the claimed typology is not its own rival

    asyncio.run(_run())


def test_competing_witness_is_surfaced_on_the_inconclusive_branch():
    """The gap the old first-hit, CONCLUSIVE_NO-only helper left open.

    On all 42 SCATTER-GATHER witnesses, CYCLE's search runs into a sink -> INCONCLUSIVE, a
    branch the old `_competing_match` never ran on. So a live SG witness sat on the subject and
    was never mentioned. The verdict is unchanged (uncertainty still never resolves to fraud);
    the competing evidence is now reported alongside it.
    """

    async def _run():
        g = await load_graph()
        retrieved = await _retrieved_from_cycle_doc()
        subject = next(
            e
            for e in sorted(g.by_id.values(), key=lambda x: str(x.id))
            if aml_graph.scatter_gather_witness(g, e).matched
        )
        assert aml_graph.cycle_witness(g, subject).outcome is Outcome.INCONCLUSIVE

        out = evaluate_claim(
            subject=subject, graph=g, retrieved=retrieved,
            claim=Claim("CYCLE", [subject.id], "the funds appear to return"),
        )
        assert out.verdict is Verdict.INSUFFICIENT_COVERAGE
        assert out.reason == "search_reached_extract_boundary"
        assert "SCATTER-GATHER" in out.competing_typologies

    asyncio.run(_run())


def test_an_inconclusive_boundary_account_resolves_to_a_real_account():
    """INSUFFICIENT_COVERAGE names the account where our knowledge of the graph stops. That
    account is a NODE of the money-flow graph, and interrogation resolves it to a real row."""

    async def _run():
        g = await load_graph()
        subject = next(
            e
            for e in sorted(g.by_id.values(), key=lambda x: str(x.id))
            if aml_graph.cycle_witness(g, e).outcome is Outcome.INCONCLUSIVE
        )
        result = await interrogate_transaction(subject.id)
        witness = next(w for w in result["witnesses"] if w.typology == "CYCLE")

        assert witness.outcome is Outcome.INCONCLUSIVE
        assert witness.kind is TraversalKind.NONE
        assert witness.transaction_ids == []
        assert witness.boundary_account_id is not None

        acct = result["accounts"][witness.boundary_account_id]
        assert acct["bank"] and acct["account"]  # a real (bank, account) node, not a bare uuid

    asyncio.run(_run())


def test_interrogation_time_travels_with_real_aost():
    """`as_of` pins graph AND rows to one MVCC snapshot. The evidence layer is static reference
    data, so a within-window replay must reproduce the present interrogation exactly."""

    async def _run():
        async with engine.connect() as c:
            hlc = (await c.execute(text("SELECT cluster_logical_timestamp()"))).scalar_one()

        now = await interrogate_transaction(BENIGN_MULTI)
        past = await interrogate_transaction(BENIGN_MULTI, as_of=str(hlc))

        assert past is not None
        assert past["competing_typologies"] == now["competing_typologies"]
        assert [w.transaction_ids for w in past["witnesses"]] == [
            w.transaction_ids for w in now["witnesses"]
        ]

    asyncio.run(_run())


def test_an_out_of_window_as_of_is_a_value_error_not_a_crash():
    """Mirrors the deposition/replay contract: the router maps this to 400, never a 500."""

    async def _run():
        with pytest.raises(ValueError, match="outside the time-travel window"):
            await interrogate_transaction(CYCLE_SUBJECT, as_of="2020-01-01T00:00:00Z")

    asyncio.run(_run())


def test_an_unknown_transaction_is_not_found_rather_than_an_error():
    async def _run():
        assert await interrogate_transaction(uuid.UUID(int=0)) is None

    asyncio.run(_run())


def test_flag_capable_membership_is_what_the_interrogation_reports():
    """The interrogation's `flag_capable` is read off aml_graph.FLAG_CAPABLE, not duplicated.
    If a future ingestion changes which typologies may authorize a flag, this surface follows."""

    async def _run():
        g = await load_graph()
        witnesses, _ = interrogate(g, g.by_id[CYCLE_SUBJECT])
        assert {w.typology for w in witnesses if w.flag_capable} == set(FLAG_CAPABLE)

    asyncio.run(_run())


# The four-way basis, EXACTLY as the AML console re-derives it from the wire. See the test below.
DETAIL_SELF_LOOP = "self-loop is not a transfer cycle"
DETAIL_CLOSED = "no return path; search closed inside the extract"
FOUR_WAY = {"MATCH": 57, "INCONCLUSIVE": 980, "SELF_LOOP": 447, "CLOSED_SEARCH": 16}


def test_the_console_four_way_basis_re_derives_from_account_identity_not_from_prose():
    """THE CONSOLE'S PREDICATE IS THE GRAPH'S PREDICATE — asserted over all 1,500 edges.

    Rung 2 renders FOUR bases, not three: MATCH / INCONCLUSIVE / CONCLUSIVE_NO-self-loop /
    CONCLUSIVE_NO-closed-search. The fourth state is a property of the EVIDENCE (re-derived from
    the graph), never of what the agent RECORDED — which is why the persisted `txn_ref` tag stays
    three-way and this distinction lives on /interrogate. All of that is settled.

    WHAT THIS TEST EXISTS FOR is HOW the console reads it. `detail` carries the distinction as
    PROSE ("self-loop is not a transfer cycle" vs "no return path; search closed inside the
    extract"), and a client that string-matches that prose is a PROXY: reword the sentence in
    aml_graph and the console silently renders a self-loop as a closed search — corrupting, in
    pixels, the exact distinction Rung 1 was corrected to make, with nothing failing.

    So the console re-derives the self-loop the way `aml_graph.Edge` itself does — from the SUBJECT
    ROW ON THE WIRE, `from_account_id == to_account_id` — and this test pins the equivalence:

        (subject.from_account_id == subject.to_account_id)   [what the console can see]
                          <==>  edge.is_self_loop            [what the graph decides on]
                          <==>  CYCLE detail is DETAIL_SELF_LOOP

    A reword now FAILS A TEST instead of corrupting a pixel. The wire fact and the prose fact are
    checked against each other, so neither can drift alone.
    """

    async def _run():
        g = await load_graph()
        result_of = {}
        for e in g.by_id.values():
            chk = aml_graph.check(g, e, "CYCLE")
            # The two fields the console actually has: the subject row's two account ids.
            wire_self_loop = e.src == e.dst
            result_of[e.id] = (wire_self_loop, e.is_self_loop, chk.outcome, chk.detail)
        return result_of

    result_of = asyncio.run(_run())
    assert len(result_of) == 1500, len(result_of)

    counts = {k: 0 for k in FOUR_WAY}
    for txn_id, (wire, graph_says, outcome, detail) in result_of.items():
        # 1. The wire predicate and the graph predicate are the SAME predicate.
        assert wire == graph_says, f"{txn_id}: wire says self-loop={wire}, graph says {graph_says}"

        # 2. A self-loop is ALWAYS CONCLUSIVE_NO, and always for the self-loop reason. It can never
        #    be MATCH (it is not a transfer between two accounts, so it is not in adjacency at all)
        #    and never INCONCLUSIVE (no search ran, so no search could reach a boundary).
        if wire:
            assert outcome is Outcome.CONCLUSIVE_NO, f"{txn_id}: self-loop decided {outcome}"
            assert detail == DETAIL_SELF_LOOP, f"{txn_id}: {detail!r}"
            counts["SELF_LOOP"] += 1
        elif outcome is Outcome.CONCLUSIVE_NO:
            # 3. And the converse: a CONCLUSIVE_NO that is NOT a self-loop is a REAL closed search.
            #    These are the only 16 rows the old "searched; there is no cycle" gloss ever described.
            assert detail == DETAIL_CLOSED, f"{txn_id}: {detail!r}"
            counts["CLOSED_SEARCH"] += 1
        elif outcome is Outcome.MATCH:
            counts["MATCH"] += 1
        else:
            counts["INCONCLUSIVE"] += 1

    # 4. The partition the console renders IS the partition the decider computes. Four buckets,
    #    summing to the whole extract — so a self-loop and a closed search can never render alike.
    assert counts == FOUR_WAY, counts
    assert sum(counts.values()) == 1500
