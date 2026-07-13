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
from app.services.aml_graph import Outcome, check, load_graph
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

# CONCLUSIVE_NO IS NOT ONE THING, and its gloss said it was for the whole life of the seam.
# 447 of the 463 are SELF-LOOPS — an account paying itself, excluded from adjacency by construction
# (`aml_graph.Graph`), so NO SEARCH EVER RAN. Only 16 are real transfers whose cycle search genuinely
# closed inside the extract. Measured by scripts/probe_conclusive_no.py; asserted below.
CONCLUSIVE_NO_SELF_LOOPS = 447
CONCLUSIVE_NO_CLOSED_SEARCHES = 16

# The `detail` strings the witness emits. THIS is the wire's four-way basis: /interrogate serves
# `detail` on every witness, which is why the fourth state costs no schema change and no new field.
DETAIL_SELF_LOOP = "self-loop is not a transfer cycle"
DETAIL_CLOSED = "no return path; search closed inside the extract"

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


def test_the_conclusive_no_decomposition_is_447_selfloops_and_16_closed_searches():
    """THE THIRD CORRUPTION ADJACENT TO THE 65.3% — and the only thing that can prevent a fourth.

    The frozen census says `CONCLUSIVE_NO 463`, and the gloss that travelled with it everywhere —
    the decider's docstring, DecisionOut's docstring (and therefore /openapi.json), README, DEMO's
    Bridge beat, the honesty ledger — was **"searched; there is no cycle"**.

    That is true of SIXTEEN of them. The other 447 are SELF-LOOPS: an account paying itself, which
    `aml_graph.Graph` excludes from adjacency by construction because it is not a transfer between
    two accounts. NO SEARCH EVER RAN. The gloss invited a reader to picture a region that was
    explored and closed, for 96.5% of the rows where nothing was explored at all.

    THE COUNT WAS NEVER WRONG. ITS DESCRIPTION OF ITSELF WAS — which is the shape of every
    corruption this number has attracted, and it has now attracted one of each available kind:
        * the phantom "728 / 48.5%"           — MISSTATED ITS VALUE
        * the phantom scripts/verify_seam.py  — INVENTED ITS PROVENANCE
        * this one                            — MISDESCRIBED ITS OWN COMPLEMENT
    Prose has failed this number in all three ways. Only executable things have ever protected it,
    so the decomposition is ASSERTED here rather than described anywhere.

    Independent of any backfill (`aml_transactions` is reference data; `seed.seed()` never touches
    it), like the census test above. If these numbers move, the evidence layer was re-ingested or
    re-sampled — which is separately prohibited.
    """

    async def _run():
        async with engine.connect() as c:
            graph = await load_graph(conn=c)  # SELECTs no label column
            return graph, decide_all(graph)   # pure; sees no label, by type

    graph, decisions = asyncio.run(_run())

    conclusive = [d for d in decisions if d.witness_outcome is Outcome.CONCLUSIVE_NO]
    assert len(conclusive) == CENSUS_N["CONCLUSIVE_NO"]

    self_loops = [d for d in conclusive if graph.by_id[d.txn_id].is_self_loop]
    closed = [d for d in conclusive if not graph.by_id[d.txn_id].is_self_loop]

    assert len(self_loops) == CONCLUSIVE_NO_SELF_LOOPS, (
        f"{len(self_loops)} self-loops among the CONCLUSIVE_NO, expected "
        f"{CONCLUSIVE_NO_SELF_LOOPS}. The gloss 'searched; there is no cycle' is only honest for "
        f"the ones that were actually SEARCHED."
    )
    assert len(closed) == CONCLUSIVE_NO_CLOSED_SEARCHES
    assert len(self_loops) + len(closed) == CENSUS_N["CONCLUSIVE_NO"]

    # The self-loop is 96.5% of CONCLUSIVE_NO — the MAJORITY case, not an edge case.
    assert len(self_loops) / len(conclusive) > 0.96

    # AND THE WIRE ALREADY TELLS THEM APART. This is why the fourth state needs no schema change:
    # `detail` is served on every witness by GET /aml/transactions/{id}/interrogate.
    for d in self_loops:
        assert check(graph, graph.by_id[d.txn_id], "CYCLE").detail == DETAIL_SELF_LOOP
    for d in closed:
        assert check(graph, graph.by_id[d.txn_id], "CYCLE").detail == DETAIL_CLOSED

    # The PERSISTED tag stays three-way, and must. Self-loop-vs-closed-search is a property of the
    # EVIDENCE, re-derived from the graph — not of what the agent RECORDED. Serving it on the
    # decision surface would be the decision layer re-deriving a fact about the evidence layer: the
    # same conflation G5 refused when it declined to re-run the witness for `witness_outcome`. The
    # fourth state belongs to /interrogate, and only there.
    assert len(TXN_REF_TAGS) == 3


