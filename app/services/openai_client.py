"""Shared AsyncOpenAI client, TLS-configured for this environment.

Gotcha (Windows): AVG antivirus intercepts outbound HTTPS (port 443) with its own
CA-signed certificate (`avgMonFltProxy`). AVG's root CA is in the Windows system trust
store but NOT in certifi's Mozilla bundle, which is what httpx/openai use by default — so
the default client fails with CERTIFICATE_VERIFY_FAILED. (CRDB is unaffected: it talks TLS
on port 26257, which AVG doesn't intercept — see app/config.py for its own certifi handling.)

Fix: verify against the OS trust store. `create_default_context()` +
`load_default_certs(SERVER_AUTH)` pulls the Windows ROOT store (incl. AVG's CA); on
Linux/macOS it loads the platform store too, so this stays cross-platform.

The client is built lazily so merely importing OpenAI-using modules never forces a key to
exist (the hermetic staleness test must not need OpenAI).
"""

from __future__ import annotations

import ssl

import httpx
from openai import AsyncOpenAI

from app.config import get_settings

_client: AsyncOpenAI | None = None


def get_openai() -> AsyncOpenAI:
    global _client
    if _client is None:
        ctx = ssl.create_default_context()
        ctx.load_default_certs(ssl.Purpose.SERVER_AUTH)
        _client = AsyncOpenAI(
            api_key=get_settings().openai_api_key,
            http_client=httpx.AsyncClient(verify=ctx),
        )
    return _client
