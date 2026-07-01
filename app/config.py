"""Application configuration.

Loads secrets from the gitignored .env. Never log full secret values.
Exposes both an async URL (app) and a sync URL (Alembic), both on the psycopg 3 driver.
"""

from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # .env also holds OPENAI/AWS keys used in later phases
    )

    # CRDB Cloud connection string, e.g.
    # postgresql://user:pass@host.cockroachlabs.cloud:26257/db?sslmode=verify-full
    database_url: str = Field(alias="DATABASE_URL")

    # Embedding dimensionality for beliefs.embedding (OpenAI text-embedding-3-small = 1536).
    embedding_dim: int = 1536

    @property
    def async_database_url(self) -> str:
        """URL for the async app engine (SQLAlchemy + psycopg 3 async)."""
        return _with_psycopg_scheme(self.database_url)

    @property
    def sync_database_url(self) -> str:
        """URL for Alembic (psycopg 3 sync). Same driver, sync mode."""
        return _with_psycopg_scheme(self.database_url)


def _with_psycopg_scheme(url: str) -> str:
    """Rewrite a plain postgres URL to the SQLAlchemy+psycopg3 driver, preserving query params.

    CRDB Cloud gives `postgresql://...?sslmode=verify-full`; SQLAlchemy needs an explicit
    driver: `postgresql+psycopg://...`. sslmode stays in the query string (libpq/psycopg reads it).
    """
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            url = "cockroachdb+psycopg://" + url[len(prefix) :]
            break
    return _ensure_system_ca(url)


def _ensure_system_ca(url: str) -> str:
    """For sslmode=verify-full with no explicit sslrootcert, point at certifi's CA bundle.

    libpq on Windows does not fall back to the OS trust store; it looks for a
    root.crt file that isn't there. The libpq bundled with psycopg[binary] here is
    v14, which lacks the `sslrootcert=system` special value (libpq 16+). So we use
    certifi's Mozilla CA bundle, which CRDB Cloud's publicly-trusted certs chain to.
    """
    import certifi

    parts = urlsplit(url)
    q = dict(parse_qsl(parts.query, keep_blank_values=True))
    if q.get("sslmode") == "verify-full" and "sslrootcert" not in q:
        q["sslrootcert"] = certifi.where()
    return urlunsplit(parts._replace(query=urlencode(q)))


@lru_cache
def get_settings() -> Settings:
    return Settings()
