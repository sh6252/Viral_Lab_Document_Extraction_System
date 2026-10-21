"""Real isolated PostgreSQL through the authenticated cloud ASGI boundary."""

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text

from backend.cloud_app import CloudWorkflow, create_cloud_application
from backend.core.cloud_config import CloudConfig
from backend.core.config import AppConfig
from backend.persistence.business_repository import BusinessRepository
from backend.persistence.cloud_database import check_business_schema
from tests.test_cloud_demo import ORIGIN, PASSWORD_HASH, PDF, login, upload
from tests.test_demo_api import Adapter, BASE
from tests.test_excel_export import read_workbook
from tests.test_form_approval import clean_result, finalize_command

pytestmark = pytest.mark.postgresql


def readiness(engine):
    with engine.connect() as conn:
        database, schema = conn.execute(text("SELECT current_database(), current_schema()")).one()
    return lambda: check_business_schema(engine, database, schema=schema)


def cloud(engine, *, capacity=(100, 64 * 1024 * 1024)):
    config = CloudConfig(ORIGIN, PASSWORD_HASH, "submission_synthetic")
    provider = Adapter(result=clean_result())
    workflow = CloudWorkflow(AppConfig(max_upload_bytes=5 * 1024 * 1024), provider,
        BusinessRepository(engine, capacity_limits=capacity))
    app = create_cloud_application(config, workflow, readiness(engine))
    client = TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN, "X-Demo-Request": "1"})
    return client, provider


def test_cloud_real_postgres_approval_and_reopen_after_new_application(pg_engine):
    client, provider = cloud(pg_engine)
    assert client.get("/api/v1/ready").status_code == 200
    assert upload(client).status_code == 401
    login(client)
    run = upload(client).json()
    assert run["status"] == "succeeded"
    approved = client.post(f"{BASE}/{run['run_id']}/forms/form-1/finalize", json=finalize_command(run))
    assert approved.status_code == 200 and approved.json()["selected_form"]["finalized"]
    before = client.get(f"{BASE}/{run['run_id']}/export.xlsx").content
    other, new_provider = cloud(pg_engine)
    other.cookies.update(client.cookies)
    assert other.get(f"{BASE}/{run['run_id']}").status_code == 401  # Restarts revoke login, not records.
    login(other)
    reopened = other.get(f"{BASE}/{run['run_id']}")
    assert reopened.status_code == 200 and reopened.json()["selected_form"]["finalized"]
    assert other.get(f"{BASE}/{run['run_id']}/source").content == PDF
    assert other.get(f"{BASE}/{run['run_id']}/export.xlsx").content == before
    assert len(read_workbook(before)["Forms"][1]) == 2
    assert other.get("/api/v1/business-records").json()["total"] == 1
    assert len(provider.calls) == 1 and not new_provider.calls


@pytest.mark.parametrize("ddl", [
    "ALTER TABLE business_review_events DROP CONSTRAINT ck_business_event_field",
    "ALTER TABLE business_records DROP CONSTRAINT ck_business_record_revision; ALTER TABLE business_records ADD CONSTRAINT ck_business_record_revision CHECK (true)",
    "ALTER TABLE business_review_events DISABLE TRIGGER business_events_append_only",
    "ALTER TABLE business_review_events DROP CONSTRAINT fk_business_event_record",
    "CREATE OR REPLACE FUNCTION business_events_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$",
    "UPDATE alembic_version SET version_num='0002_business_persistence'",
])
def test_schema_revision_critical_checks_and_trigger_body_gate_every_write(pg_engine, ddl):
    client, provider = cloud(pg_engine)
    login(client)
    assert client.get("/api/v1/ready").status_code == 200
    with pg_engine.begin() as conn:
        conn.execute(text(ddl))
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/ready").status_code == 503
    assert upload(client).status_code == 503
    assert not provider.calls
    with pg_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM business_documents")).scalar_one() == 0


@pytest.mark.parametrize("capacity", [(1, 10000), (100, len(PDF))])
def test_database_capacity_is_enforced_before_second_provider_call(pg_engine, capacity):
    client, provider = cloud(pg_engine, capacity=capacity)
    login(client)
    assert upload(client).status_code == 201
    assert upload(client).status_code == 503
    assert len(provider.calls) == 1
    with pg_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM business_documents")).scalar_one() == 1