def test_the_conclusive_no_decomposition_reaches_the_openapi_schema():
    """A caller who only ever sees /openapi.json must not be told that 463 searches happened.

    The sibling of test_the_disclosure_reaches_the_openapi_schema, for the same audience and the
    same reason: the DTO docstring IS the read surface's disclosure, and it was the surface carrying
    the false gloss furthest — straight into the machine-readable schema.
    """
    schema = app.openapi()["components"]["schemas"]["DecisionOut"]
    blurb = schema.get("description", "")
    assert str(CONCLUSIVE_NO_SELF_LOOPS) in blurb, "the 447 self-loops must be disclosed"
    assert str(CONCLUSIVE_NO_CLOSED_SEARCHES) in blurb, "the 16 real closed searches must be named"
    assert "self-loop" in blurb


# The surfaces a reader meets. NOTES.md is deliberately NOT swept: it is an append-only engineering
# log whose historical entries quote the old, wrong gloss ON PURPOSE, and rewriting history to
# satisfy a grep would be the actual dishonesty. Same exclusion, same reason, as
# tests/test_restore_instructions.py.
_SWEPT = (
    sorted((Path(__file__).resolve().parents[1] / "app").rglob("*.py"))
    + sorted((Path(__file__).resolve().parents[1] / "seed").rglob("*.py"))
    + sorted((Path(__file__).resolve().parents[1] / "scripts").rglob("*.py"))
    + sorted((Path(__file__).resolve().parents[1] / "frontend" / "src").rglob("*.ts"))
    + sorted((Path(__file__).resolve().parents[1] / "frontend" / "src").rglob("*.tsx"))
    + [Path(__file__).resolve().parents[1] / d for d in ("README.md", "DEMO.md", "ARCHITECTURE.md")]
)

_SUBJECT = re.compile(r"CONCLUSIVE_NO|\b463\b")
# THE FALSEHOOD IS THE CLAIM THAT A SEARCH RAN — and nothing else. "there is no cycle" is TRUE of
# all 463 (self-loops cannot form one either), so matching that phrase would flag the corrected
# label as if it were the bug. The first draft of this guard did exactly that, and getting it wrong
# is what showed what the gloss actually was: not "no cycle", but "SEARCHED; no cycle".
_GLOSS = re.compile(r"\bsearch(ed|es|ing)?\b", re.I)
_DISCLOSED = re.compile(r"self-?loop", re.I)
# Code, not prose. A cycle-selection loop or a witness constructor mentions these tokens
# incidentally and asserts nothing to a reader. Prose is what lies.
_CODE = re.compile(r"^\s*(def |return |for |if |elif |else|\w+ = |\)|\()")


def _paragraphs(path: Path):
    """A paragraph is a run of consecutive non-blank lines, comment/quote markers stripped."""
    buf, start = [], 0
    for i, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        line = re.sub(r'^\s*(#|//|\*|>|"""|\'\'\')?\s?', "", raw)
        if line.strip():
            if not buf:
                start, first = i, raw
            buf.append(line)
        elif buf:
            yield start, first, " ".join(buf)
            buf = []
    if buf:
        yield start, first, " ".join(buf)


