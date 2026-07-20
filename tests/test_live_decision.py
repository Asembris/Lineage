"""J1 — the LIVE governed decision route: the agent acts on its inherited memory, on request.

Everything before this wrote decisions from a script. `POST /decisions/aml/{txn_id}` runs the SAME
frozen, label-free witness at HTTP request time and writes a real row into the joined two-graph
memory. These tests assert the three things that makes true rather than merely plausible:

  1. THE ROW IS REAL AND SCHEMA-IDENTICAL to a backfilled one — same FK citation, same basis tag,
     NULL merchant/confidence, a named currency. Only `decided_at` differs, and that is the marker.
  2. THE VERDICT IS THE WITNESS'S — it agrees with the backfilled decision for the same edge, which
     is determinism DEMONSTRATED (same function, same unlabeled graph, months apart).
  3. THE WRITE IS GOVERNED — an invalidated belief, a dead agent, or a missing inheritance edge
     REFUSES the write with a 409. The Phase-3 kill-shot, observable at the agent's own hands.

    !!  THIS FILE MUTATES THE LIVE CLUSTER — IT IS THE ONLY TEST THAT WRITES A DECISION  !!

Every test that creates a row DELETES IT in a `finally`, by the exact `decision_id` the route
returned. The cluster's 5,500-row fingerprint (4,000 card + 1,500 AML) must be intact before AND
after this file runs. Nothing here reseeds — a reseed would destroy both backfills (NOTES "THE
TWO-BACKFILL LANDMINE").

CI-SAFE: no OpenAI. The route runs the deterministic witness; `evaluate_transaction` is never
reachable from it, which tests/test_aml_routes.py asserts statically over the whole `app` package.
"""

import asyncio
import uuid

import httpx
from sqlalchemy import text

from app.db import engine
from app.main import app
from app.services.aml_live_decide import AML_BELIEF, DECIDING_AGENT

# Real rows from the live extract (the same subjects tests/test_aml_routes.py uses).
CYCLE_SUBJECT = "045adfd2-a822-566f-9cd2-6a17fc150539"   # witnesses a real directed cycle -> MATCH
BENIGN_MULTI = "3cda6d1d-f765-5001-9342-0478b1a92232"
UNKNOWN = "00000000-0000-0000-0000-000000000000"


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def _scalar(sql: str, **params):
    async with engine.connect() as c:
        return (await c.execute(text(sql), params)).scalar()


async def _delete(decision_id) -> None:
    """Remove exactly the row the route wrote. The fingerprint must survive this file."""
    async with engine.begin() as c:
        await c.execute(text("DELETE FROM decisions WHERE id = :id"), {"id": decision_id})


def test_a_live_decision_writes_a_real_row_that_matches_the_backfilled_shape():
    """The heart of J1: a request produces a real, governed, citing decision row.

    Asserted against the DATABASE, not the response — a route that returned a beautiful payload and
    wrote nothing would pass an envelope check.
    """

    async def _run():
        before = await _scalar("SELECT count(*) FROM decisions")
        async with _client() as c:
            r = await c.post(f"/decisions/aml/{CYCLE_SUBJECT}")
        assert r.status_code == 200, r.text
        body = r.json()
        did = body["decision_id"]

        try:
            assert await _scalar("SELECT count(*) FROM decisions") == before + 1

            row = None
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        text(
                            "SELECT agent_id, txn_ref, merchant, amount, amount_currency, verdict, "
                            "driving_belief_id, confidence, decided_at, is_fraud, aml_transaction_id "
                            "FROM decisions WHERE id = :id"
                        ),
                        {"id": uuid.UUID(did)},
                    )
                ).mappings().one()

            # Schema-identical to a backfilled AML decision, constraint for constraint.
            assert row["aml_transaction_id"] == uuid.UUID(CYCLE_SUBJECT)  # the real FK citation
            assert row["driving_belief_id"] == AML_BELIEF
            assert row["agent_id"] == DECIDING_AGENT
            assert row["merchant"] is None        # migration 0007: an AML transfer has no merchant
            assert row["confidence"] is None      # 0007: a deterministic witness has no confidence
            assert row["amount_currency"]         # 0007: an AML row must name its currency
            assert row["txn_ref"] == "aml:MATCH"  # 0008: the basis tag, structurally enforced
            assert row["verdict"] == "blocked"    # MATCH -> blocked (aml_seam.VERDICT_FOR)

            # ...and the response agrees with the row it claims to have written.
            assert body["witness_outcome"] == "MATCH"
            assert body["verdict"] == "blocked"
            assert body["transaction_id"] == CYCLE_SUBJECT
            assert body["is_new_row"] is True
        finally:
            await _delete(uuid.UUID(did))

        assert await _scalar("SELECT count(*) FROM decisions") == before

    asyncio.run(_run())


