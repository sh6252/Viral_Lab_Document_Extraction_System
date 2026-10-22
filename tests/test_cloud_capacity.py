"""Offline admission diagnostics; no database, provider or network connections."""
from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
import json
import logging
from unittest.mock import MagicMock, Mock

import pytest
from sqlalchemy.exc import OperationalError, TimeoutError as PoolTimeout

from backend.core.cloud_config import CloudConfig
from backend.persistence.business_repository import BusinessRepository
from backend.services.demo_workflow import DemoError, DemoRun
from tests.test_cloud_demo import ORIGIN, PASSWORD_HASH, PDF, login, setup_cloud, upload
from tests.test_demo_api import Adapter
from backend.services.extraction.errors import ProviderUnavailable


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Capacity regressions must never open a database or external network connection")
    monkeypatch.setattr("backend.cloud_app.create_cloud_engine", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)


def repository(*, count=0, size=0, limits=(30, 32 * 1024 * 1024), failure=None):
    """Fake SQL results test application lifecycle, not PostgreSQL server behavior."""
    engine, conn = Mock(), MagicMock()
    engine.dialect.name = "postgresql"
    lifecycle = {"active": 0, "commits": 0, "rollbacks": 0, "closes": 0}

    @contextmanager
    def begin():
        lifecycle["active"] += 1
        try:
            yield conn
        except BaseException:
            lifecycle["rollbacks"] += 1
            raise
        else:
            lifecycle["commits"] += 1
        finally:
            lifecycle["active"] -= 1
            lifecycle["closes"] += 1

    engine.begin.side_effect = begin
    conn.execute.return_value.one.return_value = (count, size)
    if failure:
        conn.execute.side_effect = failure
    return BusinessRepository(engine, capacity_limits=limits), conn, lifecycle


def run():
    return DemoRun("synthetic-run", "PRIVATE_REVIEW_TOKEN", PDF, "application/pdf", sha256(PDF).hexdigest(),
                   0, "synthetic-prompt", "synthetic-pipeline")


@pytest.mark.parametrize("count,size,document_limit,byte_limit", [
    (30, 0, True, False), (0, 32 * 1024 * 1024, False, True),
    (30, 32 * 1024 * 1024, True, True),
])
def test_capacity_is_an_application_rejection_before_insert_or_provider(caplog, count, size, document_limit, byte_limit):
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    repo, conn, lifecycle = repository(count=count, size=size)
    client, adapter, _, _ = setup_cloud()
    client.app.state.demo_workflow.repository = repo
    login(client)
    response = upload(client)
    assert response.status_code == 503 and response.json()["error"]["code"] == "DEMO_CAPACITY_REACHED"
    detail = response.json()["diagnostic"]
    assert detail["stage"] == "database_create" and detail["failure_kind"] == "application_capacity"
    assert detail["provider_attempt_count"] == 0 and not adapter.calls
    assert conn.execute.call_count == 2  # Advisory lock + aggregate read, never INSERT.
    assert lifecycle == {"active": 0, "commits": 0, "rollbacks": 1, "closes": 1}
    records = [json.loads(record.message) for record in caplog.records if record.message.startswith('{')]
    event = next(record for record in records if record.get("event") == "business_capacity_rejected")
    assert event["document_limit_reached"] is document_limit and event["source_byte_limit_reached"] is byte_limit
    assert event["document_count"] == count and event["source_bytes"] == size
    assert set(event) == {"event", "run_id", "document_limit_reached", "source_byte_limit_reached",
                          "document_count", "source_bytes", "incoming_source_bytes", "max_documents", "max_source_bytes"}
    assert "PRIVATE_REVIEW_TOKEN" not in caplog.text and "%PDF" not in caplog.text


def test_exact_source_byte_limit_is_allowed_and_aggregate_counts_every_status():
    repo, conn, lifecycle = repository(count=29, size=32 * 1024 * 1024 - len(PDF))
    repo.create(run())
    assert conn.execute.call_count == 3 and lifecycle["commits"] == 1 and lifecycle["active"] == 0
    query = str(conn.execute.call_args_list[1].args[0]).lower()
    assert "business_documents" in query and "count(" in query and "octet_length" in query
    assert "where" not in query  # Failed, completed and processing documents all consume admission capacity.


def test_raising_a_bounded_limit_admits_without_deleting_or_updating_existing_documents():
    repo, conn, lifecycle = repository(count=30, size=32 * 1024 * 1024, limits=(100, 64 * 1024 * 1024))
    repo.create(run())
    statements = [str(call.args[0]).lower() for call in conn.execute.call_args_list]
    assert len(statements) == 3 and statements[-1].startswith("insert into business_documents")
    assert not any(statement.startswith(("delete", "update", "alter", "drop", "truncate")) for statement in statements)
    assert lifecycle["active"] == 0 and lifecycle["commits"] == 1


