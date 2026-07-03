"""boto3 client factory with Windows trust-store CA handling.

Same environment quirk as app/services/openai_client.py: AVG antivirus MITMs outbound
port 443 with a cert signed by AVG's local root CA — present in the Windows ROOT store but
NOT in certifi's bundle, which botocore uses by default. So botocore fails outbound AWS
calls with CERTIFICATE_VERIFY_FAILED. (CRDB is on 26257 and is not intercepted.)

Fix, mirroring the OpenAI fix: build a CA bundle = certifi's Mozilla roots + the Windows
ROOT store (which legitimately contains AVG's installed CA), and point botocore's `verify`
at it. This is still REAL TLS verification against the OS-trusted chain — never verify=False.

On Linux (e.g. the deployed Lambda) there is no AVG interception and ssl.enum_certificates
is unavailable, so we fall back to certifi's default bundle — this helper is a no-op there.
"""

from __future__ import annotations

import os
import ssl
import sys
import tempfile
from functools import lru_cache

import boto3
import certifi


@lru_cache(maxsize=1)
def ca_bundle_path() -> str:
    """Path to a CA bundle trusted for outbound AWS TLS on this machine.

    On Windows, returns certifi's roots + the Windows ROOT store merged into one PEM
    (so AVG's MITM CA is trusted). Elsewhere, returns certifi's bundle unchanged.
    Built once per process.
    """
    certifi_pem = certifi.where()
    if sys.platform != "win32":
        return certifi_pem

    pems: list[str] = [open(certifi_pem, encoding="utf-8").read()]
    try:
        for der, _enc, _trust in ssl.enum_certificates("ROOT"):
            pems.append(ssl.DER_cert_to_PEM_cert(der))
    except (OSError, AttributeError):
        # enum_certificates unavailable — fall back to certifi alone.
        return certifi_pem

    out = os.path.join(tempfile.gettempdir(), "lineage_aws_ca_bundle.pem")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(pems))
    return out


def session(region: str | None = None) -> boto3.Session:
    """A boto3 Session pinned to AWS_REGION (or the given region)."""
    return boto3.Session(region_name=region or os.environ.get("AWS_REGION"))


def client(service: str, region: str | None = None):
    """A boto3 client for `service` with the right CA bundle for this environment."""
    return session(region).client(service, verify=ca_bundle_path())
