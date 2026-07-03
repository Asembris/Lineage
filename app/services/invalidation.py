"""Atomic belief invalidation — the CockroachDB kill-shot (Phase 3).

invalidate_belief() closes a belief AND its full inherited closure in ONE serializable
CockroachDB transaction — every holder edge flipped at a single commit, not a loop of
per-holder updates (that loop is the eventually-consistent baseline we contrast against in
scripts/demo_consistency.py; it is deliberately NOT how the real endpoint works).

Mechanics (plan (a), same rigor as the AOST/staleness paths):

  0. On a SEPARATE read connection, snapshot cluster_logical_timestamp() BEFORE the write
     txn opens. HLC is monotonic, so this value is strictly earlier than the invalidation's
     commit timestamp — which is what lets the certificate PROVE, via AS OF SYSTEM TIME, that
     the belief was active immediately before the kill (a timestamp captured *inside* the
     write txn could equal the commit ts and make the "before" proof circular).
  1. SELECT ... FOR UPDATE the belief row — serializes concurrent invalidations of the same
     belief and gives idempotency. Not active -> AlreadyInvalidated (no-op, no certificate).
  2. Read the affected closure (originator ∪ every to_agent of this belief) for the audit
     record / certificate.
  3. UPDATE beliefs   SET status='invalidated', invalidated_at, invalidated_by  (guarded).
  4. UPDATE belief_inheritance SET invalidated_at, invalidated_by WHERE belief_id = X
     — EVERY edge, one set-based statement. This is "all holders at once".
  5. INSERT the audit_log row (atomic with the mutation: no invalidation without its record).
  6. Commit. Atomicity + snapshot isolation => no reader can ever observe a torn closure.

The S3 certificate is a post-commit side effect (see certificate.py / the endpoint): the DB
commit is the source of truth and is never gated on an external service.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import text

from app.db import SessionLocal, engine


class BeliefNotFound(Exception):
    """The belief id does not exist."""


class AlreadyInvalidated(Exception):
    """The belief is not 'active' — invalidation is a no-op (idempotent guard)."""


_CLOSURE_SQL = text(
    """
    SELECT a.id, a.generation, a.bloodline, a.status
    FROM agents a
    WHERE a.id = (SELECT originating_agent_id FROM beliefs WHERE id = :b)
       OR a.id IN (SELECT to_agent_id FROM belief_inheritance WHERE belief_id = :b)
    ORDER BY a.generation, a.id
    """
)


async def invalidate_belief(belief_id: uuid.UUID, actor_id: uuid.UUID) -> dict:
    """Invalidate `belief_id` and its full closure atomically. Returns the audit payload.

    Raises BeliefNotFound / AlreadyInvalidated. The returned dict carries everything the
    certificate builder needs (belief, actor, affected closure, the pre-invalidation
    snapshot HLC, and the audit_log id).
    """
    now = dt.datetime.now(dt.timezone.utc)

    # 0. Pre-invalidation snapshot, strictly before the write commit (see module docstring).
    async with engine.connect() as c:
        snapshot_hlc = (
            await c.execute(text("SELECT cluster_logical_timestamp()::string"))
        ).scalar_one()

    async with SessionLocal() as s:
        async with s.begin():  # single serializable CRDB transaction
            # 1. Lock + guard.
            belief = (
                await s.execute(
                    text(
                        "SELECT id, rule_text, status, originating_agent_id, formed_at "
                        "FROM beliefs WHERE id = :b FOR UPDATE"
                    ),
                    {"b": belief_id},
                )
            ).mappings().first()
            if belief is None:
                raise BeliefNotFound(str(belief_id))
            if belief["status"] != "active":
                raise AlreadyInvalidated(str(belief_id))

            # 2. Affected closure (for the audit record / certificate).
            closure = (
                await s.execute(_CLOSURE_SQL, {"b": belief_id})
            ).mappings().all()
            affected_agents = [dict(r) for r in closure]
            living = [a for a in affected_agents if a["status"] == "alive"]

            # 3. Flip the belief (guarded so a lost race is a no-op, not a double write).
            await s.execute(
                text(
                    "UPDATE beliefs SET status='invalidated', invalidated_at=:t, "
                    "invalidated_by=:a WHERE id=:b AND status='active'"
                ),
                {"t": now, "a": actor_id, "b": belief_id},
            )

            # 4. Flip EVERY closure edge at once — the "all holders at once" set-based update.
            edge_res = await s.execute(
                text(
                    "UPDATE belief_inheritance SET invalidated_at=:t, invalidated_by=:a "
                    "WHERE belief_id=:b AND invalidated_at IS NULL"
                ),
                {"t": now, "a": actor_id, "b": belief_id},
            )
            affected_edge_count = edge_res.rowcount

            # 5. Audit record, atomic with the mutation.
            audit_id = uuid.uuid4()
            await s.execute(
                text(
                    "INSERT INTO audit_log (id, action, actor, belief_id, "
                    "affected_agent_count, affected_edge_count, commit_hlc, "
                    "cert_status, created_at) VALUES (:id, 'belief_invalidation', :actor, "
                    ":belief, :na, :ne, :hlc, 'pending', :now)"
                ),
                {
                    "id": audit_id,
                    "actor": actor_id,
                    "belief": belief_id,
                    "na": len(affected_agents),
                    "ne": affected_edge_count,
                    "hlc": snapshot_hlc,
                    "now": now,
                },
            )
            # 6. Commit on exit of s.begin().

    return {
        "audit_id": audit_id,
        "belief": dict(belief),
        "actor_id": actor_id,
        "invalidated_at": now,
        "snapshot_hlc": snapshot_hlc,
        "affected_agents": affected_agents,
        "living_holders": living,
        "affected_agent_count": len(affected_agents),
        "affected_edge_count": affected_edge_count,
    }