@pytest.mark.parametrize("state", ["53300", "08006", "53100", "42501", "pool"])
def test_postgresql_categories_and_pool_exhaustion_never_become_capacity(caplog, state):
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    original = Exception("PRIVATE_DSN_AND_DATABASE_MESSAGE")
    original.sqlstate = state
    failure = PoolTimeout("PRIVATE_POOL_MESSAGE") if state == "pool" else OperationalError(
        "PRIVATE_SQL", {"PRIVATE_PARAMETERS": "PRIVATE_DOCUMENT"}, original)
    repo, _, lifecycle = repository(failure=failure)
    client, adapter, _, _ = setup_cloud()
    client.app.state.demo_workflow.repository = repo
    login(client)
    response = upload(client)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "CLOUD_DATABASE_CREATE_FAILED"
    assert response.json()["diagnostic"]["failure_kind"] == "database" and not adapter.calls
    assert "PRIVATE" not in caplog.text + response.text
    assert lifecycle["active"] == 0 and lifecycle["rollbacks"] == lifecycle["closes"] == 1


@pytest.mark.parametrize("fails", [False, True])
def test_successful_and_failed_provider_calls_do_not_hold_database_transactions(fails):
    repo, _, lifecycle = repository()
    adapter = Adapter(error=ProviderUnavailable("PRIVATE_PROVIDER_BODY")) if fails else Adapter()
    client, adapter, _, _ = setup_cloud(adapter=adapter)
    client.app.state.demo_workflow.repository = repo
    created = []
    create = repo.create

    def remember(value):
        create(value)
        created.append(value)

    repo.create = remember
    # Exercise actual complete() context/transition while replacing only DB row decoding.
    repo._load = lambda conn, run_id, **kwargs: replace(created[0], expires_at=0)
    extract = adapter.extract

    def observed(*args, **kwargs):
        assert lifecycle["active"] == 0 and lifecycle["closes"] == 1
        return extract(*args, **kwargs)

    adapter.extract = observed
    login(client)
    response = upload(client)
    assert response.status_code == (503 if fails else 201)
    assert lifecycle == {"active": 0, "commits": 2, "rollbacks": 0, "closes": 2}


def test_completion_decode_failure_releases_connection():
    repo, _, lifecycle = repository()
    repo._load = Mock(side_effect=ValueError("PRIVATE_ROW"))
    with pytest.raises(DemoError, match="^BUSINESS_STORAGE_UNAVAILABLE$"):
        repo.complete(replace(run(), status="failed"))
    assert lifecycle["active"] == 0 and lifecycle["rollbacks"] == lifecycle["closes"] == 1


def environment(monkeypatch):
    for name, value in {"APP_DEPLOYMENT": "cloud", "DEMO_DATA_POLICY": "synthetic-only",
                        "DEMO_PUBLIC_ORIGIN": ORIGIN, "DEMO_PASSWORD_HASH": PASSWORD_HASH,
                        "DEMO_DATABASE_NAME": "submission_synthetic"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("DEMO_MAX_DOCUMENTS", raising=False)
    monkeypatch.delenv("DEMO_MAX_SOURCE_BYTES", raising=False)


def test_capacity_defaults_unchanged_and_manual_settings_are_bounded(monkeypatch):
    environment(monkeypatch)
    config = CloudConfig.from_env()
    assert (config.demo_max_documents, config.demo_max_source_bytes) == (30, 32 * 1024 * 1024)
    monkeypatch.setenv("DEMO_MAX_DOCUMENTS", "100")
    monkeypatch.setenv("DEMO_MAX_SOURCE_BYTES", "67108864")
    config = CloudConfig.from_env()
    assert (config.demo_max_documents, config.demo_max_source_bytes) == (100, 64 * 1024 * 1024)


@pytest.mark.parametrize("name,bad,code", [
    ("DEMO_MAX_DOCUMENTS", value, "DEMO_MAX_DOCUMENTS_MUST_BE_INTEGER_1_TO_200")
    for value in ["", "PRIVATE_BAD_VALUE", "0", "-1", "201", "1.5"]
] + [
    ("DEMO_MAX_SOURCE_BYTES", value, "DEMO_MAX_SOURCE_BYTES_MUST_BE_INTEGER_1_TO_134217728")
    for value in ["", "PRIVATE_BAD_VALUE", "0", "-1", "134217729", "1.5"]
])
def test_invalid_capacity_environment_fails_closed_without_echoing_values(monkeypatch, name, bad, code):
    from backend.cloud_app import create_cloud_app
    environment(monkeypatch)
    monkeypatch.setenv(name, bad)
    with pytest.raises(ValueError, match=f"^{code}$"):
        create_cloud_app()


@pytest.mark.parametrize("kwargs", [
    {"demo_max_documents": value} for value in [0, 201, True, 30.5]
] + [{"demo_max_source_bytes": value} for value in [0, 134217729, True, 100.5]])
def test_direct_configuration_cannot_bypass_capacity_bounds(kwargs):
    with pytest.raises(ValueError, match="^DEMO_MAX_"):
        CloudConfig(ORIGIN, PASSWORD_HASH, "submission_synthetic", **kwargs)
