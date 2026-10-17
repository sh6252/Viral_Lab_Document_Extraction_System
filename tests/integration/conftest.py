"""Opt-in disposable PostgreSQL schemas. Never reset/drop an application database."""

import os
import re
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

from backend.persistence.db import create_review_engine
from backend.persistence.review_repository import ReviewSessionRepository


def validate_test_dsn(dsn: str, acknowledgement: str | None):
    try:
        url = make_url(dsn)
        if (acknowledgement != "yes" or url.drivername not in {"postgresql", "postgresql+psycopg"}
                or not re.fullmatch(r"review_test_[a-z0-9_]+", url.database or "")
                or url.host not in {"localhost", "127.0.0.1", "::1"}
                or url.query):
            raise ValueError
        return url
    except Exception:
        raise ValueError("EXPLICIT_LOCAL_DISPOSABLE_POSTGRESQL_TEST_DSN_REQUIRED") from None


@pytest.fixture
def pg_engine():
    dsn = os.environ.get("REVIEW_TEST_DSN")
    if not dsn:
        pytest.skip("REVIEW_TEST_DSN not configured; real PostgreSQL tests not run")
    try:
        url = validate_test_dsn(dsn, os.environ.get("REVIEW_TEST_ALLOW_DISPOSABLE"))
    except ValueError:
        pytest.fail("EXPLICIT_LOCAL_DISPOSABLE_POSTGRESQL_TEST_DSN_REQUIRED", pytrace=False)
    admin = create_review_engine(dsn)
    schema = "review_test_" + uuid4().hex
    scoped = None
    try:
        with admin.begin() as conn:
            assert conn.execute(text("SELECT current_database()")).scalar_one() == url.database
            conn.execute(CreateSchema(schema))
        scoped_url = url.update_query_dict({"options": "-csearch_path=" + schema})
        scoped = create_review_engine(scoped_url.render_as_string(hide_password=False))
        config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        with scoped.begin() as conn:
            config.attributes["connection"] = conn
            command.upgrade(config, "head")
        yield scoped
    finally:
        if scoped is not None:
            scoped.dispose()
        # Only this freshly generated schema in the explicitly acknowledged test DB.
        assert re.fullmatch(r"review_test_[0-9a-f]{32}", schema)
        with admin.begin() as conn:
            conn.execute(DropSchema(schema, cascade=True, if_exists=True))
        admin.dispose()


@pytest.fixture
def repository(pg_engine):
    return ReviewSessionRepository(pg_engine)
