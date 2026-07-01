"""OpenAI embeddings — real belief/transaction vectors for CockroachDB vector search.

text-embedding-3-small returns 1536-dim vectors (matches beliefs.embedding). The client is
built lazily so importing this module never forces an API key to exist (the hermetic
staleness test imports app code but must not need OpenAI).
"""

from __future__ import annotations

from app.config import get_settings
from app.services.openai_client import get_openai


async def embed_text(text: str) -> list[float]:
    """Embed a single string with the configured OpenAI embedding model."""
    resp = await get_openai().embeddings.create(
        model=get_settings().embedding_model, input=text
    )
    return resp.data[0].embedding


def transaction_text(merchant: str, mcc: int, amount, account_age_months: float) -> str:
    """Canonical natural-language rendering of a transaction, for embedding + retrieval."""
    return (
        f"Card transaction at {merchant} (merchant category code {mcc}) "
        f"for ${amount}. Account age {account_age_months:.1f} months."
    )
