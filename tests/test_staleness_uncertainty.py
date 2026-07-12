"""Hermetic tests for the staleness curve's measured uncertainty (certificate schema 1.2).

ZERO cluster, ZERO S3, ZERO OpenAI — `wilson_ci` and `staleness_evidence` are pure functions,
which is the whole point of them living in the import-safe module both the endpoint and the
certifier Lambda reach.

What is worth pinning here:
  * the intervals are REAL (they reproduce the shipped curve's published numbers exactly);
  * a sample that cannot be trusted WITHHOLDS its interval rather than emitting a plausible one;
  * the support criterion is derived from the data, so a thin window disqualifies itself with no
    minimum-n gate anywhere;
  * the schema bump is ADDITIVE — a 1.0/1.1-shaped certificate still verifies;
  * `confidence_now` is still a bare float (reshaping it would break every consumer);
  * and the two halves' staleness SELECTs project the same columns, so one drifting from the
    other fails loudly instead of silently changing what an interval means.
"""

import datetime as dt
import importlib.util
import re
import sys
from pathlib import Path

from app.services import certificate

HANDLER_PATH = Path(__file__).resolve().parents[1] / "lambda" / "certifier" / "handler.py"

sys.modules.setdefault("certificate", certificate)
_spec = importlib.util.spec_from_file_location("certifier_handler_staleness", HANDLER_PATH)
handler = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(handler)

BASE = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)

# The REAL shipped curve: (n, correct) per window, straight off the deterministic backfill.
# confidence = correct/n reproduces .924 .952 .876 .852 .724 .556 .624 .528 exactly.
SHIPPED = [
    (250, 231), (250, 238), (250, 219), (250, 213),
    (250, 181), (250, 139), (250, 156), (250, 132),
]


def _row(i: int, n: int, k: int, confidence: float | None = None, frauds: int = 0) -> dict:
    """One STALENESS_COLUMNS row as either half's SELECT returns it."""
    return {
        "window_start": BASE + dt.timedelta(days=30 * i),
        "window_end": BASE + dt.timedelta(days=30 * i + 29),
        "confidence": (k / n) if confidence is None else confidence,
        "false_positive_rate": 0.0,
        "frauds_approved": frauds,
        "n": n,
        "correct": k,
    }


def _shipped_rows() -> list[dict]:
    return [_row(i, n, k) for i, (n, k) in enumerate(SHIPPED)]


# --- the statistic ---------------------------------------------------------------------------

def test_wilson_reproduces_the_shipped_curves_published_intervals():
    """The headline: 0.528 is really 0.528 [0.466, 0.589] — a band 12 points wide.

    These are the numbers the README and NOTES publish. If this test fails, either the arithmetic
    moved or the curve did; both are things to notice, not to re-baseline.
    """
    block = certificate.staleness_evidence(_shipped_rows())

    assert round(block["confidence_when_formed"], 3) == 0.924
    assert round(block["confidence_when_formed_ci_low"], 3) == 0.884
    assert round(block["confidence_when_formed_ci_high"], 3) == 0.951

    assert round(block["confidence_now"], 3) == 0.528
    assert round(block["confidence_now_ci_low"], 3) == 0.466
    assert round(block["confidence_now_ci_high"], 3) == 0.589

    assert block["confidence_now_sample_size"] == 250
    assert block["uncertainty"]["method"] == "wilson-score"
    assert block["uncertainty"]["confidence_level"] == 0.95


def test_a_perfect_window_still_has_real_downside_uncertainty():
    """Wilson, not the normal approximation — which would hand a 250/250 window a zero-width
    interval and let the certificate claim certainty it does not have."""
    lo, hi = certificate.wilson_ci(250, 250)
    assert hi == 1.0          # clamped
    assert 0.98 < lo < 1.0    # but the floor is NOT 1.0
    assert certificate.wilson_ci(0, 0) is None


def test_the_interval_narrows_with_the_sample():
    _, hi_small = certificate.wilson_ci(37, 74)   # crimson-5b's real n
    lo_small, _ = certificate.wilson_ci(37, 74)
    lo_big, hi_big = certificate.wilson_ci(125, 250)
    assert (hi_big - lo_big) < (hi_small - lo_small)


