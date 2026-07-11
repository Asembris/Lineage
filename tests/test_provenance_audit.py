"""Live done-tests for the inheritance-provenance audit (roadmap Item A).

Proves the A1..A4 audit (app/services/provenance_audit.py) returns CLEAN on the real
seeded closure and ANOMALOUS — flagging EXACTLY the offending edge with EXACTLY the
violated invariant, leaving every legitimate edge untouched — for three constructed
attacks:

  * A1  phantom ancestor        — edge whose from_agent is not the heir's parent.
  * A2  out-of-band inheritance — edge whose inherited_at matches no spawn event.
  * A4  later-invalidated source — edge grafted AFTER the belief was invalidated, so it
        inherits a belief that was already dead ("provenance traces to a later-
        invalidated source").

ISOLATION (the whole point, same discipline as test_demo_isolation): every write here
goes to the dedicated `demo` database via demo_engine / DemoSession. defaultdb's real
belief_inheritance — the closure the console reads — is NEVER written. The test
snapshots defaultdb before and after and asserts it is byte-identical, so constructing a
poisoned edge cannot pollute the console's data. NON-DESTRUCTIVE to defaultdb; no
backfill recovery needed.

The three attacks are inserted by DIRECT SQL that bypasses lifecycle.spawn_child() —
which is precisely the out-of-band vector the audit exists to catch. FK constraints in
`demo` still hold (real belief_id, real from/to agents), so each poisoned edge is
structurally valid and passes every referential check; only the provenance walk refutes
it. That is the "clean-label" property made concrete.
"""

import asyncio
import datetime as dt
import uuid

from sqlalchemy import text

from app.db import engine as app_engine
from app.demo_db import DemoSession, demo_engine, ensure_demo_ready
from app.services.provenance_audit import (
    ANOMALOUS,
    CLEAN,
    INCONCLUSIVE,
    audit_inheritance,
    classify_closure,
)
from seed.seed import aid, bid, days, seed as run_seed

ORIGIN = bid("origin")

# Test-only agent ids for the injected heirs (uuid5 in a distinct namespace so they are
# stable across runs and can never collide with the seed's `agent:<name>` ids).
_TESTNS = uuid.uuid5(uuid.NAMESPACE_DNS, "lineage.provenance-audit.test")


def _tid(name: str) -> uuid.UUID:
    return uuid.uuid5(_TESTNS, name)


async def _seed_demo() -> None:
    """Fresh, deterministic 9-edge crimson closure in the `demo` database."""
    await ensure_demo_ready()
    await run_seed(DemoSession)


async def _insert_agent(
    agent_id: uuid.UUID,
    *,
    parent_id: uuid.UUID | None,
    generation: int,
    spawned_at: dt.datetime,
) -> None:
    async with demo_engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO agents (id, generation, bloodline, status, spawned_at, "
                "retired_at, parent_id) VALUES (:id, :g, 'crimson', 'alive', :s, NULL, :p)"
            ),
            {"id": agent_id, "g": generation, "s": spawned_at, "p": parent_id},
        )


async def _insert_edge(
    edge_id: uuid.UUID,
    *,
    from_agent: uuid.UUID,
    to_agent: uuid.UUID,
    inherited_at: dt.datetime,
) -> None:
    async with demo_engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO belief_inheritance (id, belief_id, from_agent_id, "
                "to_agent_id, inherited_at) VALUES (:id, :b, :f, :t, :ia)"
            ),
            {"id": edge_id, "b": ORIGIN, "f": from_agent, "t": to_agent, "ia": inherited_at},
        )


async def _defaultdb_fingerprint() -> dict:
    """Everything a constructed attack could conceivably disturb in the console's db."""
    async with app_engine.connect() as c:
        return {
            "belief_status": (
                await c.execute(text("SELECT status FROM beliefs WHERE id=:b"), {"b": ORIGIN})
            ).scalar(),
            "agents": (await c.execute(text("SELECT count(*) FROM agents"))).scalar(),
            "edges": (
                await c.execute(
                    text("SELECT count(*) FROM belief_inheritance WHERE belief_id=:b"),
                    {"b": ORIGIN},
                )
            ).scalar(),
            "open_edges": (
                await c.execute(
                    text(
                        "SELECT count(*)-count(invalidated_at) FROM belief_inheritance "
                        "WHERE belief_id=:b"
                    ),
                    {"b": ORIGIN},
                )
            ).scalar(),
        }


def _by_edge_to(report: dict, to_agent: uuid.UUID) -> dict:
    return next(e for e in report["edges"] if e["to_agent_id"] == str(to_agent))