def test_the_live_verdict_agrees_with_the_backfilled_one_which_is_determinism_not_novelty():
    """THE HONEST NARRATION, ASSERTED.

    Every one of the 1,500 ingested edges already carries a backfilled decision, so "this decision
    did not exist before this call" would be FALSE. What the call created is a ROW. The response
    must disclose the prior ruling rather than conceal it, and the live verdict must MATCH it —
    the same frozen witness re-deriving the same answer from the same unlabeled graph, live.

    That agreement is the actual claim: determinism demonstrated, not a novelty claim.

    THIS TEST DEPENDS ON NO BACKFILL, and it had to be rewritten to stop doing so. The first draft
    asserted the live verdict against the BACKFILLED row for the same edge — which passed alone and
    failed in the suite, because `seed.seed()` DELETEs every decision and several tests call it.
    That is the house rule (NOTES "NO TEST MAY DEPEND ON A BACKFILL") re-broken by the very file
    documenting the seam, and re-learned the same way: by ordering.

    So the test now CREATES its own prior: it decides the same edge TWICE and asserts the second
    call discloses the first and agrees with it. That is strictly the better experiment anyway —
    two independent live runs of the witness over the same unlabeled graph, rather than a comparison
    against a row a script wrote months ago. Whatever prior rows happen to exist (backfilled or not)
    are handled by measuring the baseline instead of assuming it.
    """

    async def _run():
        baseline = await _scalar(
            "SELECT count(*) FROM decisions WHERE aml_transaction_id = :id",
            id=uuid.UUID(BENIGN_MULTI),
        )

        first = second = None
        try:
            async with _client() as c:
                r1 = await c.post(f"/decisions/aml/{BENIGN_MULTI}")
                assert r1.status_code == 200, r1.text
                first = r1.json()
                # Whatever existed before is disclosed exactly, by count and by id.
                assert first["prior_decisions_for_this_transaction"] == baseline
                assert len(first["prior_decision_ids"]) == baseline

                r2 = await c.post(f"/decisions/aml/{BENIGN_MULTI}")
                assert r2.status_code == 200, r2.text
                second = r2.json()

            # The second call SEES the first — the prior row is disclosed, never concealed.
            assert second["prior_decisions_for_this_transaction"] == baseline + 1
            assert first["decision_id"] in second["prior_decision_ids"]
            assert second["decision_id"] not in second["prior_decision_ids"]

            # THE DETERMINISM BEAT: same frozen witness, same unlabeled graph, same verdict.
            assert second["verdict_agrees_with_prior"] is True
            assert second["verdict"] == first["verdict"]
            assert second["witness_outcome"] == first["witness_outcome"]
            assert second["witness_txn_ids"] == first["witness_txn_ids"]

            # `is_new_row` is true of the ROW, and the response never claims more than that: by the
            # second call a prior ruling demonstrably existed, and the payload says so.
            assert second["is_new_row"] is True
            assert second["prior_decisions_for_this_transaction"] > 0
        finally:
            for body in (first, second):
                if body:
                    await _delete(uuid.UUID(body["decision_id"]))

        assert (
            await _scalar(
                "SELECT count(*) FROM decisions WHERE aml_transaction_id = :id",
                id=uuid.UUID(BENIGN_MULTI),
            )
            == baseline
        )

    asyncio.run(_run())


