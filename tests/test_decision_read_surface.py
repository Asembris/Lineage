"""G5 — the decision read surface: the reverse lookup, and the basis tag made first-class.

The grounding seam (G2/G3/G4) made the causal chain EXIST in the database. This surface makes it
RESOLVABLE. The forward direction already composed from existing endpoints — decision ->
GET /aml/transactions/{id} -> GET /beliefs/{id}/lineage -> azure-0, every hop a real row — so
nothing here wraps it. What did NOT exist is what these tests cover:

  1. THE REVERSE LOOKUP. The FK runs decisions -> aml_transactions. Nothing resolved the other way:
     looking at a flagged transaction, there was no way to ask "did any agent act on this?" No
     route, and (measured before migration 0008) no index — a FULL SCAN of the whole table.

  2. THE 65.3% DISCLOSURE, legible from the RESPONSE rather than only from the data. `txn_ref`
     always carried the witness outcome, but as an undocumented string convention. An API caller saw
     1,443 approvals and could not tell that 980 of them mean "we could not tell" rather than "this
     is clean". `witness_outcome` is now a field, and migration 0008 makes the tag it projects
     STRUCTURAL — a future backfill writing `txn_ref = str(txn_id)` is rejected by CockroachDB.

NO TEST HERE DEPENDS ON A BACKFILL, and that is deliberate. `seed.seed()` DELETEs every row of
`decisions`, and several tests in this suite call it — so any assertion against "the 4,000 card rows"
or "the 1,500 AML rows" would pass or fail on test ORDERING. The house rule (test_read_endpoints.py's
idiom) is: reseed, then insert the controlled set you assert on. So:

  * the READ-SURFACE tests seed their own small world (three AML decisions, one per witness outcome,
    citing real `aml_transactions` rows + some card decisions);
  * the CENSUS (57/463/980, 43/5/252) is asserted against the DECIDER run over the real evidence
    layer — `aml_transactions` is reference data that `seed.seed()` never touches — so it is a fact
    about the witness and the extract, not about whether a backfill happened to have run.

    (Before this file, that census was asserted NOWHERE. `aml_seam.py`'s docstring claimed it was
    checked by `scripts/verify_seam.py` and `tests/test_grounding_seam.py`; neither ever contained
    it, and the first does not exist. A number defended only by a docstring that points at a missing
    file is not defended.)

CI-SAFE: no OpenAI. Reseeds (like test_read_endpoints / test_atomic_invalidation already do), so the
two backfills must be restored afterwards — see NOTES "THE TWO-BACKFILL LANDMINE".
"""

import asyncio
import datetime as dt
import re
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import insert, text
from sqlalchemy.exc import IntegrityError

from app.db import engine
from app.main import app
from app.models import Decision
from app.services.aml_graph import Outcome, load_graph
from app.services.aml_seam import TXN_REF_TAGS, census, decide_all, txn_ref_for, witness_outcome_of
from seed.seed import aid, bid
from seed.seed import seed as run_seed

ORIGIN = bid("origin")        # crimson's card belief
AML_BELIEF = bid("aml-cycle")  # azure-0's laundering belief (G3)
AZURE_7 = aid("azure-7")       # the LIVING holder that applies it
CRIMSON_7 = aid("crimson-7")

# The measured census of the CYCLE witness over all 1,500 ingested edges — Item 4's frozen
# constants. NOT to be re-baselined: if these move, `aml_*` was re-ingested or re-sampled, which is
# separately prohibited (it would also move Item 7's eval inputs). The failure is the point.
CENSUS_N = {"MATCH": 57, "CONCLUSIVE_NO": 463, "INCONCLUSIVE": 980}
CENSUS_LAUNDERING = {"MATCH": 43, "CONCLUSIVE_NO": 5, "INCONCLUSIVE": 252}
TOTAL_EDGES = 1500

_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations" / "versions" / "0008_seam_read_surface.py"
)

