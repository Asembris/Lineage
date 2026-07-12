"""Real invocation of the deployed certifier Lambda (Phase 3 step 8; Item 6 closure check).

Seeds + a real belief_performance curve, invalidates, then INVOKES the deployed AWS Lambda,
which re-verifies the closure AS OF SYSTEM TIME and writes the certificate to S3. We re-fetch
that S3 object and re-verify its sha256 locally.

TWO scenarios, because Item 6's closure-hash cross-check is a TRI-STATE and a demo that only
ever shows the happy path proves the check can pass, never that it can fail to find a
counterparty:

  A. invalidate via the SERVICE  -> audit_log cert_status='pending', no cert in S3
     => the Lambda has nothing to compare against => agreement = 'unavailable'
        (this is the honest answer, NOT a pass. A missing counterparty must never read as
        a successful check.)

  B. invalidate via the real POST /beliefs/{id}/invalidate endpoint, which writes its own
     certificate carrying pre_invalidation_state.closure_content_hash
     => the Lambda independently re-derives that hash from its own AOST replay and compares
     => agreement = 'agreed'

Run:  PYTHONPATH=. .venv/Scripts/python.exe scripts/demo_certifier.py
"""

import asyncio
import datetime as dt
import json
import sys
import uuid

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import insert, text  # noqa: E402

from app.db import engine  # noqa: E402
from app.models import Decision  # noqa: E402
from app.services import aws_client, certificate, s3_audit  # noqa: E402
from app.services.invalidation import invalidate_belief  # noqa: E402
from app.services.performance import recompute_belief_performance  # noqa: E402
from seed.seed import aid, bid  # noqa: E402
from seed.seed import seed as run_seed  # noqa: E402

ORIGIN = bid("origin")
ACTOR = aid("crimson-7")
BASE = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)
EARLY = (BASE - dt.timedelta(days=400), BASE - dt.timedelta(days=390))
LATE = (BASE - dt.timedelta(days=100), BASE - dt.timedelta(days=90))


def _rows(at, n, frauds):
    return [
        {
            "id": uuid.uuid4(), "agent_id": ACTOR, "txn_ref": f"cert-{at.date()}-{i}",
            "merchant": "Grocery Mart #500", "amount": 99.00, "verdict": "approve",
            "driving_belief_id": ORIGIN, "confidence": 0.9, "decided_at": at,
            "is_fraud": i < frauds,
        }
        for i in range(n)
    ]


async def _reseed():
    await run_seed()
    async with engine.begin() as c:
        await c.execute(insert(Decision), _rows(EARLY[0] + dt.timedelta(days=5), 200, 10))
        await c.execute(insert(Decision), _rows(LATE[0] + dt.timedelta(days=5), 200, 110))
    await recompute_belief_performance(ORIGIN, [EARLY, LATE])


def _invoke_certifier() -> dict | None:
    print("\n[aws] invoking lineage-certifier ...")
    lam = aws_client.client("lambda")
    resp = lam.invoke(
        FunctionName="lineage-certifier",
        Payload=json.dumps({"belief_id": str(ORIGIN)}).encode(),
    )
    payload = json.loads(resp["Payload"].read())
    if resp.get("FunctionError"):
        print("[aws] FUNCTION ERROR:")
        print(json.dumps(payload, indent=2)[:2000])
        return None
    print("[aws] Lambda returned:")
    print(json.dumps(payload, indent=2))
    return payload


