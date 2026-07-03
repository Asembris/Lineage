"""Real invocation of the deployed certifier Lambda (Phase 3, step 8 gate).

Seeds + a real belief_performance curve, invalidates via the SERVICE (leaves audit_log
cert_status='pending' — the endpoint's inline cert path is untouched), then INVOKES the
deployed AWS Lambda. The Lambda re-verifies the closure AS OF SYSTEM TIME and writes the
certificate to S3. We then re-fetch that S3 object and re-verify its sha256 locally.

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


async def main():
    await run_seed()
    async with engine.begin() as c:
        await c.execute(insert(Decision), _rows(EARLY[0] + dt.timedelta(days=5), 200, 10))
        await c.execute(insert(Decision), _rows(LATE[0] + dt.timedelta(days=5), 200, 110))
    await recompute_belief_performance(ORIGIN, [EARLY, LATE])

    inv = await invalidate_belief(ORIGIN, ACTOR)
    print(f"\n[local] invalidated belief {ORIGIN}  (audit {inv['audit_id']}, cert pending)")
    print(f"[local] snapshot_hlc = {inv['snapshot_hlc']}")

    # --- invoke the DEPLOYED Lambda ---
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
        await engine.dispose()
        return

    print("[aws] Lambda returned:")
    print(json.dumps(payload, indent=2))

    # --- re-fetch the cert the Lambda wrote to S3 and re-verify its hash locally ---
    fetched = await asyncio.to_thread(s3_audit.get_certificate, payload["s3_key"])
    ok = certificate.verify(fetched)
    print("\n[verify] GET s3://%s/%s" % (payload["s3_bucket"], payload["s3_key"]))
    print(f"[verify] sha256 re-verifies locally : {ok}")
    print(f"[verify] hash matches Lambda's       : {fetched['content_hash'] == payload['content_hash']}")
    print(f"[verify] aost_verification block     : {json.dumps(fetched['aost_verification'])}")
    print(f"[verify] issued_by                   : {fetched['issued_by']}")
    print(f"[verify] staleness conf {fetched['staleness_evidence'].get('confidence_when_formed')}"
          f" -> {fetched['staleness_evidence'].get('confidence_now')}, "
          f"frauds_last={fetched['staleness_evidence'].get('frauds_approved_last_window')}")

    # --- confirm the Lambda closed the audit loop in CRDB ---
    async with engine.connect() as c:
        row = (
            await c.execute(
                text("SELECT cert_status, cert_s3_key FROM audit_log WHERE id=:i"),
                {"i": inv["audit_id"]},
            )
        ).mappings().one()
    print(f"\n[db] audit_log.cert_status = {row['cert_status']}  (Lambda stamped it)")
    await engine.dispose()


asyncio.run(main())
