"""Roadmap Item 2 — reversible-deterministic replay of a belief's inheritance closure.

Two proofs, mirroring the Phase-1 AOST done-test but lifted to the HASH level:

  1. BYTE-IDENTICAL + hides-a-committed-write: two independent reads at the same HLC produce
     an identical content_hash; then a closure-CHANGING write is committed and the replay at
     the OLD timestamp still hashes identically (MVCC time-travel hides it), while a
     current-state read reflects the grown closure with a different hash. If replay were
     non-deterministic (unstable ordering / non-canonical serialization) or not real
     time-travel, the hash equality would break.
  2. Out-of-window / malformed as_of -> 400 (never 500); unknown belief -> 404.
"""

import asyncio
import uuid

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.db import engine
from app.main import app
from seed.seed import aid, bid, seed as run_seed

ORIGIN = str(bid("origin"))


def test_replay_is_byte_identical_and_hides_a_committed_write():
    async def _run():
        try:
            await run_seed()  # deterministic baseline: 9-node closure (8 spine + 1 branch)
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                # capture t0 (HLC) BEFORE any mutation
                async with engine.connect() as c:
                    t0 = str(
                        (await c.execute(text("SELECT cluster_logical_timestamp()"))).scalar()
                    )

                # 1) two INDEPENDENT reads at t0 -> byte-identical (same world => same hash)
                r1 = await client.get(f"/beliefs/{ORIGIN}/replay", params={"as_of": t0})
                r2 = await client.get(f"/beliefs/{ORIGIN}/replay", params={"as_of": t0})
                assert r1.status_code == 200, r1.text
                assert r2.status_code == 200, r2.text
                h0 = r1.json()["content_hash"]
                assert h0.startswith("sha256:")
                assert r2.json()["content_hash"] == h0, (
                    "two reads at the same HLC are not byte-identical — replay is not deterministic"
                )
                assert len(r1.json()["closure"]) == 9, "8 spine + 1 branch expected"

                # 2) COMMIT a closure-CHANGING write: extend the origin belief's closure to
                #    crimson-2b (a real agent deliberately OUTSIDE the seeded closure), via a
                #    new inheritance edge from the in-closure crimson-2.
                async with engine.begin() as c:
                    await c.execute(
                        text(
                            "INSERT INTO belief_inheritance (belief_id, from_agent_id, "
                            "to_agent_id, inherited_at) VALUES (:b, :f, :t, now())"
                        ),
                        {"b": bid("origin"), "f": aid("crimson-2"), "t": aid("crimson-2b")},
                    )

                # 3) RE-READ at t0 AFTER the commit -> hash UNCHANGED (time-travel hides it)
                r_past = await client.get(f"/beliefs/{ORIGIN}/replay", params={"as_of": t0})
                assert r_past.status_code == 200, r_past.text
                assert r_past.json()["content_hash"] == h0, (
                    "AOST replay at t0 reflected a write committed AFTER t0 — replay is not "
                    "reproducible / not real time-travel"
                )
                assert len(r_past.json()["closure"]) == 9

                # 4) read NOW -> closure grew to 10, hash DIFFERS (the change is real)
                r_now = await client.get(f"/beliefs/{ORIGIN}/replay")
                assert r_now.status_code == 200, r_now.text
                assert len(r_now.json()["closure"]) == 10
                assert r_now.json()["content_hash"] != h0
                # the new node is crimson-2b, inheriting from crimson-2
                by_id = {n["agent_id"]: n for n in r_now.json()["closure"]}
                b2 = by_id[str(aid("crimson-2b"))]
                assert b2["from_agent_id"] == str(aid("crimson-2"))
                assert b2["edge_invalidated_at"] is None  # a live (open) edge
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_replay_rejects_out_of_window_and_unknown_belief():
    async def _run():
        try:
            await run_seed()
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                # older than the GC TTL (4500s) -> fails inside CRDB at SET TRANSACTION -> 400
                r_old = await client.get(
                    f"/beliefs/{ORIGIN}/replay",
                    params={"as_of": "2020-01-01T00:00:00+00:00"},
                )
                assert r_old.status_code == 400, r_old.text

                # malformed as_of -> 400 (normalize_as_of rejects it)
                r_bad = await client.get(
                    f"/beliefs/{ORIGIN}/replay", params={"as_of": "not-a-timestamp"}
                )
                assert r_bad.status_code == 400, r_bad.text

                # unknown belief (current-state) -> 404
                r_404 = await client.get(f"/beliefs/{uuid.uuid4()}/replay")
                assert r_404.status_code == 404, r_404.text
        finally:
            await engine.dispose()

    asyncio.run(_run())
