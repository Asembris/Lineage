"""Hermetic certificate tests — the self-contained, tamper-evident pre-kill record.

build_certificate is a pure function (no I/O), so these run with ZERO cluster/S3/AWS access.
They prove the AUDIT.md Part A integrity fix: the certificate body carries the measured
"belief active, whole closure open" fact (pre_invalidation_state), hash-covered, so the
document stands on its own even after the AOST snapshot ages past the GC TTL — AOST replay is
a bonus cross-check, not the sole integrity mechanism.
"""

import datetime as dt
import uuid

from app.services import certificate


def _fake_inv(with_pre_state: bool = True) -> dict:
    """An invalidation result shaped exactly like invalidation.invalidate_belief returns."""
    agents = [
        {"id": uuid.uuid4(), "generation": g, "bloodline": "crimson",
         "status": "alive" if g in (5, 7) else "dead"}
        for g in range(9)
    ]
    inv = {
        "audit_id": uuid.uuid4(),
        "belief": {
            "id": uuid.uuid4(),
            "rule_text": "mcc 5411 under $180 is safe if account age > 6 months",
            "originating_agent_id": agents[0]["id"],
            "formed_at": dt.datetime(2024, 5, 12, tzinfo=dt.timezone.utc),
            "status": "active",
        },
        "actor_id": uuid.uuid4(),
        "invalidated_at": dt.datetime(2026, 7, 3, tzinfo=dt.timezone.utc),
        "snapshot_hlc": "1751500000000000000.0000000000",
        "affected_agents": agents,
        "living_holders": [a for a in agents if a["status"] == "alive"],
        "affected_agent_count": len(agents),
        "affected_edge_count": 8,
    }
    if with_pre_state:
        inv["pre_state"] = {
            "belief_status": "active",
            "closure_edge_total": 8,
            "closure_edge_open": 8,
            "affected_agent_count": 9,
            "living_holder_count": 2,
            "snapshot_hlc": inv["snapshot_hlc"],
            "closure_content_hash": "sha256:" + "ab" * 32,
            "source": "issue-time-read",
        }
    return inv


def _fake_world() -> tuple[dict, list[dict]]:
    """A belief row + closure rows shaped exactly as replay.py's two SELECTs return them."""
    origin, heir = uuid.UUID(int=1), uuid.UUID(int=2)
    belief = {
        "id": uuid.UUID(int=9),
        "rule_text": "mcc 5411 under $180 is safe if account age > 6 months",
        "status": "active",
        "originating_agent_id": origin,
        "formed_at": dt.datetime(2024, 5, 12, tzinfo=dt.timezone.utc),
        "invalidated_at": None,
    }
    closure = [
        {"depth": 0, "agent_id": origin, "generation": 0, "bloodline": "crimson",
         "status": "dead", "from_agent_id": None, "inherited_at": None,
         "edge_invalidated_at": None},
        {"depth": 1, "agent_id": heir, "generation": 1, "bloodline": "crimson",
         "status": "alive", "from_agent_id": origin,
         "inherited_at": dt.datetime(2024, 6, 1, tzinfo=dt.timezone.utc),
         "edge_invalidated_at": None},
    ]
    return belief, closure


def test_certificate_embeds_self_contained_pre_kill_state():
    cert = certificate.build_certificate(_fake_inv(), {"available": False})
    pre = cert["pre_invalidation_state"]
    assert pre["belief_status"] == "active"
    # Whole closure open before the kill — recorded as a fact, not only replayable via AOST.
    assert pre["closure_edge_total"] == 8
    assert pre["closure_edge_open"] == 8
    assert pre["source"] == "issue-time-read"
    # The pre-kill record is covered by the content hash.
    assert certificate.verify(cert) is True


def test_tampering_the_pre_kill_record_breaks_the_hash():
    cert = certificate.build_certificate(_fake_inv(), {"available": False})
    assert certificate.verify(cert) is True
    # Forge the pre-kill claim (pretend an edge was already closed) — the hash must reject it.
    cert["pre_invalidation_state"]["closure_edge_open"] = 0
    assert certificate.verify(cert) is False


def test_pre_kill_state_is_derived_when_caller_omits_it():
    """Even a caller that doesn't supply pre_state gets a present, self-contained field."""
    cert = certificate.build_certificate(_fake_inv(with_pre_state=False), {"available": False})
    pre = cert["pre_invalidation_state"]
    assert pre["belief_status"] == "active"
    assert pre["closure_edge_total"] == 8 and pre["closure_edge_open"] == 8
    assert pre["source"] == "derived"
    # A derived pre_state content-addresses nothing, and says so rather than omitting the key.
    assert "closure_content_hash" in pre and pre["closure_content_hash"] is None
    assert certificate.verify(cert) is True


def test_tampering_the_closure_content_hash_breaks_the_certificate_hash():
    """The content-address of the pre-kill world is itself hash-covered (Item 6)."""
    cert = certificate.build_certificate(_fake_inv(), {"available": False})
    assert cert["pre_invalidation_state"]["closure_content_hash"].startswith("sha256:")
    cert["pre_invalidation_state"]["closure_content_hash"] = "sha256:" + "00" * 32
    assert certificate.verify(cert) is False


def test_closure_digest_is_stable_and_sensitive_to_every_covered_field():
    """The falsifiable half: equal worlds hash equal, and any real change moves the hash.

    This is what makes the Lambda's independent re-derivation a real check. If the digest were
    insensitive to (say) an edge's revocation state, the certifier could 'confirm' a pre-kill
    world in which the closure was already half-closed.
    """
    belief, closure = _fake_world()
    baseline = certificate.canonical_digest(certificate.closure_world(belief, closure))

    # Rebuilt from equal inputs => byte-identical. Dict insertion order must not matter.
    shuffled = [{k: r[k] for k in reversed(list(r))} for r in closure]
    assert certificate.canonical_digest(certificate.closure_world(dict(belief), shuffled)) == baseline

    # Each of these is a materially different pre-kill world.
    mutated_edge = [dict(closure[0]), {**closure[1], "edge_invalidated_at": belief["formed_at"]}]
    assert certificate.canonical_digest(certificate.closure_world(belief, mutated_edge)) != baseline

    dropped_holder = closure[:1]
    assert certificate.canonical_digest(certificate.closure_world(belief, dropped_holder)) != baseline

    killed_belief = {**belief, "status": "invalidated"}
    assert certificate.canonical_digest(certificate.closure_world(killed_belief, closure)) != baseline


def test_replay_and_the_certifier_share_one_canonicalizer():
    """The hash-equality check between app and Lambda must not compare two implementations.

    Item 2 kept replay.py's digest local; Item 6 reversed that. If someone reintroduces a local
    copy, the endpoint-vs-Lambda comparison silently degrades into 'do these two functions still
    agree', which is not the property anyone wants proven. See NOTES.md Item 6.
    """
    from app.services import replay

    assert replay.canonical_digest is certificate.canonical_digest
    assert replay.closure_world is certificate.closure_world
    assert not hasattr(replay, "_content_hash"), "replay.py re-grew a local canonicalizer"
