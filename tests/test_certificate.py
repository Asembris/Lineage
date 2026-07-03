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
            "source": "issue-time-read",
        }
    return inv


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
    assert certificate.verify(cert) is True
