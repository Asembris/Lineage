"""THE Phase-1 done-test: prove AS OF SYSTEM TIME is real MVCC time-travel.

Design (per review): capture t0, THEN commit a write, THEN re-query as_of=t0 and assert it
STILL returns the pre-write state. A plain (non-AOST) read would already see the committed
write, so the re-query-after-commit is the actual proof. A separate positive assertion
confirms the SET TRANSACTION AS OF SYSTEM TIME actually engaged (so we can't pass green for
the wrong reason via an autocommit boundary swallowing the SET).
"""

import asyncio
import uuid

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.db import engine
from app.main import app
from seed.seed import aid, bid, seed as run_seed

CRIMSON7 = str(aid("crimson-7"))


def test_aost_hides_a_committed_write():
    async def _run():
        try:
            await run_seed()  # deterministic baseline
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                # 1) capture t0 (HLC) BEFORE any mutation
                async with engine.connect() as c:
                    t0 = str(
                        (await c.execute(text("SELECT cluster_logical_timestamp()"))).scalar()
                    )

                # baseline deposition at t0
                r0 = await client.get(f"/agents/{CRIMSON7}/beliefs", params={"as_of": t0})
                assert r0.status_code == 200, r0.text
                n = len(r0.json()["beliefs"])
                assert n >= 1  # crimson-7 holds the inherited founding belief

                # 2) COMMIT a real write: crimson-7 inherits a SECOND belief
                new_belief = uuid.uuid4()
                async with engine.begin() as c:
                    await c.execute(
                        text(
                            "INSERT INTO beliefs (id, rule_text, originating_agent_id, "
                            "formed_at, status) VALUES (:id, :rt, :orig, now(), 'active')"
                        ),
                        {"id": new_belief, "rt": "TEST second belief", "orig": aid("crimson-6")},
                    )
                    await c.execute(
                        text(
                            "INSERT INTO belief_inheritance (belief_id, from_agent_id, "
                            "to_agent_id, inherited_at) VALUES (:b, :f, :t, now())"
                        ),
                        {"b": new_belief, "f": aid("crimson-6"), "t": aid("crimson-7")},
                    )

                # 3) RE-QUERY at t0 AFTER the commit -> must STILL be n (write is hidden)
                r_past = await client.get(f"/agents/{CRIMSON7}/beliefs", params={"as_of": t0})
                assert r_past.status_code == 200, r_past.text
                past_ids = {b["id"] for b in r_past.json()["beliefs"]}
                assert len(past_ids) == n, (
                    "AOST read at t0 saw a write committed AFTER t0 — time-travel is NOT real"
                )
                assert str(new_belief) not in past_ids

                # 4) query NOW -> must reflect the write (n+1)
                r_now = await client.get(f"/agents/{CRIMSON7}/beliefs")
                now_ids = {b["id"] for b in r_now.json()["beliefs"]}
                assert len(now_ids) == n + 1
                assert str(new_belief) in now_ids

                # 5) POSITIVE proof the SET TRANSACTION AS OF SYSTEM TIME engaged:
                #    inside the AOST txn the read timestamp == t0 exactly; a fresh read differs.
                async with engine.connect() as c:
                    async with c.begin():
                        await c.execute(text(f"SET TRANSACTION AS OF SYSTEM TIME {t0}"))
                        aost_ts = str(
                            (await c.execute(text("SELECT cluster_logical_timestamp()"))).scalar()
                        )
                    fresh_ts = str(
                        (await c.execute(text("SELECT cluster_logical_timestamp()"))).scalar()
                    )
                assert aost_ts == t0, "SET TRANSACTION AS OF SYSTEM TIME did not engage"
                assert fresh_ts != t0, "control read should be at a later timestamp"
        finally:
            await engine.dispose()

    asyncio.run(_run())