# The ORACLE, read HERE and only here — in a test, after the fact, to SCORE a result. This is the
# sanctioned use (tests/test_oracle_boundary.py's own closing words). No module on the deciding path
# may do this, and none does.
_LABELS = text("SELECT id, is_laundering FROM aml_transactions")


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def _scalar(sql: str, **params):
    async with engine.connect() as c:
        return (await c.execute(text(sql), params)).scalar()


# ---------------------------------------------------------------------------------------------
# The controlled world. Reseed, then insert exactly what we assert on.
# ---------------------------------------------------------------------------------------------

async def _seed_controlled_world() -> dict[str, uuid.UUID]:
    """3 AML decisions (one per witness outcome, citing REAL transactions) + 5 card decisions."""
    await run_seed()  # leaves `decisions` empty

    async with engine.connect() as c:
        txns = [
            r[0]
            for r in (
                await c.execute(text("SELECT id FROM aml_transactions ORDER BY id LIMIT 3"))
            ).all()
        ]

    at = dt.datetime(2026, 7, 12, 12, 0, tzinfo=dt.timezone.utc)
    by_outcome = dict(zip((Outcome.MATCH, Outcome.CONCLUSIVE_NO, Outcome.INCONCLUSIVE), txns))

    aml_rows = [
        {
            "id": uuid.uuid4(),
            "agent_id": AZURE_7,
            "txn_ref": txn_ref_for(o),          # the BASIS — 0008 makes anything else unwritable
            "merchant": None,                    # an AML transfer has no merchant (0007)
            "amount": 100.0,
            "amount_currency": "Euro",
            "verdict": "blocked" if o is Outcome.MATCH else "approve",
            "driving_belief_id": AML_BELIEF,
            "confidence": None,                  # the witness is deterministic (0007)
            "decided_at": at,
            "is_fraud": o is Outcome.MATCH,
            "aml_transaction_id": t,
        }
        for o, t in by_outcome.items()
    ]
    card_rows = [
        {
            "id": uuid.uuid4(),
            "agent_id": CRIMSON_7,
            "txn_ref": f"txn-{i:04d}",
            "merchant": "Grocery Mart #453",
            "amount": 85.44,
            "amount_currency": None,             # the card world had no currency concept
            "verdict": "approve",
            "driving_belief_id": ORIGIN,
            "confidence": 0.87,
            "decided_at": at - dt.timedelta(days=30, minutes=i),
            "is_fraud": False,
            "aml_transaction_id": None,
        }
        for i in range(5)
    ]
    async with engine.begin() as c:
        await c.execute(insert(Decision), aml_rows + card_rows)

    return {o.value: t for o, t in by_outcome.items()}


# ---------------------------------------------------------------------------------------------
# 1. THE REVERSE LOOKUP — the direction that did not exist
# ---------------------------------------------------------------------------------------------


def test_the_reverse_lookup_resolves_a_transaction_to_the_decision_made_about_it():
    """transaction -> decision. The seam's FK only ever went the other way.

    This is the hop that completes the chain in BOTH directions: a reader interrogating a flagged
    money-flow edge can now ask "did any agent act on this, and what did it decide?" — and the
    answer carries `driving_belief_id`, which is the next hop back to azure-0 via existing routes.
    """

    async def _run():
        txns = await _seed_controlled_world()
        matched = txns["MATCH"]

        async with _client() as c:
            r = await c.get(f"/decisions?aml_transaction_id={matched}")
        assert r.status_code == 200, r.text
        body = r.json()

        assert body["total"] == 1, body
        assert body["aml_transaction_id"] == str(matched)  # the filter is echoed back

        d = body["decisions"][0]
        assert d["aml_transaction_id"] == str(matched)
        assert d["agent_id"] == str(AZURE_7)
        assert d["driving_belief_id"] == str(AML_BELIEF)  # the chain's next hop resolves
        assert d["verdict"] == "blocked"                   # a re-derived cycle is blocked
        assert d["witness_outcome"] == "MATCH"
        assert d["merchant"] is None and d["confidence"] is None
        assert d["amount_currency"] == "Euro"

        # And the belief hop really does walk home to the founding ancestor, through the route that
        # already existed — the point of the reverse lookup is that it FEEDS this, not wraps it.
        async with _client() as c:
            lin = await c.get(f"/beliefs/{d['driving_belief_id']}/lineage")
        assert lin.status_code == 200
        assert lin.json()["origin_agent_id"] == str(aid("azure-0"))

    asyncio.run(_run())