def test_audit_flags_constructed_attacks_and_leaves_defaultdb_untouched():
    async def _run():
        try:
            before = await _defaultdb_fingerprint()

            # --- baseline: the real seeded closure audits CLEAN -----------------------
            await _seed_demo()
            base = await audit_inheritance(ORIGIN, engine=demo_engine)
            assert base is not None
            assert base["status"] == CLEAN, base
            assert base["edge_count"] == 8, base  # 7 spine + 1 branch (9-node closure, origin has no inbound edge)
            assert base["anomaly_count"] == 0, base

            # --- Attack A1: phantom ancestor -----------------------------------------
            # New heir X descends from crimson-2 (parent_id), but the edge claims to
            # inherit from crimson-5. inherited_at == X.spawned_at (A2 ok) and crimson-5
            # genuinely held the belief by then (A3 ok), so ONLY A1 trips.
            await _seed_demo()
            x, x_edge = _tid("a1-heir"), _tid("a1-edge")
            await _insert_agent(x, parent_id=aid("crimson-2"), generation=3, spawned_at=days(250))
            await _insert_edge(x_edge, from_agent=aid("crimson-5"), to_agent=x, inherited_at=days(250))
            r = await audit_inheritance(ORIGIN, engine=demo_engine)
            assert r["status"] == ANOMALOUS, r
            assert r["anomaly_count"] == 1, r
            bad = _by_edge_to(r, x)
            assert bad["violated"] == ["A1"], bad
            assert bad["edge_id"] == str(x_edge)
            assert all(e["verdict"] == "ok" for e in r["edges"] if e["to_agent_id"] != str(x))

            # --- Attack A2: out-of-band inheritance time -----------------------------
            # New heir Y genuinely descends from crimson-6 (A1 ok), crimson-6 held the
            # belief (A3 ok), belief active (A4 ok) — but inherited_at (days 120) does
            # not equal Y.spawned_at (days 150), so ONLY A2 trips.
            await _seed_demo()
            y, y_edge = _tid("a2-heir"), _tid("a2-edge")
            await _insert_agent(y, parent_id=aid("crimson-6"), generation=7, spawned_at=days(150))
            await _insert_edge(y_edge, from_agent=aid("crimson-6"), to_agent=y, inherited_at=days(120))
            r = await audit_inheritance(ORIGIN, engine=demo_engine)
            assert r["status"] == ANOMALOUS, r
            assert r["anomaly_count"] == 1, r
            bad = _by_edge_to(r, y)
            assert bad["violated"] == ["A2"], bad
            assert all(e["verdict"] == "ok" for e in r["edges"] if e["to_agent_id"] != str(y))

            # --- Attack A4: later-invalidated source ---------------------------------
            # Invalidate the whole belief closure at T=days(50), THEN graft a fresh edge
            # (heir Z of the still-living crimson-7) with inherited_at days(10) — AFTER
            # the kill. A1/A2/A3 all hold; the edge inherits an already-dead belief, so
            # ONLY A4 trips. The 9 legitimate edges (all with old inherited_at < T) stay
            # clean despite being invalidated.
            await _seed_demo()
            async with demo_engine.begin() as c:
                await c.execute(
                    text(
                        "UPDATE beliefs SET status='invalidated', invalidated_at=:t WHERE id=:b"
                    ),
                    {"t": days(50), "b": ORIGIN},
                )
                await c.execute(
                    text(
                        "UPDATE belief_inheritance SET invalidated_at=:t WHERE belief_id=:b"
                    ),
                    {"t": days(50), "b": ORIGIN},
                )
            z, z_edge = _tid("a4-heir"), _tid("a4-edge")
            await _insert_agent(z, parent_id=aid("crimson-7"), generation=8, spawned_at=days(10))
            await _insert_edge(z_edge, from_agent=aid("crimson-7"), to_agent=z, inherited_at=days(10))
            r = await audit_inheritance(ORIGIN, engine=demo_engine)
            assert r["status"] == ANOMALOUS, r
            assert r["anomaly_count"] == 1, r
            bad = _by_edge_to(r, z)
            assert bad["violated"] == ["A4"], bad
            assert "belief_invalidated_at" in bad["evidence"]["A4"], bad
            assert all(e["verdict"] == "ok" for e in r["edges"] if e["to_agent_id"] != str(z))

            # --- isolation: defaultdb (the console's data) is byte-identical ----------
            after = await _defaultdb_fingerprint()
            assert after == before, (before, after)
        finally:
            await demo_engine.dispose()
            await app_engine.dispose()

    asyncio.run(_run())


def test_inconclusive_when_a_referenced_agent_is_missing():
    """The classifier surfaces INCONCLUSIVE (never a silent CLEAN) when an edge points at
    an agent row it cannot see. Pure — no cluster; FK constraints make this unreachable in
    the live db, so the honest defensive branch is proven at the classifier level."""
    origin = _tid("inc-origin")
    heir = _tid("inc-heir")
    belief = {
        "id": _tid("inc-belief"),
        "rule_text": "r",
        "status": "active",
        "originating_agent_id": origin,
        "formed_at": days(100),
        "invalidated_at": None,
    }
    edge = {
        "id": _tid("inc-edge"),
        "belief_id": belief["id"],
        "from_agent_id": origin,
        "to_agent_id": heir,          # heir row deliberately absent from `agents`
        "inherited_at": days(50),
        "invalidated_at": None,
    }
    # `agents` map is missing the heir row entirely.
    report = classify_closure(belief, [edge], {origin: {"id": origin, "parent_id": None,
                                                        "spawned_at": days(100), "generation": 0,
                                                        "bloodline": "crimson", "status": "dead",
                                                        "retired_at": None}})
    assert report["status"] == INCONCLUSIVE, report
    assert report["edges"][0]["verdict"] == "inconclusive", report