# --- the tri-state: a sample that cannot be trusted withholds its interval ---------------------

def test_a_trustworthy_curve_emits_its_intervals():
    block = certificate.staleness_evidence(_shipped_rows())
    assert block["uncertainty"]["sample_agreement"] == "agreed"
    assert block["uncertainty"]["sample_source"] == "recomputed-from-decisions"
    assert all(w["confidence_ci_low"] is not None for w in block["windows"])


def test_a_persisted_confidence_that_does_not_reproduce_withholds_every_interval():
    """belief_performance stale w.r.t. `decisions` -> `disagreed`, intervals WITHHELD.

    This is the one new failure mode re-aggregating n introduces, and the reason the query
    selects `correct` as well: a fresh denominator bolted onto a stale point estimate would be a
    confidently wrong interval — precisely the number this whole block exists to prevent.
    """
    rows = _shipped_rows()
    rows[3]["confidence"] = 0.999  # the row no longer summarizes its own decisions
    block = certificate.staleness_evidence(rows)

    assert block["uncertainty"]["sample_agreement"] == "disagreed"
    assert block["uncertainty"]["decay_supported"] is None
    assert all(w["confidence_ci_low"] is None for w in block["windows"])
    assert all(w["confidence_ci_high"] is None for w in block["windows"])
    # The point estimates and the true counts still stand — only the intervals are withheld.
    assert round(block["confidence_now"], 3) == 0.528
    assert block["windows"][0]["sample_size"] == 250


def test_a_window_with_no_decisions_to_aggregate_is_unavailable_never_faked():
    """`decisions` pruned/absent -> no denominator -> no interval. Never a fabricated one."""
    rows = _shipped_rows()
    rows[2] = _row(2, 0, 0, confidence=0.876)
    block = certificate.staleness_evidence(rows)

    assert block["uncertainty"]["sample_agreement"] == "unavailable"
    assert block["uncertainty"]["decay_supported"] is None
    assert all(w["confidence_ci_low"] is None for w in block["windows"])


def test_an_unmeasured_belief_is_still_available_false():
    assert certificate.staleness_evidence([]) == {
        "available": False, "window_count": 0, "windows": []
    }


# --- the support criterion IS the thin-window guard --------------------------------------------

def test_the_shipped_decay_survives_its_own_uncertainty():
    """0.924 (231/250) vs 0.528 (132/250) — overwhelming, and the certificate may say so."""
    block = certificate.staleness_evidence(_shipped_rows())
    unc = block["uncertainty"]
    assert unc["decay_supported"] is True
    assert unc["decay_p_value"] < 1e-20  # Item C measured this decay at z = -9.93
    assert unc["decay_support_criterion"] == "fisher-exact-two-sided-first-vs-last-window"


def test_a_one_sample_final_window_disqualifies_itself_with_no_minimum_n_gate():
    """performance.py writes a row for any n >= 1, and it should KEEP doing so — the row is a
    real measurement. What was missing was its PRECISION, not its right to exist. Refusing to
    persist a measured window would make the curve lie by omission.

    So there is no minimum-n gate. Fisher is exact at every n, and the thin window disqualifies
    ITSELF: one wrongly-decided decision against a healthy 0.924 baseline gives p = 0.080, so
    "same rate" cannot be rejected and no decay is asserted. The measurement is still reported —
    with the sample size and the enormous band that make it self-evidently useless.
    """
    rows = _shipped_rows()[:-1] + [_row(7, 1, 0)]  # final window: n=1, confidence 0.0
    block = certificate.staleness_evidence(rows)
    unc = block["uncertainty"]

    assert block["confidence_now"] == 0.0            # the measurement is still reported...
    assert block["confidence_now_sample_size"] == 1  # ...and so is what it rests on
    lo, hi = block["confidence_now_ci_low"], block["confidence_now_ci_high"]
    assert lo == 0.0 and round(hi, 3) == 0.793       # a band spanning most of [0, 1]

    assert unc["sample_agreement"] == "agreed"       # the sample IS internally consistent...
    assert round(unc["decay_p_value"], 3) == 0.080   # ...it is just far too thin to conclude
    assert unc["decay_supported"] is False