def test_a_transaction_no_agent_ruled_on_is_an_honest_empty_answer_not_a_404():
    """`total: 0` is the truthful answer to "did anyone act on this?" — never a fabricated row."""

    async def _run():
        await _seed_controlled_world()
        # A real AML transaction that this world's agent never decided on.
        untouched = await _scalar(
            "SELECT t.id FROM aml_transactions t WHERE NOT EXISTS "
            "(SELECT 1 FROM decisions d WHERE d.aml_transaction_id = t.id) LIMIT 1"
        )
        async with _client() as c:
            real = await c.get(f"/decisions?aml_transaction_id={untouched}")
            unknown = await c.get(f"/decisions?aml_transaction_id={uuid.uuid4()}")
        for r in (real, unknown):
            assert r.status_code == 200, r.text
            assert r.json()["total"] == 0
            assert r.json()["decisions"] == []

    asyncio.run(_run())


def test_the_reverse_lookup_is_not_a_full_scan():
    """A LIVING guard on migration 0008's partial index, not a one-off benchmark.

    Before 0008 this query was `scan ... spans: FULL SCAN, estimated row count: 5,500 (100% of the
    table)` — CockroachDB's optimizer volunteered the index recommendation unprompted. The PARTIAL
    index (`WHERE aml_transaction_id IS NOT NULL`) is only usable if the optimizer PROVES that
    `col = $1` implies `col IS NOT NULL`. That implication is CockroachDB's to make, not ours to
    assume, so it is asserted against the real planner: drop the index and this fails with the real
    FULL SCAN plan in the message.
    """

    async def _run():
        txn = await _scalar("SELECT id FROM aml_transactions LIMIT 1")
        async with engine.connect() as c:
            rows = (
                await c.execute(
                    text("EXPLAIN SELECT id, verdict FROM decisions WHERE aml_transaction_id = :t"),
                    {"t": txn},
                )
            ).all()
        plan = "\n".join(r[0] for r in rows)
        assert "ix_decisions_aml_txn" in plan, f"the seam's index is not used:\n{plan}"
        assert "FULL SCAN" not in plan, f"the reverse lookup is scanning the table:\n{plan}"

    asyncio.run(_run())


# ---------------------------------------------------------------------------------------------
# 2. THE CENSUS — the 65.3%, asserted against the real extract (not against a backfill)
# ---------------------------------------------------------------------------------------------


def test_the_witness_census_over_the_real_extract_is_57_463_980():
    """THE DISCLOSURE ITSELF, measured — and until this test, asserted nowhere in the repo.

    Run the frozen, label-free decider over all 1,500 real edges and count. Then read the oracle
    (in a test, after the fact — the sanctioned use) and count how much laundering each outcome
    carries. The result is the single most important caveat about the azure belief:

        TWO OUTCOMES MAP TO `approve`, and the bigger one means "we could not tell".

    Independent of any backfill: `aml_transactions` is reference data and `seed.seed()` never
    touches it. If these numbers move, the evidence layer was re-ingested or re-sampled — which is
    prohibited precisely because it would also move Item 4's constants and Item 7's eval inputs.
    """

    async def _run():
        async with engine.connect() as c:
            graph = await load_graph(conn=c)          # SELECTs no label column
            decisions = decide_all(graph)             # pure; sees no label, by type
            labels = {
                r["id"]: bool(r["is_laundering"])
                for r in (await c.execute(_LABELS)).mappings().all()
            }
        return decisions, census(decisions), labels

    decisions, counts, labels = asyncio.run(_run())

    assert len(decisions) == TOTAL_EDGES
    for outcome, n in CENSUS_N.items():
        assert counts[Outcome(outcome)] == n, f"{outcome}: {counts[Outcome(outcome)]} != {n}"

    laundering = {o: 0 for o in CENSUS_N}
    for d in decisions:
        if labels[d.txn_id]:
            laundering[d.witness_outcome.value] += 1
    assert laundering == CENSUS_LAUNDERING, laundering

    # The two approving outcomes, and the price of the third line of the verdict mapping.
    approvals = CENSUS_N["CONCLUSIVE_NO"] + CENSUS_N["INCONCLUSIVE"]
    assert approvals == 1443
    assert CENSUS_N["INCONCLUSIVE"] / TOTAL_EDGES == pytest.approx(0.653, abs=0.001)
    # It silently approves 252 of the extract's 300 laundering rows.
    assert sum(CENSUS_LAUNDERING.values()) == 300
    assert CENSUS_LAUNDERING["INCONCLUSIVE"] == 252
    # And it is NOT "728 / 48.5%" — the benign-only subset this project has twice mistaken for it.
    assert CENSUS_N["INCONCLUSIVE"] != 728


