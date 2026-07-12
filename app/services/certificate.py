"""Audit certificate — the tamper-evident record of a belief invalidation (Phase 3).

A certificate is a self-contained JSON document proving WHAT was invalidated, WHY (the real
staleness evidence from belief_performance — "valid then / rotten now", never asserted), WHO
did it, WHEN, and the full affected closure. It is PUT to S3 by s3_audit.py.

Integrity model (no HMAC, per plan):
  1. SELF-CONTAINED pre-kill record. The certificate body carries pre_invalidation_state — the
     MEASURED "belief active, whole closure open" fact captured at issue-time (inside the
     invalidation txn, before the flip; or, for the certifier Lambda, from its independent AOST
     replay). It is hash-covered, so the document proves what the world was immediately before
     the kill WITHOUT depending on any external lookup. This is the primary integrity mechanism.
  2. content_hash = sha256 over the canonical (sorted-key) JSON of every field except the hash
     itself. The round-trip test re-reads the object from S3 and re-derives this hash — any
     tampering with the pre-kill record (or anything else) breaks it.
  3. db_snapshot_hlc pins the pre-invalidation MVCC version as a BONUS freshness cross-check:
     within CockroachDB's GC window (gc.ttlseconds, ~75 min) anyone can replay the cluster
     `AS OF SYSTEM TIME db_snapshot_hlc` and independently reproduce the same world. Once the
     snapshot ages past the GC TTL this replay no longer resolves — which is exactly why the
     self-contained record in (1) exists: the certificate does not silently lose its integrity
     guarantee after 75 minutes.
  4. pre_invalidation_state.closure_content_hash CONTENT-ADDRESSES the pre-kill world
     (Item 6). The counts in (1) say "8 of 8 edges were open"; this says WHICH world those
     counts summarize — sha256 over the canonically-serialized belief row plus every closure
     edge's revocation state, the same digest `GET /beliefs/{id}/replay` returns. The certifier
     Lambda reconstructs that world independently via its own AOST replay and compares hashes,
     so the pre-kill claim is re-derived on separate compute rather than taken on the app's
     word. See `closure_world` / `canonical_digest` below.

  5. staleness_evidence carries its own UNCERTAINTY (schema 1.2). The invalidation's data
     justification is a measured proportion, and a proportion without its denominator is not a
     measurement — a reader could not tell whether `confidence_now: 0.528` summarizes 250
     samples or 5. Every window now arrives with the sample size it was aggregated over and a
     95% Wilson interval, `n` being RE-AGGREGATED from `decisions` at read time rather than
     persisted (no migration, no change to the five-table moat). The block states plainly
     whether the "valid then / rotten now" claim survives that uncertainty — see
     `staleness_evidence` below for the tri-state and the support criterion, and for why the
     Lambda does NOT cross-check these intervals.

What this integrity model does NOT provide, stated so nobody mistakes it for more (Item 6):
  content_hash is an UNKEYED digest, so it proves nothing about AUTHORSHIP. Anyone can forge a
  certificate and compute a perfectly self-consistent hash for it; `verify()` would return True.
  What actually anchors authenticity is the DATABASE — audit_log.content_hash holds the expected
  digest, and within the GC window the AOST replay reproduces the claimed world from CRDB's own
  MVCC history. A forgery has neither. The residual gap is that an offline third party holding
  ONLY the JSON, with no cluster access, cannot verify authorship. Asymmetric signing would close
  it (HMAC would not — a shared secret lets the verifier forge too). Deliberately not built: it
  needs a new AWS service, which CLAUDE.md forbids adding unasked. See NOTES.md "Roadmap Item 6".
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import uuid

# 1.1 (Item 6) adds pre_invalidation_state.closure_content_hash + the certifier's
# closure_verification stamp.
# 1.2 (the staleness-uncertainty item) adds sample sizes + Wilson intervals to
# staleness_evidence. Additive only: `_digest` hashes whatever keys are present and `verify()`
# re-derives over the same set with NO version branch, so 1.0 and 1.1 certificates still verify
# unchanged (empirically: all 66 pre-1.1 certificates in S3 still verify under 1.1 code). The
# existing scalars keep their shape — `confidence_now` stays a bare float and gains SIBLING
# interval fields. Reshaping it into {point, lo, hi} would not break verify(), but it would
# break every consumer that reads it as a number.
SCHEMA_VERSION = "1.2"

# NOTE: this module is import-safe with ZERO app/SQLAlchemy dependencies at load time, so the
# certifier Lambda can `import certificate` and reuse build_certificate/verify/storage_bytes
# without dragging in app.config (which requires DATABASE_URL/OPENAI_API_KEY). The only DB
# helper, gather_staleness_evidence, imports engine/text lazily inside the function; the Lambda
# does its own sync staleness query and never calls it.

# --- staleness uncertainty ---------------------------------------------------------------
#
# WHY THE LAMBDA DOES NOT "RE-DERIVE AND COMPARE" THESE INTERVALS — READ THIS BEFORE ADDING A
# CROSS-CHECK. A future session WILL be tempted to bolt a `staleness_verification: agreed`
# block onto the certificate, by analogy with closure_verification. That would be wrong, and
# the analogy is what makes it wrong.
#
# Item 6's rule — "hash-coverage proves a document has not CHANGED; it can never prove the
# document was TRUE" — is a rule about CLAIMS ABOUT THE WORLD. The closure hash asserts "the
# world at snapshot_hlc was this", and the certifier re-derives it because an INDEPENDENT
# ORACLE exists: CockroachDB's own MVCC history, replayed AS OF SYSTEM TIME on separate
# compute. Two reads, one world, one hash to compare.
#
# A confidence interval is not a claim about the world. It is wilson_ci(k, n) — pure
# arithmetic, no I/O, nothing to corroborate. Two independent implementations of Wilson
# agreeing would prove only that two parties can do algebra. The thing that CAN be wrong is the
# READ (k, n) — and for staleness there is NO oracle to re-derive against: Item 6 established,
# and it is still true, that BOTH halves read belief_performance at CURRENT COMMITTED STATE
# (never AOST), so "neither is a check on the other". Re-aggregating n from `decisions` does
# not change that; both halves still read current state. A `staleness_verification: agreed`
# block would therefore FABRICATE THE APPEARANCE of the closure hash's hard-won guarantee while
# proving nothing — the same "every field true, juxtaposition fabricated" move Item 6 refused.
#
# What DOES apply, with full force, is the SHARED-CANONICALIZER obligation. The endpoint and the
# Lambda each build their OWN certificate (Item 6: there is no single canonical certificate per
# invalidation), so the Lambda cannot "carry" the endpoint's interval — it never sees it. If the
# Wilson formula, the window shape, and the support criterion were implemented twice, the
# interval on the endpoint's certificate and the interval on the Lambda's certificate FOR THE
# SAME INVALIDATION would silently drift apart. So the statistics live HERE, in the import-safe
# module both halves already reach for canonical_digest / closure_world, and each half supplies
# only its own (k, n) from its own DB read.
#
# (scripts/eval_detection.py keeps its own wilson_ci ON PURPOSE: it is a deliberately app-free
# script — tests/test_eval_detection.py imports it with no app import at all — and its intervals
# are never compared against, nor printed in the same artifact as, these. Do not "unify" them;
# that would couple the eval to the app package for no guarantee.)

WILSON_Z = 1.96  # 95%
_CONFIDENCE_LEVEL = 0.95
_AGREEMENT_TOL = 1e-9

# The column set BOTH staleness reads must project. The async half (gather_staleness_evidence,
# below) and the sync half (the certifier Lambda's _staleness) issue their own SQL — different
# drivers, different placeholder styles — so the TEXT cannot be shared. What is shared is this
# contract plus the pure statistics; tests/test_staleness_uncertainty.py asserts both SELECTs
# project exactly these columns, so a column added to one and forgotten in the other fails
# loudly. This mirrors closure_world's contract exactly (Item 6).
STALENESS_COLUMNS = (
    "confidence",
    "correct",
    "false_positive_rate",
    "frauds_approved",
    "n",
    "window_end",
    "window_start",
)

# `n` and `correct` are RE-AGGREGATED FROM `decisions` — belief_performance persists no
# denominator, and this deliberately does not add one (no migration, no change to the five-table
# moat). It is the same mechanism Item B chose: a deterministic aggregate over immutable columns,
# no new persisted state. The window BOUNDS come from the persisted belief_performance rows, so
# no caller needs generation_windows() — which is what lets the Lambda, which cannot import
# app.sim.transactions, run the identical aggregate.
_STALENESS_SQL = """
    SELECT bp.window_start, bp.window_end, bp.confidence, bp.false_positive_rate,
           bp.frauds_approved,
           count(d.id) AS n,
           sum(CASE WHEN (d.verdict = 'approve' AND NOT d.is_fraud)
                      OR (d.verdict IN ('decline', 'blocked') AND d.is_fraud)
                    THEN 1 ELSE 0 END) AS correct
    FROM belief_performance bp
    LEFT JOIN decisions d
      ON d.driving_belief_id = bp.belief_id
     AND d.decided_at >= bp.window_start
     AND d.decided_at <  bp.window_end
    WHERE bp.belief_id = :b
    GROUP BY bp.window_start, bp.window_end, bp.confidence, bp.false_positive_rate,
             bp.frauds_approved
    ORDER BY bp.window_start