async def _verify_s3(payload: dict, audit_id) -> dict:
    fetched = await asyncio.to_thread(s3_audit.get_certificate, payload["s3_key"])
    ok = certificate.verify(fetched)
    print("\n[verify] GET s3://%s/%s" % (payload["s3_bucket"], payload["s3_key"]))
    print(f"[verify] sha256 re-verifies locally : {ok}")
    print(f"[verify] hash matches Lambda's       : {fetched['content_hash'] == payload['content_hash']}")
    print(f"[verify] aost_verification block     : {json.dumps(fetched['aost_verification'])}")
    print(f"[verify] issued_by                   : {fetched['issued_by']}")

    # The staleness evidence, WITH the uncertainty behind it (schema 1.2). Printed as an
    # interval rather than two bare floats, because two bare floats are exactly what this item
    # existed to fix: a reader could not tell whether confidence_now summarized 250 samples or 5.
    se = fetched["staleness_evidence"]
    if se.get("available"):
        u = se.get("uncertainty") or {}
        lo0, hi0 = se.get("confidence_when_formed_ci_low"), se.get("confidence_when_formed_ci_high")
        lo1, hi1 = se.get("confidence_now_ci_low"), se.get("confidence_now_ci_high")
        fmt = lambda v: "null" if v is None else f"{v:.3f}"  # noqa: E731
        print(f"\n[staleness] when formed : {se['confidence_when_formed']:.3f}  "
              f"95% CI [{fmt(lo0)}, {fmt(hi0)}]  n={se.get('confidence_when_formed_sample_size')}")
        print(f"[staleness] present day : {se['confidence_now']:.3f}  "
              f"95% CI [{fmt(lo1)}, {fmt(hi1)}]  n={se.get('confidence_now_sample_size')}")
        print(f"[staleness] frauds approved, last window: "
              f"{se.get('frauds_approved_last_window')}")
        # The SECOND tri-state (distinct from the closure one below): does each persisted
        # confidence still reproduce from the decisions it claims to summarize?
        print(f"[staleness] sample_agreement = {u.get('sample_agreement')}  "
              f"(source: {u.get('sample_source')})")
        if u.get("sample_agreement") != "agreed":
            print("[staleness]  -> the intervals are WITHHELD. A fresh denominator on a stale "
                  "point estimate\n[staleness]     would be a confidently wrong number.")
        p = u.get("decay_p_value")
        print(f"[staleness] decay_supported  = {u.get('decay_supported')}"
              + (f"  (Fisher exact p = {p:.3g})" if p is not None else "")
              + f"\n[staleness]   criterion: {u.get('decay_support_criterion')}")
        if u.get("decay_supported") is False:
            print("[staleness]  -> the certificate does NOT assert a measured decay. The curve is "
                  "shown;\n[staleness]     the conclusion is not, because the data does not carry it.")
    else:
        print("[staleness] no measured windows for this belief (available=false)")

    # The Item-6 verdict, stated as a headline rather than left as one key among eight. A
    # 'disagreed' certificate is otherwise easy to miss: the Lambda still stamps
    # audit_log.cert_status='written' either way (see NOTES "no single canonical certificate").
    cv = fetched["closure_verification"]
    banner = {"agreed": "AGREED", "disagreed": "*** DISAGREED ***", "unavailable": "UNAVAILABLE"}
    print(f"\n[closure]  {banner.get(cv['agreement'], cv['agreement'])}")
    print(f"[closure]  re-derived by Lambda (AOST @ {cv['snapshot_hlc']}):")
    print(f"[closure]    {cv['rederived_closure_hash']}")
    print(f"[closure]  issue-time hash it was checked against:")
    print(f"[closure]    {cv['issue_time_closure_hash']}  (source: {cv['compared_against_source']})")
    if cv["agreement"] == "unavailable":
        print("[closure]  -> no counterparty certificate existed to compare against.")
        print("[closure]     This is NOT a pass. The Lambda re-derived the world and said so.")
    elif cv["agreement"] == "disagreed":
        print("[closure]  -> the certificate's pre-kill claim does NOT match the cluster's history.")

    async with engine.connect() as c:
        row = (
            await c.execute(
                text("SELECT cert_status FROM audit_log WHERE id=:i"), {"i": audit_id}
            )
        ).mappings().one()
    print(f"\n[db] audit_log.cert_status = {row['cert_status']}  (Lambda stamped it)")
    return fetched


async def main():
    # === Scenario A: no counterparty certificate => 'unavailable' ===============
    print("=" * 78)
    print("SCENARIO A — invalidate via the SERVICE (no certificate written to S3)")
    print("=" * 78)
    await _reseed()
    inv = await invalidate_belief(ORIGIN, ACTOR)
    print(f"\n[local] invalidated belief {ORIGIN}  (audit {inv['audit_id']}, cert pending)")
    print(f"[local] snapshot_hlc = {inv['snapshot_hlc']}")
    payload = _invoke_certifier()
    if payload is None:
        await engine.dispose()
        return
    await _verify_s3(payload, inv["audit_id"])

    # === Scenario B: the endpoint certified first => 'agreed' ===================
    print("\n\n" + "=" * 78)
    print("SCENARIO B — invalidate via POST /beliefs/{id}/invalidate (endpoint certifies)")
    print("=" * 78)
    await _reseed()

    import httpx  # local: the demo drives the REAL app, not a reimplementation

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://demo") as client:
        r = await client.post(f"/beliefs/{ORIGIN}/invalidate", json={"actor_id": str(ACTOR)})
    r.raise_for_status()
    body = r.json()
    pre = body["pre_invalidation_state"]
    print(f"\n[endpoint] status={body['certificate_status']}  key={body['certificate_s3_key']}")
    print(f"[endpoint] pre-kill closure {pre['closure_edge_open']}/{pre['closure_edge_total']} open"
          f"  source={pre['source']}")
    print(f"[endpoint] closure_content_hash = {pre['closure_content_hash']}")

    payload = _invoke_certifier()
    if payload is None:
        await engine.dispose()
        return
    fetched = await _verify_s3(payload, uuid.UUID(body["audit_id"]))

    # The headline claim, asserted rather than eyeballed.
    cv = fetched["closure_verification"]
    assert cv["agreement"] == "agreed", cv
    assert cv["rederived_closure_hash"] == pre["closure_content_hash"], cv
    print("\n[PROOF] The endpoint hashed the pre-kill world at issue time.")
    print("[PROOF] The Lambda, on AWS compute, replayed that same instant AS OF SYSTEM TIME,")
    print("[PROOF] rebuilt the world from CockroachDB's own MVCC history, and hashed it.")
    print("[PROOF] The two hashes are identical. Nothing was taken on faith.")

    await engine.dispose()


asyncio.run(main())
