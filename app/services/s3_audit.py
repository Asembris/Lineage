"""S3 audit sink — writes/reads invalidation certificates (real boto3, never mocked).

put_certificate() PUTs the certificate JSON to the audit bucket; get_certificate() reads it
back (used by the round-trip realness test). Sync boto3 — the async endpoint calls these via
asyncio.to_thread so it never blocks the event loop.

Ordering (per plan): this is a POST-COMMIT side effect. The DB invalidation is already the
source of truth; a failed PUT does not undo it — the caller records cert_status='failed' on
audit_log and can retry. Correctness is never gated on S3.
"""

from __future__ import annotations

from app.config import get_settings
from app.services import aws_client, certificate


def bucket_name() -> str:
    b = get_settings().s3_bucket
    if not b:
        raise RuntimeError("S3_BUCKET not set (config/.env)")
    return b


def certificate_key(cert: dict) -> str:
    """Deterministic object key: certificates/<belief_id>/<certificate_id>.json."""
    return f"certificates/{cert['belief']['id']}/{cert['certificate_id']}.json"


def put_certificate(cert: dict, bucket: str | None = None) -> dict:
    """PUT the certificate to S3. Returns {bucket, key, etag}. Raises on failure."""
    bucket = bucket or bucket_name()
    key = certificate_key(cert)
    s3 = aws_client.client("s3")
    resp = s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=certificate.storage_bytes(cert),
        ContentType="application/json",
    )
    return {"bucket": bucket, "key": key, "etag": resp.get("ETag", "").strip('"')}


def get_certificate(key: str, bucket: str | None = None) -> dict:
    """GET a certificate object and parse it back to a dict (for verification)."""
    import json

    bucket = bucket or bucket_name()
    s3 = aws_client.client("s3")
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    return json.loads(body)