"""


def _fisher_exact_2sided(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact p for [[a, b], [c, d]] (correct/incorrect x first/last window).

    EXACT at every sample size — which is why it, and not interval non-overlap, decides
    `decay_supported`. See `staleness_evidence` for the measured reason that choice is not
    cosmetic.
    """
    n1, total = a + b, a + b + c + d
    successes = a + c
    if n1 == 0 or c + d == 0 or successes == 0 or successes == total:
        return 1.0

    def _logp(x: int) -> float:
        # Hypergeometric: x successes among the n1 drawn for the first window.
        return (
            math.lgamma(successes + 1) - math.lgamma(x + 1) - math.lgamma(successes - x + 1)
            + math.lgamma(total - successes + 1)
            - math.lgamma(n1 - x + 1)
            - math.lgamma(total - successes - n1 + x + 1)
            - (
                math.lgamma(total + 1)
                - math.lgamma(n1 + 1)
                - math.lgamma(total - n1 + 1)
            )
        )

    lo = max(0, n1 - (total - successes))
    hi = min(n1, successes)
    p_obs = math.exp(_logp(a))
    # Two-sided: every table at least as extreme (no more likely) than the one observed.
    tail = sum(
        p for x in range(lo, hi + 1)
        if (p := math.exp(_logp(x))) <= p_obs * (1.0 + 1e-7)
    )
    return min(1.0, tail)


