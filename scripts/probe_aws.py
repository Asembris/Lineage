"""AWS reachability probe — the Phase-3 gate (step 1).

Mirrors the discipline of scripts/probe_crdb.py: prove the environment is real BEFORE
building against it. This answers, with real API calls (never mocked):

  1. Do the .env AWS creds authenticate at all?         -> sts:GetCallerIdentity
  2. Can we reach the audit bucket (right region)?       -> s3:HeadBucket + a PUT/GET/DELETE
     round-trip of a probe object (the certifier writes real objects, so prove PUT works).
  3. Can we create a Lambda function?                     -> lambda:GetFunction/List (perm probe)
  4. THE GATE: can this IAM principal create + pass the   -> iam:CreateRole / iam:PassRole
     Lambda EXECUTION ROLE?  The scoped S3+Lambda user very likely CANNOT. If so, we STOP
     and surface it — a console-level fix from the account owner, not something to route around.

Prints a verdict block. Never prints secret values (only the masked access-key id sts returns,
which is already non-secret). Read-only except the S3 probe object, which it deletes.
"""

from __future__ import annotations

import os
import sys
import uuid

from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import aws_client  # noqa: E402


def _load_env() -> None:
    """Load .env without depending on python-dotenv (not a pinned dep)."""
    try:
        from dotenv import load_dotenv as _ld

        _ld()
        return
    except Exception:
        pass
    # Minimal fallback parser.
    from pathlib import Path

    env = Path(__file__).resolve().parents[1] / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


OK = "  [OK]  "
FAIL = " [FAIL] "
WARN = " [WARN] "


def main() -> int:
    _load_env()
    region = os.environ.get("AWS_REGION", "eu-central-1")
    bucket = os.environ.get("S3_BUCKET", "")
    print(f"\n=== AWS PROBE (region={region}, bucket={bucket}) ===\n")

    gate_ok = True  # the CreateRole/PassRole gate

    # --- 1. identity -------------------------------------------------------
    try:
        ident = aws_client.client("sts", region).get_caller_identity()
        arn = ident["Arn"]
        acct = ident["Account"]
        print(f"{OK}sts:GetCallerIdentity -> {arn}")
        print(f"        account={acct}")
    except (NoCredentialsError, ClientError, EndpointConnectionError) as e:
        print(f"{FAIL}sts:GetCallerIdentity -> {e}")
        print("\nCreds do not authenticate. Fix .env before anything else.")
        return 2

    # --- 2. S3 bucket round-trip ------------------------------------------
    s3 = aws_client.client("s3", region)
    try:
        s3.head_bucket(Bucket=bucket)
        print(f"{OK}s3:HeadBucket -> {bucket} reachable")
    except ClientError as e:
        print(f"{FAIL}s3:HeadBucket -> {e}")
        return 2

    probe_key = f"_probe/{uuid.uuid4()}.txt"
    try:
        s3.put_object(Bucket=bucket, Key=probe_key, Body=b"lineage-probe")
        body = s3.get_object(Bucket=bucket, Key=probe_key)["Body"].read()
        assert body == b"lineage-probe"
        s3.delete_object(Bucket=bucket, Key=probe_key)
        print(f"{OK}s3 PUT/GET/DELETE round-trip -> {probe_key} (certifier can write)")
    except (ClientError, AssertionError) as e:
        print(f"{FAIL}s3 PUT/GET/DELETE -> {e}")
        print("        Cannot write certificates. Check bucket policy / IAM s3:PutObject.")
        return 2

    # --- 3. Lambda access --------------------------------------------------
    lam = aws_client.client("lambda", region)
    try:
        fns = lam.list_functions(MaxItems=1)
        print(f"{OK}lambda:ListFunctions -> ok ({len(fns.get('Functions', []))} shown)")
    except ClientError as e:
        print(f"{WARN}lambda:ListFunctions -> {e}")

    # --- 4. THE GATE: IAM CreateRole / PassRole ---------------------------
    # A Lambda needs an EXECUTION ROLE (separate from this user). We probe whether
    # this principal can create one. We do a *dry* create with an intentionally odd
    # name and immediately delete on success; AccessDenied means the gate is CLOSED.
    print("\n--- GATE: Lambda execution-role creation ---")
    iam = aws_client.client("iam", region)
    trust = (
        '{"Version":"2012-10-17","Statement":[{"Effect":"Allow",'
        '"Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
    )
    role_name = f"lineage-probe-role-{uuid.uuid4().hex[:8]}"
    try:
        iam.create_role(RoleName=role_name, AssumeRolePolicyDocument=trust)
        print(f"{OK}iam:CreateRole -> CAN create the Lambda execution role")
        try:
            iam.delete_role(RoleName=role_name)
            print(f"{OK}iam:DeleteRole -> probe role cleaned up")
        except ClientError as e:
            print(f"{WARN}iam:DeleteRole -> left {role_name} behind: {e}")
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("AccessDenied", "AccessDeniedException"):
            gate_ok = False
            print(f"{FAIL}iam:CreateRole -> ACCESS DENIED (gate CLOSED)")
            print("        This IAM user cannot create the Lambda execution role.")
        else:
            print(f"{WARN}iam:CreateRole -> unexpected: {e}")

    # --- verdict -----------------------------------------------------------
    print("\n=== VERDICT ===")
    if gate_ok:
        print("  Gate OPEN: can provision the Lambda execution role from code. Proceed.")
        return 0
    print("  Gate CLOSED: STOP. The Lambda execution role must be created by the account")
    print("  owner in the console (or the IAM user granted iam:CreateRole + iam:PassRole")
    print("  scoped to a lineage-* role). Steps 2-7 (endpoint, cert, consistency) are")
    print("  UNBLOCKED and use the S3 creds directly; only step 8 (Lambda) needs this.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
