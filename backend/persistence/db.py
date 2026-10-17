"""Explicit, PostgreSQL-only connection construction. No import-time connections."""

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


def create_review_engine(dsn: str) -> Engine:
    try:
        url = make_url(dsn)
        if url.drivername not in {"postgresql", "postgresql+psycopg"}:
            raise ValueError
    except (ValueError, TypeError, ArgumentError):
        raise ValueError("POSTGRESQL_DSN_REQUIRED") from None
    return create_engine(url.set(drivername="postgresql+psycopg"),
                         echo=False, hide_parameters=True, pool_pre_ping=True)