def test_witness_outcome_projects_the_basis_through_http():
    """Each of the three outcomes survives the round trip to a caller, as a FIELD.

    `verdict` alone cannot distinguish CONCLUSIVE_NO from INCONCLUSIVE — both are `approve`. That is
    the entire reason this field exists.
    """

    async def _run():
        txns = await _seed_controlled_world()
        async with _client() as c:
            r = await c.get("/decisions?kind=aml&limit=200")
        assert r.status_code == 200, r.text
        rows = {d["witness_outcome"]: d for d in r.json()["decisions"]}

        assert set(rows) == {"MATCH", "CONCLUSIVE_NO", "INCONCLUSIVE"}
        assert rows["MATCH"]["verdict"] == "blocked"
        # THE POINT: two different bases, one indistinguishable verdict.
        assert rows["CONCLUSIVE_NO"]["verdict"] == rows["INCONCLUSIVE"]["verdict"] == "approve"
        assert rows["CONCLUSIVE_NO"]["witness_outcome"] != rows["INCONCLUSIVE"]["witness_outcome"]
        for outcome, d in rows.items():
            assert d["aml_transaction_id"] == str(txns[outcome])
            assert d["txn_ref"] == f"aml:{outcome}"  # the field is a projection OF this

    asyncio.run(_run())


def test_witness_outcome_is_null_for_a_card_decision_never_invented():
    """A card decision has no witness. The field is absent, not defaulted to something plausible."""

    async def _run():
        await _seed_controlled_world()
        async with _client() as c:
            r = await c.get("/decisions?kind=card&limit=50")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 5
        for d in body["decisions"]:
            assert d["aml_transaction_id"] is None
            assert d["witness_outcome"] is None
            # The card shape 0007 restored: a real merchant and a real confidence.
            assert d["merchant"] is not None and d["confidence"] is not None

    asyncio.run(_run())


def test_the_two_kinds_partition_the_feed_exactly():
    """`kind` is a real partition, not a convenience: aml + card = every row, no overlap."""

    async def _run():
        await _seed_controlled_world()
        async with _client() as c:
            both = await c.get("/decisions?limit=1")
            aml = await c.get("/decisions?kind=aml&limit=1")
            card = await c.get("/decisions?kind=card&limit=1")
        t_all, t_aml, t_card = (x.json()["total"] for x in (both, aml, card))
        assert (t_aml, t_card) == (3, 5)
        assert t_aml + t_card == t_all == 8

    asyncio.run(_run())


