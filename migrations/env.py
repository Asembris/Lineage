"""Alembic environment — runs migrations on the SYNC cockroachdb+psycopg dialect.

Sync (not async) on purpose: simpler, and it sidesteps the Windows selector-event-loop
requirement entirely (that only bites async psycopg). The URL comes from app.config so
secrets stay in .env.
"""

from logging.config import fileConfig

from sqlalchemy import create_engine, pool

from alembic import context

# Import models so target_metadata sees every table.
from app.config import get_settings
from app.db import Base
from app import models  # noqa: F401  (registers tables on Base.metadata)

config = context.config

# NOTE: we do NOT push the URL through config.set_main_option — its configparser
# %-interpolation chokes on the percent-encoded sslrootcert path in the query string.
# Build the engine straight from settings instead. Keeps secrets out of the ini too.
DB_URL = get_settings().sync_database_url

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=DB_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(DB_URL, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
