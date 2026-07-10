"""Hermetic tests for the certifier's closure-hash cross-check (Roadmap Item 6).

ZERO AWS, ZERO cluster: the Lambda handler is loaded by path with `certificate` shimmed onto
the app module it is packaged flat alongside, and boto3's S3 client is stubbed.

What is worth pinning here is NOT the happy path — it is that a MISSING counterparty reads as
'unavailable' and never as 'agreed'. The endpoint's certificate is a post-commit side effect
that can fail, and certificates written before schema 1.1 carry no closure hash at all. A
comparison that quietly succeeds when there was nothing to compare against would turn the
certifier's central claim ("I re-derived this myself and it matched") into a lie told by
omission — exactly the failure mode the brake's `INSUFFICIENT_COVERAGE` exists to prevent
elsewhere in this project.
"""

import importlib.util
import json
import sys
import uuid
from pathlib import Path

import pytest

from app.services import certificate

HANDLER_PATH = Path(__file__).resolve().parents[1] / "lambda" / "certifier" / "handler.py"

# The Lambda zip packs certificate.py flat next to the handler; in-repo it lives under
# app/services. Same module object either way — which is the point of the shared canonicalizer.
sys.modules.setdefault("certificate", certificate)
_spec = importlib.util.spec_from_file_location("certifier_handler", HANDLER_PATH)
handler = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(handler)


class _FakeS3:
    def __init__(self, body: bytes | None = None, raise_on_get: bool = False):
        self._body = body
        self._raise = raise_on_get

    def get_object(self, Bucket, Key):  # noqa: N803 — boto3's kwarg names
        if self._raise:
            raise RuntimeError("NoSuchKey")
        return {"Body": _Body(self._body)}


class _Body:
    def __init__(self, b):
        self._b = b

    def read(self):
        return self._b


def _cert_bytes(closure_hash, source="issue-time-read") -> bytes:
    pre = {"belief_status": "active", "source": source}
    if closure_hash is not None:
        pre["closure_content_hash"] = closure_hash
    return json.dumps({"pre_invalidation_state": pre}).encode()


@pytest.fixture
def s3(monkeypatch):
    holder = {}

    def _client(_name):
        return holder["client"]

    monkeypatch.setattr(handler.boto3, "client", _client)
    return holder


def test_a_written_certificate_supplies_the_hash_to_compare_against(s3):
    s3["client"] = _FakeS3(_cert_bytes("sha256:abc"))
    got, source = handler._issue_time_closure_hash("b", "certificates/x.json", "written")
    assert got == "sha256:abc"
    assert source == "issue-time-read"


def test_a_failed_s3_write_leaves_nothing_to_compare_against(s3):
    """cert_status='failed' means the endpoint's certificate never landed. Not a pass."""
    s3["client"] = _FakeS3(_cert_bytes("sha256:abc"))
    assert handler._issue_time_closure_hash("b", "certificates/x.json", "failed") == (None, None)
    assert handler._issue_time_closure_hash("b", None, "written") == (None, None)


def test_a_pre_1_1_certificate_carries_no_closure_hash(s3):
    """Old certificates are valid and hash-verify; they simply cannot be cross-checked here."""
    s3["client"] = _FakeS3(_cert_bytes(None))
    got, source = handler._issue_time_closure_hash("b", "certificates/x.json", "written")
    assert got is None
    assert source == "issue-time-read"


def test_an_unreadable_counterparty_degrades_rather_than_crashing(s3):
    s3["client"] = _FakeS3(raise_on_get=True)
    assert handler._issue_time_closure_hash("b", "certificates/x.json", "written") == (None, None)


def test_a_second_certifier_run_reports_whose_word_it_checked(s3):
    """The Lambda overwrites audit_log.cert_s3_key, so a re-run compares against a LAMBDA
    certificate, not the endpoint's. The comparison is still real; the reader is told."""
    s3["client"] = _FakeS3(_cert_bytes("sha256:abc", source="aost-replay"))
    got, source = handler._issue_time_closure_hash("b", "certificates/x.json", "written")
    assert got == "sha256:abc"
    assert source == "aost-replay"


def test_the_handler_hashes_the_pre_kill_world_with_the_shared_canonicalizer():
    """The certifier must not grow its own digest. If it did, comparing its hash to the
    endpoint's would only prove two implementations still happen to agree."""
    assert handler.certificate.canonical_digest is certificate.canonical_digest
    assert handler.certificate.closure_world is certificate.closure_world


def test_the_lambdas_closure_sql_selects_exactly_what_closure_world_hashes():
    """The two halves' SELECTs must stay in step — the contract certificate.closure_world states.

    A column added to replay.py's query and forgotten here would produce two different worlds
    from the same instant, and the certifier would report `disagreed` on a perfectly honest
    invalidation. Compared as text against the app's own SQL rather than restated by hand.
    """
    from app.services import replay

    def _cols(sql: str, before: str) -> list[str]:
        """The column names of the last SELECT projection preceding `before`."""
        flat = " ".join(str(sql).split())
        select = flat.split(before)[0].rsplit("SELECT", 1)[1]
        return sorted(c.strip().split()[-1].split(".")[-1] for c in select.split(","))

    app_belief = _cols(replay._BELIEF_SQL, "FROM beliefs")
    lam_belief = _cols(handler._BELIEF_SQL, "FROM beliefs")
    assert app_belief == lam_belief, (app_belief, lam_belief)
    assert "invalidated_at" in app_belief, "the pre-kill belief's revocation state must be hashed"

    # The closure CTE's FINAL projection (after the recursive body) is what becomes each row.
    app_closure = _cols(replay._CLOSURE_SQL, "FROM chain ORDER BY")
    lam_closure = _cols(handler._CLOSURE_SQL, "FROM chain ORDER BY")
    assert app_closure == lam_closure, (app_closure, lam_closure)
    assert "edge_invalidated_at" in app_closure, "per-edge revocation state must be hashed"