def test_the_belief_filter_makes_discoverability_semantic_not_accidental():
    """The azure belief's decisions, asked for BY THE BELIEF.

    On the live cluster the AML decisions are reachable today only by two ACCIDENTS of this seed —
    they share one fixed `decided_at` NEWER than every card decision (so a newest-first feed happens
    to put them on page 1), and azure-7 happens to make no card decisions (so `?agent_id=` happens to
    isolate them). Neither is a guarantee; both are facts about the seed. A future session that moved
    the seam's `decided_at` or gave azure-7 a card decision would break discoverability silently.
    The SEMANTIC filters are what make that survivable, and this asserts they agree.
    """

    async def _run():
        await _seed_controlled_world()
        async with _client() as c:
            by_belief = await c.get(f"/decisions?driving_belief_id={AML_BELIEF}&limit=200")
            by_kind = await c.get("/decisions?kind=aml&limit=1")
            by_agent = await c.get(f"/decisions?agent_id={AZURE_7}&limit=1")
            card_belief = await c.get(f"/decisions?driving_belief_id={ORIGIN}&limit=1")
        assert by_belief.json()["total"] == 3
        assert by_belief.json()["driving_belief_id"] == str(AML_BELIEF)
        assert all(d["witness_outcome"] for d in by_belief.json()["decisions"])
        # The two contracts and the one accident agree today. Only the first two are contracts.
        assert by_kind.json()["total"] == by_agent.json()["total"] == 3
        # The crimson belief is untouched by any of this — the closures stay disjoint.
        assert card_belief.json()["total"] == 5

    asyncio.run(_run())


def test_filters_and_together_rather_than_overriding_one_another():
    """A filter combination that matches nothing returns 0 — it must not silently widen."""

    async def _run():
        await _seed_controlled_world()
        async with _client() as c:
            # crimson-7 made no AML decision, so agent AND kind must intersect to nothing.
            r = await c.get(f"/decisions?agent_id={CRIMSON_7}&kind=aml")
            # ...and azure-7 + the AML belief is the real intersection.
            hit = await c.get(f"/decisions?agent_id={AZURE_7}&driving_belief_id={AML_BELIEF}")
        assert r.json()["total"] == 0
        assert hit.json()["total"] == 3

    asyncio.run(_run())


def test_an_unknown_kind_is_422_never_a_silent_empty_page():
    """`?kind=laundering` must not quietly return zero rows as if that were the answer."""

    async def _run():
        async with _client() as c:
            r = await c.get("/decisions?kind=laundering")
        assert r.status_code == 422, r.text

    asyncio.run(_run())


def test_the_disclosure_reaches_the_openapi_schema():
    """The 65.3% must be readable by a caller who only ever sees /openapi.json.

    NOTES records the number in three places (the decider's docstring, the backfill's printed
    output, the txn_ref tag on the data). A read surface is the FOURTH, and its audience is someone
    who never opens the repo. If the DTO docstring stops carrying it, this fails.
    """
    schema = app.openapi()["components"]["schemas"]["DecisionOut"]
    assert "witness_outcome" in schema["properties"]
    blurb = schema.get("description", "")
    assert "65.3%" in blurb
    assert "252" in blurb  # the laundering rows the INCONCLUSIVE->approve mapping lets through
    assert "INCONCLUSIVE" in blurb and "CONCLUSIVE_NO" in blurb


# ---------------------------------------------------------------------------------------------
# 3. THE TAG IS STRUCTURAL — migration 0008. The guard that protects the disclosure's carrier.
# ---------------------------------------------------------------------------------------------


def test_the_migrations_check_pins_exactly_the_seams_tags():
    """The migration's SQL literals ARE `aml_seam.TXN_REF_TAGS`.

    A migration cannot import application code (it must stay runnable against any past revision), so
    the basis-tag vocabulary is necessarily written twice: once in the decider that owns the outcome
    enum, once in the CHECK that enforces it. This test is the only thing preventing those two from
    drifting — add a fourth Outcome and it fails until the migration is dealt with deliberately.
    """
    src = _MIGRATION.read_text(encoding="utf-8")
    tags_line = re.search(r"^_TAGS = \"\((.*)\)\"$", src, re.M)
    assert tags_line, "0008's _TAGS constant is not where this test expects it"
    in_migration = tuple(t.strip().strip("'") for t in tags_line.group(1).split(","))
    assert in_migration == TXN_REF_TAGS, (
        f"migration 0008 enforces {in_migration} but the decider writes {TXN_REF_TAGS}"
    )
    assert TXN_REF_TAGS == ("aml:MATCH", "aml:CONCLUSIVE_NO", "aml:INCONCLUSIVE")


