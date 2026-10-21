"""Preserve existing v1 business data during 0001 -> 0002 -> 0003 upgrades."""

import os
from pathlib import Path
import re
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.operations import Operations
import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.schema import CreateSchema, DropSchema

from backend.persistence.business_models import documents, events, records
from backend.persistence.db import create_review_engine
from tests.integration.conftest import validate_test_dsn
from tests.integration.test_business_repository import all_actions, client_for, two_form_pdf
from tests.test_demo_api import Adapter, BASE, post, upload
from tests.test_excel_export import read_workbook
from tests.test_form_approval import clean_result, http_finalize

pytestmark = pytest.mark.postgresql


@pytest.fixture
def empty_disposable_engine():
    dsn = os.environ.get("REVIEW_TEST_DSN")
    if not dsn:
        pytest.skip("REVIEW_TEST_DSN not configured; real PostgreSQL tests not run")
    url = validate_test_dsn(dsn, os.environ.get("REVIEW_TEST_ALLOW_DISPOSABLE"))
    # This migration validation is intentionally narrower than the generic fixture.
    if (url.database, url.host, url.port) != ("review_test_viral_lab", "127.0.0.1", 5433):
        pytest.fail("EXACT_LOCAL_DISPOSABLE_TARGET_REQUIRED", pytrace=False)
    admin = create_review_engine(dsn)
    schema = "review_test_" + uuid4().hex
    scoped = None
    created = False
    try:
        with admin.begin() as conn:
            assert conn.execute(text("SELECT current_database()")).scalar_one() == "review_test_viral_lab"
            conn.execute(CreateSchema(schema))
            created = True
        scoped = create_review_engine(url.update_query_dict({"options": "-csearch_path=" + schema})
                                      .render_as_string(hide_password=False))
        yield scoped
    finally:
        if scoped is not None:
            scoped.dispose()
        if created:
            assert re.fullmatch(r"review_test_[0-9a-f]{32}", schema)
            with admin.begin() as conn:
                assert conn.execute(text("SELECT current_database()")).scalar_one() == "review_test_viral_lab"
                conn.execute(DropSchema(schema, cascade=True))
        admin.dispose()


def migrate(engine, revision, *, downgrade=False):
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        (command.downgrade if downgrade else command.upgrade)(config, revision)


def capture(engine):
    with engine.connect() as conn:
        rows = {table.name: tuple(tuple(row) for row in conn.execute(select(table).order_by(*table.primary_key)))
                for table in (documents, records, events)}
        triggers = tuple(conn.execute(text("SELECT tgname, pg_get_triggerdef(oid) FROM pg_trigger "
            "WHERE NOT tgisinternal AND tgrelid IN ('business_documents'::regclass, "
            "'business_records'::regclass, 'business_review_events'::regclass) ORDER BY tgname")))
        return rows, triggers


def constraint_and_revision(engine):
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        constraint = conn.execute(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid='business_review_events'::regclass AND conname='ck_business_event_field'")).scalar_one()
        return constraint, version


@pytest.mark.parametrize("inject_failure", [False, True], ids=["existing-v1-upgrade", "ddl-rollback-then-upgrade"])
def test_existing_rows_hashes_events_and_guards_survive_upgrade(empty_disposable_engine, monkeypatch, inject_failure):
    engine = empty_disposable_engine
    migrate(engine, "0001_review_persistence")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0001_review_persistence"
    migrate(engine, "0002_business_persistence")
    old_constraint, version = constraint_and_revision(engine)
    assert version == "0002_business_persistence" and "__form__" not in old_constraint
    client, workflow, provider = client_for(engine, Adapter(clean_result()))
    with client:
        run = upload(client, content=two_form_pdf()).json()
        response = post(client, run, all_actions(run))
        assert response.status_code == 200
        original = workflow.repository.get(run["run_id"])
        assert original.results[0].record.revision == 7 and not original.results[0].finalized
        before = capture(engine)
        assert len(before[0]["business_review_events"]) == 7
        original_workbook = client.get(f"{BASE}/{run['run_id']}/export.xlsx").content

        if inject_failure:
            execute = Operations.execute
            def fail_add(self, statement, *args, **kwargs):
                if "ADD CONSTRAINT ck_business_event_field" in str(statement):
                    raise RuntimeError("INJECTED_CONSTRAINT_FAILURE")
                return execute(self, statement, *args, **kwargs)
            with monkeypatch.context() as context:
                context.setattr(Operations, "execute", fail_add)
                with pytest.raises(RuntimeError, match="INJECTED_CONSTRAINT_FAILURE"):
                    migrate(engine, "0003_form_approval")
            assert constraint_and_revision(engine) == (old_constraint, "0002_business_persistence")
            assert capture(engine) == before

        migrate(engine, "0003_form_approval")
        new_constraint, version = constraint_and_revision(engine)
        assert version == "0003_form_approval" and "__form__" in new_constraint
        assert capture(engine) == before  # All columns, source bytes, JSON/hash strings, timestamps and triggers.
        assert workflow.repository.get(run["run_id"]) == original
        assert client.get(f"{BASE}/{run['run_id']}/export.xlsx").content == original_workbook
        migrate(engine, "head")  # Already applied: no duplicate work or data changes.
        assert capture(engine) == before
        with pytest.raises(RuntimeError, match="FORM_APPROVAL_DOWNGRADE_REQUIRES_EXPLICIT_ARCHIVAL_PLAN"):
            migrate(engine, "0002_business_persistence", downgrade=True)
        assert capture(engine) == before
        assert constraint_and_revision(engine) == (new_constraint, "0003_form_approval")

        current = client.get(f"{BASE}/{run['run_id']}").json()
        finalized = http_finalize(client, current)
        assert finalized.status_code == 200 and finalized.json()["selected_form"]["revision"] == 8
        after = capture(engine)
        assert len(after[0]["business_review_events"]) == 8
        assert after[0]["business_documents"] == before[0]["business_documents"]
        assert after[1] == before[1]
        with engine.begin() as conn:
            with pytest.raises(SQLAlchemyError), conn.begin_nested():
                conn.execute(update(events).values(event_json="tamper"))
        assert capture(engine) == after
        workbook = client.get(f"{BASE}/{run['run_id']}/export.xlsx").content
        book = read_workbook(workbook)
        assert [row["commitment_numbers"] for row in book["Forms"][1]] == ["00001", "00002"]
        assert len(book["ReviewAudit"][1]) == 8
        assert len(provider.calls) == 1
    engine.dispose()
    fresh, reloaded, forbidden = client_for(engine, Adapter(error=AssertionError("NO_PROVIDER_ON_REOPEN")))
    with fresh:
        assert fresh.get(f"{BASE}/{run['run_id']}").json() == finalized.json()
        assert reloaded.repository.get(run["run_id"]).results[0].finalized
        assert fresh.get(f"{BASE}/{run['run_id']}/export.xlsx").content == workbook
        assert not forbidden.calls