def test_interval_non_overlap_would_have_called_that_one_sample_window_a_supported_decay():
    """THE REASON THE CRITERION IS FISHER AND NOT "DO THE INTERVALS OVERLAP?".

    Non-overlap is the obvious criterion, and it is WRONG HERE — it fails in exactly the
    direction that defeats this whole block. The textbook property (disjoint intervals => the
    rates really differ) holds for symmetric normal-approximation intervals; Wilson intervals at
    extreme small n break it. This test PINS the counterexample so nobody "simplifies" the
    criterion back and reintroduces the bug. It asserts the defect, not the behaviour.
    """
    healthy = certificate.wilson_ci(231, 250)   # [0.884, 0.951]
    one_bad = certificate.wilson_ci(0, 1)       # [0.000, 0.793]

    # Disjoint -- a non-overlap rule would happily assert a measured decay off ONE decision.
    assert healthy[0] > one_bad[1]
    # Fisher, exact at n=1, correctly refuses: that observation is 7.6% likely under no decay.
    assert certificate._fisher_exact_2sided(231, 19, 0, 1) > 0.05


def test_a_thin_but_unambiguous_window_is_still_supported():
    """The criterion is EVIDENCE, not sample size. A small sample that is genuinely conclusive is
    not penalised — which is exactly what a minimum-n gate would get wrong."""
    rows = [_row(0, 30, 30), _row(1, 30, 3)]  # 1.00 vs 0.10 on n=30: thin, but unmistakable
    block = certificate.staleness_evidence(rows)
    assert block["uncertainty"]["decay_supported"] is True
    assert block["uncertainty"]["decay_p_value"] < 0.05


def test_an_improving_belief_is_not_a_decay():
    """`decay_supported` means it ROTTED, not merely that the rate moved. A belief that got
    significantly BETTER must not hand a certificate evidence for invalidating it."""
    rows = [_row(0, 250, 132), _row(1, 250, 231)]  # 0.528 -> 0.924, the shipped curve reversed
    block = certificate.staleness_evidence(rows)
    assert block["uncertainty"]["decay_p_value"] < 1e-20  # the change is real...
    assert block["uncertainty"]["decay_supported"] is False  # ...and it is an improvement


def test_a_single_window_can_assert_no_decay():
    block = certificate.staleness_evidence([_row(0, 250, 132)])
    assert block["uncertainty"]["decay_supported"] is False
    assert block["uncertainty"]["decay_p_value"] is None


# --- the schema bump is additive ---------------------------------------------------------------

def _fake_inv() -> dict:
    import uuid as _u
    agents = [{"id": _u.uuid4(), "generation": 0, "bloodline": "crimson", "status": "dead"}]
    return {
        "audit_id": _u.uuid4(),
        "belief": {
            "id": _u.uuid4(), "rule_text": "r", "originating_agent_id": agents[0]["id"],
            "formed_at": BASE, "status": "active",
        },
        "actor_id": _u.uuid4(),
        "invalidated_at": BASE,
        "snapshot_hlc": "1751500000000000000.0000000000",
        "affected_agents": agents,
        "living_holders": [],
        "affected_agent_count": 1,
        "affected_edge_count": 8,
    }


def test_the_schema_is_1_2_and_the_new_evidence_is_hash_covered():
    cert = certificate.build_certificate(
        _fake_inv(), certificate.staleness_evidence(_shipped_rows())
    )
    assert cert["schema_version"] == "1.2"
    assert certificate.verify(cert) is True

    # Forging the interval must break the document, exactly like forging the pre-kill state.
    cert["staleness_evidence"]["confidence_now_ci_low"] = 0.9
    assert certificate.verify(cert) is False


def test_a_pre_1_2_certificate_still_verifies_under_1_2_code():
    """The additive guarantee, and it is not theoretical: 66 schema-1.0 certificates written
    before the 1.1 bump still verify in S3 today. `_digest` hashes whatever keys are PRESENT and
    `verify()` re-derives over the same set with no version branch, so a document is always
    checked against the keys it actually carries.
    """
    legacy_staleness = {  # exactly the pre-1.2 block shape: no sizes, no intervals
        "available": True,
        "confidence_when_formed": 0.95,
        "confidence_now": 0.45,
        "frauds_approved_last_window": 118,
        "window_count": 2,
        "windows": [
            {"window_start": BASE, "window_end": BASE, "confidence": 0.95,
             "false_positive_rate": 0.0, "frauds_approved": 19},
        ],
    }
    cert = certificate.build_certificate(_fake_inv(), legacy_staleness)
    cert["schema_version"] = "1.1"          # pretend it was issued before this change...
    cert["content_hash"] = certificate._digest(cert)
    assert certificate.verify(cert) is True  # ...and it still verifies under 1.2 code