def test_the_database_rejects_an_aml_decision_whose_basis_tag_is_not_real():
    """THE GUARD THAT KEEPS THE 65.3% ALIVE — a database constraint, not a convention.

    Nothing used to stop a future backfill writing `txn_ref = str(txn_id)`. That is the OBVIOUS
    thing to write — `txn_ref` means "transaction reference" on every other row in the table — and
    it would silently destroy the only in-data carrier of the coverage split, with no test failing.
    (Verified: against 0007's constraint the database ACCEPTS it. This test fails `DID NOT RAISE`.)
    Migration 0008 makes it unwritable. Each shape below is a real INSERT against the live cluster.

    Rejected inserts leave no rows; the one accepted probe is deleted in a finally.
    """

    async def _run():
        agent = AZURE_7
        txn = await _scalar("SELECT id FROM aml_transactions LIMIT 1")

        async def _insert(txn_ref: str) -> uuid.UUID:
            pid = uuid.uuid4()
            async with engine.begin() as c:
                await c.execute(
                    text(
                        "INSERT INTO decisions (id, agent_id, txn_ref, merchant, amount, "
                        "amount_currency, verdict, driving_belief_id, confidence, decided_at, "
                        "is_fraud, aml_transaction_id) VALUES (:i, :a, :r, NULL, 1.00, 'Euro', "
                        "'approve', :b, NULL, now(), false, :t)"
                    ),
                    {"i": pid, "a": agent, "r": txn_ref, "b": AML_BELIEF, "t": txn},
                )
            return pid

        # The exact future mistake, plus three neighbours of it.
        for bogus in (str(txn), "aml:", "aml:MATCHED", "txn-0001"):
            with pytest.raises(IntegrityError) as exc:
                await _insert(bogus)
            # GOTCHA (0007, still true): CockroachDB reports a CHECK by its EXPRESSION, not its
            # NAME, so `"ck_decisions_kind" in str(err)` would FAIL. Assert the violation CLASS —
            # which still distinguishes it from a foreign-key or NOT NULL rejection.
            assert "CheckViolation" in type(exc.value.orig).__name__, exc.value.orig

        # And a REAL tag is ACCEPTED — otherwise the four rejections above would prove only that the
        # INSERT was malformed, not that the CHECK discriminates. (That is the trap G2's guard 3 fell
        # into, and this very migration sprang it again on two of G2's tests: a test that passes for
        # the wrong reason is worse than no test.)
        pid = await _insert("aml:INCONCLUSIVE")
        try:
            assert await _scalar("SELECT txn_ref FROM decisions WHERE id = :i", i=pid) == (
                "aml:INCONCLUSIVE"
            )
        finally:
            async with engine.begin() as c:
                await c.execute(text("DELETE FROM decisions WHERE id = :i"), {"i": pid})

    asyncio.run(_run())


def test_witness_outcome_of_is_a_projection_and_refuses_to_guess():
    """Pure, hermetic. It reads a tag; it never re-runs a witness and never invents an outcome.

    The distinction is load-bearing: this reports what the agent RECORDED at decision time, not what
    the graph would say NOW. Those are different questions, and conflating them in one payload is
    how "every field individually true, the juxtaposition fabricated" happens. Re-deriving the
    witness against today's graph is what GET /aml/transactions/{id}/interrogate is for.
    """
    assert witness_outcome_of("aml:MATCH") == "MATCH"
    assert witness_outcome_of("aml:CONCLUSIVE_NO") == "CONCLUSIVE_NO"
    assert witness_outcome_of("aml:INCONCLUSIVE") == "INCONCLUSIVE"
    # A card txn_ref carries no basis, and nothing plausible is manufactured for it.
    assert witness_outcome_of("txn-0001") is None
    # A tag that is not a real outcome is NOT passed through as if it were one.
    assert witness_outcome_of("aml:MATCHED") is None
    assert witness_outcome_of("aml:") is None
    # Every tag the decider can write round-trips.
    for o in Outcome:
        assert witness_outcome_of(txn_ref_for(o)) == o.value
