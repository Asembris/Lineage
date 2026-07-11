"""Provenance-integrity audit of a belief's inheritance closure (roadmap Item A).

WHAT THIS IS — AND, HONESTLY, WHAT IT IS NOT
--------------------------------------------
This is a READ-ONLY, DETERMINISTIC verifier that walks a belief's real
`belief_inheritance` closure and proves every edge was created legitimately — i.e.
by a genuine spawn event, from an ancestor that actually held the belief, before any
invalidation. It is NOT a patch for an open write-path hole, because there is no such
hole. The investigation for this item established (and this docstring restates so a
later reader is not misled) that the ONLY two code paths that ever INSERT a
`belief_inheritance` row are `seed.seed()` and `lifecycle.spawn_child()`, that
`spawn_child()` is not even exposed by any HTTP route, and that both writers maintain
the invariants below by construction. So through the application, an illegitimate edge
cannot arise.

The threat this defends against is therefore OUT-OF-BAND tampering — an edge inserted
by something other than the legitimate spawn lifecycle: a direct SQL write by an actor
with cluster credentials, a future write path that does not preserve the invariants, a
buggy migration, or a future multi-belief / multi-writer world. Such an edge is the
"clean-label" analog: every FK is real (real belief_id, real from/to agents, a
plausible `inherited_at`, a real revocation state) so it passes every existing
structural and referential check, and its illegitimacy is only visible by walking the
provenance chain and testing the invariants. This audit is that walk.

Framing note: this maps onto the recognised threat of *agent memory-store poisoning*
(the belief/inheritance graph is the fleet's inherited long-term memory). Specific
security-taxonomy identifiers (OWASP GenAI / MITRE ATLAS) are deliberately NOT cited in
this file pending explicit approval of the exact verified IDs — cite correctly or not
at all.

THE LEGITIMACY INVARIANTS (every real edge satisfies all four)
--------------------------------------------------------------
For an edge E = (belief_id, from_agent_id, to_agent_id, inherited_at, invalidated_at):

  A1  genealogy-consistency:   E.from_agent_id == to_agent.parent_id
        Every legitimate edge rides a real parent->child spawn link. A violation is a
        "phantom ancestor": provenance grafted onto a lineage the node never descended
        from. (Both writers set the child's parent_id to exactly the edge's from_agent.)

  A2  spawn-time consistency:  E.inherited_at == to_agent.spawned_at
        Inheritance happens AT the spawn instant, never out-of-band. A violation is an
        edge inserted "after the fact" whose timestamp corresponds to no spawn event.
        (seed sets inherited_at = child.spawned_at; spawn_child sets both to the same
        `now`.)

  A3  source-was-a-holder:     at E.inherited_at, from_agent genuinely held the belief
        from_agent must be the belief's originator, or hold an earlier inbound edge for
        this belief (inherited_at <= E.inherited_at). You cannot inherit from an ancestor
        that never held it. (This also subsumes "the belief existed": the originator only
        holds it from formed_at onward.)

  A4  not-post-invalidation:   E.inherited_at precedes any invalidation it depends on
        If the belief was invalidated at T (or the source edge was revoked at T) and
        E.inherited_at > T, the edge inherited a belief/holding that was already dead —
        this is exactly the roadmap's "provenance traces to a later-invalidated source".
        NOTE: a legitimate closure that was later invalidated is NOT flagged: every real
        edge's inherited_at (an old spawn time) precedes the single invalidation commit.

OUTCOME — a three-way verdict that never silently passes (mirrors the AML brake):
  CLEAN         every edge satisfies A1..A4.
  ANOMALOUS     >= 1 edge violates >= 1 invariant (each reported with codes + evidence).
  INCONCLUSIVE  an edge's backing data is missing/insufficient to decide (e.g. a
                referenced agent row is absent) — surfaced, never treated as clean.

No new table, no persisted state, no AML read, no LLM call. Pure read over
agents/beliefs/belief_inheritance. `engine` is injectable (defaults to the app global,
defaultdb) so the isolated attack test can run the SAME audit against the `demo`
database without touching the console's real closure.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db import engine as _DEFAULT_ENGINE

# Overall verdicts.
CLEAN = "CLEAN"
ANOMALOUS = "ANOMALOUS"
INCONCLUSIVE = "INCONCLUSIVE"

# Per-edge verdicts.
OK = "ok"
EDGE_ANOMALOUS = "anomalous"
EDGE_INCONCLUSIVE = "inconclusive"

# Invariant codes (kept short + stable; the human wording lives alongside each check).
A1 = "A1"  # genealogy-consistency: from_agent_id == to_agent.parent_id
A2 = "A2"  # spawn-time consistency: inherited_at == to_agent.spawned_at
A3 = "A3"  # source-was-a-holder at inherited_at
A4 = "A4"  # not-post-invalidation (later-invalidated source)

INVARIANT_DESCRIPTIONS = {
    A1: "edge from_agent is not the heir's parent (phantom ancestor)",
    A2: "inherited_at does not match the heir's spawn time (out-of-band edge)",
    A3: "source did not hold the belief at inherited_at (never a holder)",
    A4: "inherited_at is after an invalidation it depends on (later-invalidated source)",
}

_BELIEF_SQL = text(
    "SELECT id, rule_text, status, originating_agent_id, formed_at, invalidated_at "
    "FROM beliefs WHERE id = :id"
)

_EDGES_SQL = text(
    "SELECT id, belief_id, from_agent_id, to_agent_id, inherited_at, invalidated_at "
    "FROM belief_inheritance WHERE belief_id = :b"
)

_AGENTS_SQL = text(
    "SELECT id, parent_id, generation, bloodline, status, spawned_at, retired_at "
    "FROM agents WHERE id IN :ids"
).bindparams(bindparam("ids", expanding=True))


def _held_by_at(
    agent_id: uuid.UUID,
    at: dt.datetime,
    originator: uuid.UUID,
    edges: list[Mapping],
) -> bool:
    """Did `agent_id` hold the belief at instant `at` (A3, revocation-agnostic)?

    True iff it is the originator, or it has an inbound edge for this belief acquired at
    or before `at`. Revocation is deliberately NOT considered here — that is A4's job, so
    the two invariants stay orthogonal and a post-invalidation edge trips A4 cleanly
    rather than being masked as an A3 failure.
    """
    if agent_id == originator:
        return True
    for e in edges:
        if e["to_agent_id"] == agent_id and e["inherited_at"] <= at:
            return True
    return False


def classify_closure(
    belief: Mapping,
    edges: list[Mapping],
    agents: dict[uuid.UUID, Mapping],
) -> dict:
    """Pure A1..A4 classifier over one belief's closure. No I/O — unit-testable alone.

    `belief` carries originating_agent_id / formed_at / invalidated_at. `edges` is every
    `belief_inheritance` row for the belief. `agents` maps agent_id -> its row (parent_id,
    spawned_at, ...). Returns the audit dict (see module docstring for the shape).
    """
    originator = belief["originating_agent_id"]
    belief_invalidated_at = belief["invalidated_at"]

    edge_reports: list[dict] = []
    any_anomalous = False
    any_inconclusive = False

    for e in edges:
        to_id = e["to_agent_id"]
        from_id = e["from_agent_id"]
        inherited_at = e["inherited_at"]
        to_agent = agents.get(to_id)
        from_agent = agents.get(from_id)

        violated: list[str] = []
        evidence: dict = {}

        # INCONCLUSIVE: a referenced agent row is missing, or the heir has no spawn time.
        # Cannot decide A1/A2 without them — surface it, never call it clean.
        if to_agent is None or from_agent is None or to_agent["spawned_at"] is None:
            edge_reports.append(
                {
                    "edge_id": str(e["id"]),
                    "from_agent_id": str(from_id),
                    "to_agent_id": str(to_id),
                    "verdict": EDGE_INCONCLUSIVE,
                    "violated": [],
                    "evidence": {
                        "reason": "referenced agent row missing or heir has no spawn time",
                        "to_agent_present": to_agent is not None,
                        "from_agent_present": from_agent is not None,
                    },
                }
            )
            any_inconclusive = True
            continue

        # A1 — the edge must ride a real parent->child spawn link.
        if from_id != to_agent["parent_id"]:
            violated.append(A1)
            evidence["A1"] = {
                "expected_parent": (
                    str(to_agent["parent_id"]) if to_agent["parent_id"] else None
                ),
                "edge_from_agent": str(from_id),
            }

        # A2 — inheritance must land exactly at the heir's spawn instant.
        if inherited_at != to_agent["spawned_at"]:
            violated.append(A2)
            evidence["A2"] = {
                "inherited_at": inherited_at.isoformat(),
                "heir_spawned_at": to_agent["spawned_at"].isoformat(),
            }

        # A3 — the source must actually have held the belief at inherited_at.
        if not _held_by_at(from_id, inherited_at, originator, edges):
            violated.append(A3)
            evidence["A3"] = {
                "from_agent": str(from_id),
                "inherited_at": inherited_at.isoformat(),
                "note": "source is neither the originator nor an earlier holder",
            }

        # A4 — inherited_at must precede any invalidation the edge depends on: the
        # belief's own invalidation, and the revocation of the source's inbound edge.
        a4_evidence: dict = {}
        if belief_invalidated_at is not None and inherited_at > belief_invalidated_at:
            a4_evidence["belief_invalidated_at"] = belief_invalidated_at.isoformat()
        for src in edges:
            if (
                src["to_agent_id"] == from_id
                and src["invalidated_at"] is not None
                and inherited_at > src["invalidated_at"]
            ):
                a4_evidence["source_edge_revoked_at"] = src["invalidated_at"].isoformat()
                break
        if a4_evidence:
            violated.append(A4)
            a4_evidence["inherited_at"] = inherited_at.isoformat()
            evidence["A4"] = a4_evidence

        verdict = EDGE_ANOMALOUS if violated else OK
        if violated:
            any_anomalous = True
        edge_reports.append(
            {
                "edge_id": str(e["id"]),
                "from_agent_id": str(from_id),
                "to_agent_id": str(to_id),
                "verdict": verdict,
                "violated": violated,
                "evidence": evidence,
            }
        )

    if any_anomalous:
        status = ANOMALOUS
    elif any_inconclusive:
        status = INCONCLUSIVE
    else:
        status = CLEAN

    anomalies = [r for r in edge_reports if r["verdict"] != OK]
    return {
        "belief_id": str(belief["id"]),
        "belief_status": belief["status"],
        "origin_agent_id": str(originator),
        "status": status,
        "edge_count": len(edges),
        "anomaly_count": len(anomalies),
        "edges": edge_reports,
        "anomalies": anomalies,
    }


async def audit_inheritance(
    belief_id: uuid.UUID,
    engine: AsyncEngine | None = None,
) -> dict | None:
    """Audit `belief_id`'s inheritance closure for provenance anomalies (A1..A4).

    Read-only. Returns the classifier dict, or None if the belief does not exist. The
    belief row, its edges, and every referenced agent are read in ONE explicit
    transaction, so all three see the same MVCC snapshot (no torn read between the edge
    set and the agents it references). `engine` defaults to the app global (defaultdb);
    the isolated attack test injects the demo engine.
    """
    eng = engine if engine is not None else _DEFAULT_ENGINE
    async with eng.connect() as conn:
        async with conn.begin():
            belief = (
                await conn.execute(_BELIEF_SQL, {"id": belief_id})
            ).mappings().first()
            if belief is None:
                return None
            edges = [
                dict(r)
                for r in (
                    await conn.execute(_EDGES_SQL, {"b": belief_id})
                ).mappings().all()
            ]
            # Every agent the closure references: heirs, sources, and the originator.
            ids: set[uuid.UUID] = {belief["originating_agent_id"]}
            for e in edges:
                ids.add(e["from_agent_id"])
                ids.add(e["to_agent_id"])
            agent_rows = (
                await conn.execute(_AGENTS_SQL, {"ids": list(ids)})
            ).mappings().all()

    agents = {r["id"]: dict(r) for r in agent_rows}
    return classify_closure(belief, edges, agents)
