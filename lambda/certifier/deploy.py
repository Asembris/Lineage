"""Deploy the certifier Lambda via boto3 (no AWS CLI on this box, no IaC framework).

Creates or updates function `lineage-certifier` from certifier.zip, against the execution
role the account owner provisioned. DATABASE_URL (raw) + S3_BUCKET go in as encrypted env
vars. Uses app.services.aws_client so the deploy call itself survives the Windows AVG MITM.

Run (after build.py):  .venv/Scripts/python.exe lambda/certifier/deploy.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config import get_settings  # noqa: E402
from app.services import aws_client  # noqa: E402

FUNCTION = "lineage-certifier"
ROLE_ARN = "arn:aws:iam::265243686715:role/lineage-certifier-role"
HANDLER = "handler.lambda_handler"
ZIP = Path(__file__).resolve().parent / "certifier.zip"


def main() -> int:
    if not ZIP.exists():
        print(f"[deploy] {ZIP} missing — run build.py first")
        return 1
    s = get_settings()
    env = {"DATABASE_URL": s.database_url, "S3_BUCKET": s.s3_bucket}  # raw URL for psycopg
    zip_bytes = ZIP.read_bytes()
    lam = aws_client.client("lambda")

    cfg = dict(
        FunctionName=FUNCTION,
        Runtime="python3.12",
        Role=ROLE_ARN,
        Handler=HANDLER,
        Timeout=30,
        MemorySize=256,
        Architectures=["x86_64"],
        Environment={"Variables": env},
    )

    try:
        lam.get_function(FunctionName=FUNCTION)
        exists = True
    except lam.exceptions.ResourceNotFoundException:
        exists = False

    if exists:
        print(f"[deploy] updating existing {FUNCTION}")
        lam.update_function_code(FunctionName=FUNCTION, ZipFile=zip_bytes, Publish=True)
        waiter = lam.get_waiter("function_updated")
        waiter.wait(FunctionName=FUNCTION)
        lam.update_function_configuration(**cfg)
    else:
        print(f"[deploy] creating {FUNCTION} against {ROLE_ARN}")
        lam.create_function(**cfg, Code={"ZipFile": zip_bytes}, Publish=True)

    waiter = lam.get_waiter("function_active_v2")
    waiter.wait(FunctionName=FUNCTION)
    info = lam.get_function(FunctionName=FUNCTION)["Configuration"]
    print(f"[deploy] {FUNCTION} state={info['State']} last={info.get('LastUpdateStatus')}")
    print(f"[deploy] arn={info['FunctionArn']}")
    print("[deploy] env vars set: DATABASE_URL (hidden), S3_BUCKET=" + env["S3_BUCKET"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