def wilson_ci(k: int, n: int, z: float = WILSON_Z) -> tuple[float, float] | None:
    """Wilson score interval for a binomial proportion. Returns (lo, hi), or None if n == 0.

    Wilson, not the normal approximation — the same instrument the detection eval uses, and for
    the same reason: `confidence` is a proportion (correct/total), and the normal approximation
    degrades badly at the extremes (a 250/250 window would get a zero-width interval).
    """
    if n <= 0:
        return None
    p = k / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def staleness_evidence(rows: list) -> dict:
    """Build the certificate's staleness_evidence block. PURE — shared by both halves.

    `rows` are the STALENESS_COLUMNS, ordered by window_start, as read by whichever half is
    calling (async SQLAlchemy in the endpoint, sync psycopg in the Lambda). See the block
    comment above for why this is shared code rather than a cross-checked claim.

    THE TRI-STATE (`uncertainty.sample_agreement`), house style — an honest "cannot determine"
    over a silent bad number:
      * `agreed`      — every window's re-aggregated correct/n reproduces its PERSISTED
                        confidence. The intervals are emitted.
      * `disagreed`   — a persisted confidence does NOT match the decisions it claims to
                        summarize, so belief_performance is stale w.r.t. `decisions`. Intervals
                        are WITHHELD: pairing a fresh denominator with a stale point estimate is
                        exactly the confidently-wrong number this block exists to prevent.
      * `unavailable` — a window has no decisions to aggregate (n = 0), so no interval is
                        derivable. Withheld, never faked.
    `sample_size` is reported in every case — it is a true count either way.

    THE SUPPORT CRITERION (`uncertainty.decay_supported`). The certificate's staleness claim is
    "valid then, rotten now". It is asserted only when a two-sided FISHER EXACT test on the first
    vs. last window (correct/incorrect) rejects "same rate" at the 95% level — the same 95%
    already carried by the intervals, not a second hand-tuned knob. There is deliberately NO
    minimum-sample-size gate anywhere: that would repeat Item 4's rejected MARGIN_FLOOR, a
    threshold with no principled derivation withholding a real finding for a reason with no
    bearing on the question. `performance.py` therefore still writes a row for any n >= 1, and
    should: the row is a real measurement. What was missing was its PRECISION, not its right to
    exist.

    WHY FISHER AND NOT "DO THE 95% INTERVALS OVERLAP?" — measured, not assumed, and the reason
    matters because non-overlap is the obvious choice and it is WRONG HERE. The textbook property
    (disjoint intervals => the rates really differ) holds for symmetric normal-approximation
    intervals. It does NOT hold for Wilson intervals at extreme small n, and it fails in exactly
    the direction that would defeat this whole block: a final window of ONE decision, called
    wrongly, yields Wilson [0.000, 0.793], which is disjoint from a healthy first window's
    [0.884, 0.951] — so a non-overlap rule reports "measured decay SUPPORTED" off a single
    sample. Fisher's exact p for that table is 0.080: observing one wrong call when the true rate
    is 0.924 happens 7.6% of the time, so "same rate" cannot be rejected at all. Fisher is exact
    at every n, so the thin-window case disqualifies itself with no gate — which is what the
    non-overlap rule only appeared to do. Do not "simplify" this back to comparing the intervals.

    The Wilson intervals are still what the READER is shown; Fisher is what the DOCUMENT is
    willing to assert. Those are different jobs and the block reports both.

    NOT given an interval: `false_positive_rate`. It is structurally 0 in every window because
    this belief only ever approves (its failure mode is approving fraud, never false positives),
    so an interval there would dress a structural impossibility as an uncertain estimate. It also
    has a DIFFERENT denominator (legit rows, not all rows) — the three-denominators discipline
    says keep them apart.
    """
    if not rows:
        return {"available": False, "window_count": 0, "windows": []}

    agreement = "agreed"
    for r in rows:
        n = int(r["n"] or 0)
        if n <= 0:
            agreement = "unavailable"
            break
        if abs(int(r["correct"] or 0) / n - float(r["confidence"])) > _AGREEMENT_TOL:
            agreement = "disagreed"
            break
    trusted = agreement == "agreed"

    windows = []
    for r in rows:
        n = int(r["n"] or 0)
        ci = wilson_ci(int(r["correct"] or 0), n) if trusted else None
        windows.append(
            {
                "window_start": r["window_start"],
                "window_end": r["window_end"],
                "confidence": float(r["confidence"]),
                "false_positive_rate": float(r["false_positive_rate"]),
                "frauds_approved": int(r["frauds_approved"]),
                # --- additive, schema 1.2 ---
                "sample_size": n,
                "confidence_ci_low": ci[0] if ci else None,
                "confidence_ci_high": ci[1] if ci else None,
            }
        )

    first, last = windows[0], windows[-1]
    decay_supported = None
    decay_p_value = None
    if trusted and len(windows) > 1:
        f_k, f_n = int(rows[0]["correct"]), int(rows[0]["n"])
        l_k, l_n = int(rows[-1]["correct"]), int(rows[-1]["n"])
        decay_p_value = _fisher_exact_2sided(f_k, f_n - f_k, l_k, l_n - l_k)
        # Decay, not merely "different": the claim is that it ROTTED, so the drop must be real
        # AND downward. A single window (first is last) can assert nothing and says so.
        decay_supported = (
            decay_p_value < (1.0 - _CONFIDENCE_LEVEL)
            and last["confidence"] < first["confidence"]
        )
    elif trusted:
        decay_supported = False

    return {
        "available": True,
        # Unchanged shape (schema <= 1.1 consumers keep working): bare floats/ints.
        "confidence_when_formed": first["confidence"],
        "confidence_now": last["confidence"],
        "frauds_approved_last_window": last["frauds_approved"],
        "window_count": len(windows),
        "windows": windows,
        # --- additive siblings, schema 1.2 ---
        "confidence_when_formed_ci_low": first["confidence_ci_low"],
        "confidence_when_formed_ci_high": first["confidence_ci_high"],
        "confidence_when_formed_sample_size": first["sample_size"],
        "confidence_now_ci_low": last["confidence_ci_low"],
        "confidence_now_ci_high": last["confidence_ci_high"],
        "confidence_now_sample_size": last["sample_size"],
        "uncertainty": {
            "method": "wilson-score",
            "confidence_level": _CONFIDENCE_LEVEL,
            "sample_source": "recomputed-from-decisions",
            "sample_agreement": agreement,
            "decay_supported": decay_supported,
            "decay_p_value": decay_p_value,
            "decay_support_criterion": "fisher-exact-two-sided-first-vs-last-window",
        },
    }