def test_no_surface_describes_conclusive_no_as_463_searches():
    """THE SAME-BREATH RULE, applied to the gloss. A paragraph that calls CONCLUSIVE_NO a search
    must name the self-loops IN THAT PARAGRAPH — because 447 of the 463 never were one.

    THE UNIT IS THE PARAGRAPH, AND THAT CHOICE IS THE WHOLE GUARD:
      * a SENTENCE is too strict — it splits legitimate multi-sentence corrections;
      * a FILE is THEATRE, and provably so: file-level containment would have PASSED the original
        `aml_graph.py`, which glossed CONCLUSIVE_NO as a search at line 21 while naming self-loops
        at line 31, ten lines away. A guard that cannot catch the bug it was written for is
        decoration — this project has now caught that in itself three times (the 14-line proximity
        window that passed its own bug; the guard that EXPLAINed a query the app never runs; and
        `test_citations.py`, whose own docstring contained the disease it was written to cure).
    So the invariant is the one the restore-instruction guard arrived at the hard way: the
    disclosure must travel IN THE SAME BREATH as the claim, not merely somewhere in the building.

    MADE TO TRIP: reverting any of the ten corrected sites fails here, naming the file and line.
    """
    violations: list[str] = []
    for f in _SWEPT:
        if "__pycache__" in f.parts or not f.exists():
            continue
        for line_no, first, prose in _paragraphs(f):
            if _CODE.match(first):
                continue
            if _SUBJECT.search(prose) and _GLOSS.search(prose) and not _DISCLOSED.search(prose):
                rel = f.relative_to(Path(__file__).resolve().parents[1]).as_posix()
                violations.append(f"{rel}:{line_no}  {prose.strip()[:110]}")

    assert not violations, (
        "A SURFACE DESCRIBES `CONCLUSIVE_NO` AS A SEARCH THAT RAN, WITHOUT NAMING THE SELF-LOOPS.\n"
        f"Only {CONCLUSIVE_NO_CLOSED_SEARCHES} of the 463 were searched; "
        f"{CONCLUSIVE_NO_SELF_LOOPS} are self-loops, where no search was ever possible.\n  "
        + "\n  ".join(violations)
        + "\n\nName the self-loops in the SAME paragraph, or do not call it a search."
    )


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


def test_the_census_is_COUNTABLE_through_the_api_not_merely_readable():
    """THE 65.3% AS SEVEN `total`s — which is what lets the honesty ledger read it LIVE.

    This is the difference that matters. A number a surface can COUNT from the cluster cannot be
    wrong about the cluster. A number a surface RETYPES from prose can be wrong in both available
    ways — and this exact census has been wrong in both: misstated once (the phantom "728 / 48.5%")
    and falsely sourced once (a `verify_seam` script that never existed). So the ledger no longer
    quotes it; it counts it.

    Seven calls at limit=1, reading only `total`, against the controlled world seeded here.
    """

    async def _run():
        await _seed_controlled_world()  # 3 AML decisions: one MATCH (fraud), one of each other

        async def total(**q) -> int:
            qs = "&".join(f"{k}={v}" for k, v in q.items())
            async with _client() as c:
                r = await c.get(f"/decisions?limit=1&{qs}")
            assert r.status_code == 200, r.text
            return r.json()["total"]

        assert await total(kind="aml") == 3
        for outcome in ("MATCH", "CONCLUSIVE_NO", "INCONCLUSIVE"):
            assert await total(kind="aml", witness_outcome=outcome) == 1, outcome

        # is_fraud is an AUDIT fact and it composes: only the MATCH row is labelled laundering here.
        assert await total(kind="aml", witness_outcome="MATCH", is_fraud="true") == 1
        assert await total(kind="aml", witness_outcome="CONCLUSIVE_NO", is_fraud="true") == 0
        assert await total(kind="aml", witness_outcome="INCONCLUSIVE", is_fraud="true") == 0

        # The filter matches the PERSISTED basis tag, so it can never disagree with the field.
        async with _client() as c:
            rows = (await c.get("/decisions?kind=aml&witness_outcome=INCONCLUSIVE")).json()
        assert [d["witness_outcome"] for d in rows["decisions"]] == ["INCONCLUSIVE"]
        assert rows["witness_outcome"] == "INCONCLUSIVE"  # echoed back

    asyncio.run(_run())


def test_an_unknown_kind_or_outcome_is_422_never_a_silent_empty_page():
    """A typo'd filter must not quietly return zero rows as if that were the answer.

    Especially `witness_outcome`: a silent empty page here would read as "there are no INCONCLUSIVE
    decisions" — i.e. it would silently REFUTE the disclosure this whole surface exists to carry.
    """

    async def _run():
        async with _client() as c:
            bad_kind = await c.get("/decisions?kind=laundering")
            bad_outcome = await c.get("/decisions?witness_outcome=INSUFFICIENT_COVERAGE")
            lowercase = await c.get("/decisions?witness_outcome=inconclusive")
        for r in (bad_kind, bad_outcome, lowercase):
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
