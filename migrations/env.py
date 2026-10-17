"""Explicit connection/DSN only; no startup imports or embedded credentials."""

import os

from alembic import context

from backend.persistence.db import create_review_engine


def run(connection):
    if connection.dialect.name != "postgresql":
        raise RuntimeError("POSTGRESQL_REQUIRED")
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("EXPLICIT_ONLINE_MIGRATION_CONNECTION_REQUIRED")
connection = context.config.attributes.get("connection")
if connection is not None:
    run(connection)
else:
    dsn = os.environ.get("REVIEW_DATABASE_DSN")
    if not dsn:
        raise RuntimeError("EXPLICIT_REVIEW_DATABASE_DSN_REQUIRED")
    engine = create_review_engine(dsn)
    try:
        with engine.connect() as connection:
            run(connection)
    except Exception:
        raise RuntimeError("REVIEW_MIGRATION_FAILED") from None
    finally:
        engine.dispose()