async def gather_staleness_evidence(belief_id: uuid.UUID) -> dict:
    """Pull the belief's measured performance (the invalidation's data justification).

    The async half of the staleness read. Issues _STALENESS_SQL — the persisted curve joined to
    the `decisions` it was aggregated from, so every point estimate arrives with the denominator
    behind it — and hands the rows to the SHARED `staleness_evidence` builder. The certifier
    Lambda runs the same aggregate through sync psycopg and calls the same builder.

    If belief_performance has no rows for this belief, returns {available: False} — the
    certificate is still valid, just without a quantified curve.
    """
    from sqlalchemy import text

    from app.db import engine

    async with engine.connect() as c:
        rows = (
            await c.execute(text(_STALENESS_SQL), {"b": belief_id})
        ).mappings().all()
    return staleness_evidence(rows)


def build_certificate(inv: dict, staleness: dict, extra: dict | None = None) -> dict:
    """Assemble the certificate dict (with content_hash) from an invalidation result.

    Pure function of its inputs — no I/O — so it is trivially testable. `extra` merges extra
    top-level fields (e.g. the certifier Lambda's aost_verification stamp) BEFORE hashing, so
    they are covered by content_hash.
    """
    belief = inv["belief"]
    agents = inv["affected_agents"]
    living = inv["living_holders"]
    # Self-contained pre-kill record (hash-covered). Callers supply it measured (endpoint:
    # issue-time read inside the txn; Lambda: AOST replay). Fall back to deriving it from the
    # affected counts so the field is always present and the document is never GC-dependent.
    pre_state = inv.get("pre_state") or {
        "belief_status": "active",
        "closure_edge_total": inv["affected_edge_count"],
        "closure_edge_open": inv["affected_edge_count"],
        "affected_agent_count": inv["affected_agent_count"],
        "living_holder_count": len(living),
        "snapshot_hlc": inv["snapshot_hlc"],
        # A derived pre_state has no reconstructed world behind it, so it cannot content-address
        # one. Present-and-null, never absent: a consumer distinguishes "no hash" from "field
        # missing" without knowing which caller built the certificate.
        "closure_content_hash": None,
        "source": "derived",
    }
    cert = {
        "schema_version": SCHEMA_VERSION,
        "certificate_id": str(uuid.uuid4()),
        "issued_at": dt.datetime.now(dt.timezone.utc),
        "action": "belief_invalidation",
        "actor": str(inv["actor_id"]),
        "audit_id": str(inv["audit_id"]),
        "belief": {
            "id": str(belief["id"]),
            "rule_text": belief["rule_text"],
            "originating_agent_id": str(belief["originating_agent_id"]),
            "formed_at": belief["formed_at"],
            "status_before": "active",
            "status_after": "invalidated",
            "invalidated_at": inv["invalidated_at"],
        },
        "staleness_evidence": staleness,
        # The self-contained pre-kill world (see module docstring, integrity mechanism #1).
        "pre_invalidation_state": pre_state,
        "affected_closure": {
            "agent_count": inv["affected_agent_count"],
            "edge_count": inv["affected_edge_count"],
            "living_holder_count": len(living),
            "agent_ids": [str(a["id"]) for a in agents],
            "living_holder_ids": [str(a["id"]) for a in living],
            "bloodlines": sorted({a["bloodline"] for a in agents}),
        },
        # Pre-invalidation MVCC version — the AOST cross-check oracle.
        "db_snapshot_hlc": inv["snapshot_hlc"],
    }
    if extra:
        cert.update(extra)
    cert["content_hash"] = _digest(cert)
    return cert