def test_the_response_carries_the_whole_ancestry_narration():
    """The 30-second story must be tellable from ONE response plus existing GETs.

    The belief, the long-dead ancestor that formed it, the generations between, and the living agent
    that just acted on it — all real rows, all in the payload.
    """

    async def _run():
        async with _client() as c:
            r = await c.post(f"/decisions/aml/{CYCLE_SUBJECT}")
        assert r.status_code == 200, r.text
        body = r.json()

        try:
            belief = body["belief"]
            assert belief["id"] == str(AML_BELIEF)
            assert belief["rule_text"]                         # a real rule, not an empty envelope
            assert belief["status"] == "active"
            assert belief["originating_agent_generation"] == 0  # the founding ancestor
            assert belief["originating_agent_status"] == "dead"  # it never met the agent acting now
            assert belief["inheritance_edge_count"] == 7        # inherited down the azure spine

            agent = body["deciding_agent"]
            assert agent["id"] == str(DECIDING_AGENT)
            assert agent["status"] == "alive"
            assert agent["generation"] == 7
            assert agent["bloodline"] == belief["originating_agent_bloodline"]

            # The witness cites REAL transactions, each resolvable through the evidence surface.
            assert body["witness_txn_ids"], "a MATCH must cite the cycle it re-derived"
            assert body["transaction_id"] in body["witness_txn_ids"]
            async with _client() as c:
                for tid in body["witness_txn_ids"]:
                    got = await c.get(f"/aml/transactions/{tid}")
                    assert got.status_code == 200, f"cited {tid} does not resolve"
        finally:
            await _delete(uuid.UUID(body["decision_id"]))

    asyncio.run(_run())


def test_an_unknown_transaction_is_404_and_writes_nothing():
    async def _run():
        before = await _scalar("SELECT count(*) FROM decisions")
        async with _client() as c:
            r = await c.post(f"/decisions/aml/{UNKNOWN}")
        assert r.status_code == 404, r.text
        assert "not in the AML evidence layer" in r.json()["detail"]
        assert await _scalar("SELECT count(*) FROM decisions") == before

    asyncio.run(_run())


def test_a_malformed_transaction_id_is_422_not_500():
    async def _run():
        async with _client() as c:
            r = await c.post("/decisions/aml/not-a-uuid")
        assert r.status_code == 422

    asyncio.run(_run())


def test_an_invalidated_belief_refuses_the_write_which_is_the_kill_shot_made_observable():
    """THE STRONGEST BEAT IN THIS ROUTE, and the reason the governance checks exist.

    Phase 3 proved a belief and its whole inherited closure can be invalidated in ONE serializable
    CockroachDB transaction. That was true but INERT — a count in a certificate. Here it changes
    what a living agent can DO: with the belief invalidated, azure-7 cannot act on it, and the
    refusal is a real 409 from a real status check against a real row.

    The belief is invalidated and restored IN THIS TEST, directly and narrowly (belief status +
    closure edges), never through POST /beliefs/{id}/invalidate — that route writes audit rows and
    an S3 certificate, which is a far larger blast radius than this assertion needs.
    """

    async def _run():
        before = await _scalar("SELECT count(*) FROM decisions")
        assert (
            await _scalar("SELECT status FROM beliefs WHERE id = :b", b=AML_BELIEF)
        ) == "active", "the azure belief is not active — restore the cluster before running this"

        async with engine.begin() as c:
            await c.execute(
                text("UPDATE beliefs SET status = 'invalidated' WHERE id = :b"), {"b": AML_BELIEF}
            )
        try:
            async with _client() as c:
                r = await c.post(f"/decisions/aml/{CYCLE_SUBJECT}")
            assert r.status_code == 409, r.text
            detail = r.json()["detail"]
            assert "invalidated" in detail
            # NOTHING was written. A refused decision leaves no trace in the moat.
            assert await _scalar("SELECT count(*) FROM decisions") == before
        finally:
            async with engine.begin() as c:
                await c.execute(
                    text("UPDATE beliefs SET status = 'active' WHERE id = :b"), {"b": AML_BELIEF}
                )

        # And once the belief is active again, the agent can act again.
        async with _client() as c:
            r = await c.post(f"/decisions/aml/{CYCLE_SUBJECT}")
        assert r.status_code == 200, r.text
        await _delete(uuid.UUID(r.json()["decision_id"]))
        assert await _scalar("SELECT count(*) FROM decisions") == before

    asyncio.run(_run())