def test_confidence_now_is_still_a_bare_float():
    """The one thing that would have broken every consumer: reshaping an existing field.

    verify() would not have caught it (a certificate is hashed over whatever it carries), which
    is exactly why it is pinned here instead. The intervals are SIBLINGS.
    """
    block = certificate.staleness_evidence(_shipped_rows())
    assert isinstance(block["confidence_now"], float)
    assert isinstance(block["confidence_when_formed"], float)
    assert isinstance(block["windows"][0]["confidence"], float)
    for legacy_key in (
        "available", "confidence_when_formed", "confidence_now",
        "frauds_approved_last_window", "window_count", "windows",
    ):
        assert legacy_key in block


def test_false_positive_rate_has_no_interval():
    """Structurally 0 for a belief that only ever approves. An interval there would dress a
    structural impossibility as an uncertain estimate — and it has a different denominator."""
    block = certificate.staleness_evidence(_shipped_rows())
    for w in block["windows"]:
        assert w["false_positive_rate"] == 0.0
        assert not any(k.startswith("false_positive_rate_ci") for k in w)


# --- the two halves must not drift apart --------------------------------------------------------

def _staleness_cols(sql: str) -> list[str]:
    """Column names projected by a staleness SELECT (aliases + the bp.* passthroughs)."""
    flat = " ".join(str(sql).split())
    proj = flat.split("FROM belief_performance")[0].split("SELECT", 1)[1]
    cols = set(re.findall(r"\bAS\s+(\w+)", proj, re.I))
    cols |= set(re.findall(r"\bbp\.(\w+)", proj))
    return sorted(cols)


def test_both_halves_staleness_selects_project_the_same_columns():
    """The endpoint (async SQLAlchemy) and the certifier Lambda (sync psycopg) issue their OWN
    staleness SQL — different drivers, different placeholder styles, so the TEXT cannot be
    shared. The COLUMN CONTRACT is, and this is what keeps them in step.

    A column added to one and forgotten in the other would change what an interval MEANS on one
    of the two certificates issued for the same invalidation, silently. Mirrors
    test_certifier_closure_verification's identical guard for the closure SELECTs.
    """
    expected = sorted(certificate.STALENESS_COLUMNS)
    assert _staleness_cols(certificate._STALENESS_SQL) == expected
    assert _staleness_cols(handler._STALENESS_SQL) == expected
    # The denominator and the numerator are the whole point — neither may be dropped.
    assert "n" in expected and "correct" in expected


def test_the_lambda_does_not_grow_its_own_statistics():
    """The shared-canonicalizer discipline, applied to the staleness block.

    The Lambda must call THIS builder. If it re-implements Wilson, the window shape, or the
    support criterion, the interval on its certificate and the interval on the endpoint's
    certificate for the SAME invalidation can drift apart in silence — the identical false
    guarantee that forced canonical_digest/closure_world to be shared (NOTES Item 6).

    NOTE what this does NOT assert: that the Lambda re-derives and COMPARES the intervals. It
    must not, and a future session must not add that. A confidence interval is pure arithmetic
    over (k, n), not a claim about the world, so there is no independent oracle to check it
    against — unlike the closure hash, which CockroachDB's own MVCC history corroborates. Both
    halves read belief_performance at current committed state, so neither is a check on the
    other. A `staleness_verification: agreed` block would fabricate the appearance of
    closure_verification's guarantee while proving nothing.
    """
    assert handler.certificate.staleness_evidence is certificate.staleness_evidence
    assert handler.certificate.wilson_ci is certificate.wilson_ci
    assert not hasattr(handler, "_staleness"), "the Lambda re-grew a local staleness builder"
    assert "staleness_verification" not in certificate.build_certificate(
        _fake_inv(), certificate.staleness_evidence(_shipped_rows())
    )