def canonical_json(obj) -> bytes:
    """The project's ONE canonical serialization: sorted keys, no incidental whitespace.

    Every content hash in this system — the certificate's, and the replay snapshot's — is a
    digest of this function's output. It lives here because this module is the import-safe one
    (see the note above): app-side code and the certifier Lambda can both reach it.
    """
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), default=_json_default
    ).encode("utf-8")


def canonical_digest(obj) -> str:
    """sha256 over canonical_json(obj), prefixed with its algorithm."""
    return "sha256:" + hashlib.sha256(canonical_json(obj)).hexdigest()


def closure_world(belief: dict, closure: list[dict]) -> dict:
    """The reconstructed world a closure hash covers: one belief row + its inheritance closure.

    Shared, not duplicated, and that is the whole point. Two parties hash this world: the app
    (app/services/replay.py, async SQLAlchemy) and the certifier Lambda (sync psycopg, its own
    independent AOST replay). If each built the dict and the digest with its own code, a
    hash-equality check between them would only ever prove the two implementations still agree
    — a guarantee that silently evaporates the day one of them drifts. Both call THIS, so the
    only thing that can differ between them is what they read from CockroachDB, which is
    precisely what the comparison is supposed to be testing.

    The two callers' SELECTs must therefore produce the same column sets: belief =
    (id, rule_text, status, originating_agent_id, formed_at, invalidated_at); each closure row =
    (depth, agent_id, generation, bloodline, status, from_agent_id, inherited_at,
    edge_invalidated_at), in the total order (depth, generation, agent_id).
    """
    return {
        "belief": dict(belief),
        "origin_agent_id": belief["originating_agent_id"],
        "closure": [dict(r) for r in closure],
    }


def _digest(cert: dict) -> str:
    """sha256 over the canonical JSON of every field except content_hash."""
    return canonical_digest({k: v for k, v in cert.items() if k != "content_hash"})


def verify(cert: dict) -> bool:
    """Re-derive the content_hash and compare — detects any field tampering."""
    return cert.get("content_hash") == _digest(cert)


def storage_bytes(cert: dict) -> bytes:
    """Human-readable canonical JSON for the S3 object body (stable key order)."""
    return json.dumps(
        cert, sort_keys=True, indent=2, default=_json_default
    ).encode("utf-8")


def _json_default(o):
    if isinstance(o, dt.datetime):
        return o.isoformat()
    if isinstance(o, uuid.UUID):
        return str(o)
    raise TypeError(f"not JSON-serializable: {type(o)}")