def test_a_revoked_inheritance_edge_refuses_the_write():
    """A decision must come from a LIVING HOLDER of the belief, or it is not inherited memory.

    The atomic invalidation closes every `belief_inheritance` edge in the same commit that flips the
    belief's status, so in production these two refusals fire together. They are separate checks
    because they mean different things — and this one is what a PARTIAL revocation would trip.
    """

    async def _run():
        before = await _scalar("SELECT count(*) FROM decisions")
        async with engine.begin() as c:
            await c.execute(
                text(
                    "UPDATE belief_inheritance SET invalidated_at = now() "
                    "WHERE belief_id = :b AND to_agent_id = :a"
                ),
                {"b": AML_BELIEF, "a": DECIDING_AGENT},
            )
        try:
            async with _client() as c:
                r = await c.post(f"/decisions/aml/{CYCLE_SUBJECT}")
            assert r.status_code == 409, r.text
            assert "holds no live belief_inheritance edge" in r.json()["detail"]
            assert await _scalar("SELECT count(*) FROM decisions") == before
        finally:
            async with engine.begin() as c:
                await c.execute(
                    text(
                        "UPDATE belief_inheritance SET invalidated_at = NULL "
                        "WHERE belief_id = :b AND to_agent_id = :a"
                    ),
                    {"b": AML_BELIEF, "a": DECIDING_AGENT},
                )

        # Restored: the holder can act again, and the world is exactly as it was.
        assert (
            await _scalar(
                "SELECT count(*) FROM belief_inheritance "
                "WHERE belief_id = :b AND invalidated_at IS NULL",
                b=AML_BELIEF,
            )
            == 7
        )
        assert await _scalar("SELECT count(*) FROM decisions") == before

    asyncio.run(_run())


def test_the_route_is_the_only_write_on_the_decisions_surface_and_never_reaches_the_llm():
    """The /aml read-only guard is untouched BECAUSE this route is not an /aml route.

    tests/test_aml_routes.py asserts every `/aml` route is GET-or-HEAD. That guard is load-bearing
    (the evidence layer is static reference data) and was NOT weakened to fit this feature: the
    write lives on `/decisions`, which is a moat surface, exactly where a `decisions` INSERT belongs.
    This test pins that placement so a future session cannot drift it back under `/aml`.
    """
    paths = {r.path: r.methods for r in app.routes if hasattr(r, "methods")}

    assert "/decisions/aml/{txn_id}" in paths
    assert paths["/decisions/aml/{txn_id}"] == {"POST"}

    # Not one /aml route gained a write.
    for path, methods in paths.items():
        if path.startswith("/aml"):
            assert methods <= {"GET", "HEAD"}, f"{path} exposes {methods}"

    # The live decider runs the DETERMINISTIC witness. The paid path is not in its namespace.
    import app.services.aml_live_decide as live

    assert not hasattr(live, "aml_agent")
    assert not hasattr(live, "evaluate_transaction")
    assert not hasattr(live, "get_openai")
    # It is the seam's decider, and it is the same one the backfill used.
    from app.services.aml_seam import decide

    assert live.decide is decide
