"""Offline codec, storage-boundary and fail-closed launcher checks; no database."""

from dataclasses import replace
import json
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

from backend.core.config import AppConfig
from backend.main import create_app
from backend.persistence import business_codec as codec
from backend.persistence.business_repository import BusinessRepository
from backend.services.business_review import BusinessReviewResult
from backend.services.demo_workflow import DemoError
from backend.services.durable_workflow import DurableWorkflow
from backend.services.excel_export import ProcessingFailure
from backend.tools import durable_demo
from tests.test_business_review import complete
from tests.test_demo_api import Adapter, BASE, PDF, setup, upload


def test_codec_preserves_complete_exact_review_and_audit():
    value = complete()
    encoded = codec.encode(value)
    decoded = codec.decode(encoded, BusinessReviewResult)
    assert decoded == value
    assert codec.encode(decoded) == encoded
    assert codec.digest(encoded) == codec.digest(codec.encode(value))
    identity = next(f for f in decoded.record.fields if f.field_name == "israeli_id")
    assert identity.original.values[0].raw == "000123" and identity.original.values[0].normalized == "123"
    assert len(decoded.audit) == 7


@pytest.mark.parametrize("mutation", ["version", "numeric_raw", "extra", "approved_without_event", "changed_event"])
def test_storage_codec_rejects_corruption_and_never_repairs(mutation):
    encoded = json.loads(codec.encode(complete()))
    if mutation == "version":
        encoded["version"] = "unknown"
    elif mutation == "numeric_raw":
        encoded["payload"]["record"]["fields"][2]["original"]["values"][0]["raw"] = 123
    elif mutation == "extra":
        encoded["payload"]["unexpected"] = "ignored data must fail"
    elif mutation == "approved_without_event":
        encoded["payload"]["audit"] = []
    else:
        encoded["payload"]["audit"][0]["action"]["reviewed_values"] = ["changed"]
    with pytest.raises(Exception):
        codec.decode(json.dumps(encoded, ensure_ascii=False, sort_keys=True, separators=(",", ":")), BusinessReviewResult)


def test_failure_codec_roundtrip_is_separate_from_business_record():
    value = ProcessingFailure("synthetic-doc", "synthetic-attempt", "PROVIDER_TIMEOUT", "live",
                              source_sha256="a" * 64, provider="synthetic", latency_seconds=120.0)
    assert codec.decode(codec.encode(value), ProcessingFailure) == value


def test_database_insert_failure_prevents_provider_call_and_is_not_hidden():
    repository = Mock(spec=BusinessRepository)
    repository.create.side_effect = DemoError("BUSINESS_STORAGE_UNAVAILABLE", 503)
    provider = Adapter()
    workflow = DurableWorkflow(AppConfig(), provider, repository)
    with pytest.raises(DemoError, match="BUSINESS_STORAGE_UNAVAILABLE"):
        workflow.create(PDF, "application/pdf", True, "he-IL")
    assert not provider.calls
    repository.complete.assert_not_called()


def test_durable_upload_uses_repository_and_read_does_not_use_ttl_or_provider():
    repository = Mock(spec=BusinessRepository)
    repository.create.side_effect = lambda run: setattr(repository, "saved_run", replace(run, expires_at=0))
    repository.complete.side_effect = lambda run: setattr(repository, "saved_run", replace(run, expires_at=0))
    repository.get.side_effect = lambda run_id: repository.saved_run
    provider = Adapter()
    workflow = DurableWorkflow(AppConfig(), provider, repository)
    run = workflow.create(PDF, "application/pdf", True, "he-IL")
    assert len(provider.calls) == 1 and run["status"] == "succeeded"
    assert not workflow._runs
    fresh = DurableWorkflow(AppConfig(), None, repository)
    fresh.clock = lambda: 10**12
    assert fresh.get(run["run_id"]) == run
    assert fresh.source(run["run_id"]) == (PDF, "application/pdf")
    assert len(provider.calls) == 1


def test_new_source_and_audit_endpoints_keep_existing_local_access_guards():
    client, _, provider = setup()
    run = upload(client).json()
    for endpoint in ("source", "audit"):
        path = f"{BASE}/{run['run_id']}/{endpoint}"
        assert client.get(path).status_code == 200
        assert client.get(path, headers={"Origin": "https://foreign.invalid"}).status_code == 403
        assert client.get(path).headers["cache-control"] == "no-store"
    assert len(provider.calls) == 1
    audit = client.get(f"{BASE}/{run['run_id']}/audit").json()
    assert all(not form["audit"] for form in audit["forms"])
    assert client.get(f"{BASE}/{run['run_id']}/audit", params={"form_id": "unknown"}).status_code == 404


def test_launcher_requires_explicit_database_and_redacts_startup_failure(monkeypatch):
    monkeypatch.delenv("REVIEW_DATABASE_DSN", raising=False)
    with pytest.raises(RuntimeError, match="EXPLICIT_REVIEW_DATABASE_DSN_REQUIRED"):
        durable_demo.create_durable_demo_app()
    monkeypatch.setenv("REVIEW_DATABASE_DSN", "private-invalid-dsn")
    def fail(dsn):
        raise ValueError(dsn)
    monkeypatch.setattr(durable_demo, "create_review_engine", fail)
    with pytest.raises(RuntimeError) as error:
        durable_demo.create_durable_demo_app()
    assert str(error.value) == "BUSINESS_DATABASE_STARTUP_FAILED"


def test_launcher_checks_schema_before_building_adapter_and_disposes_failure(monkeypatch):
    engine = Mock()
    repository = Mock()
    repository.check_ready.side_effect = DemoError("PRIVATE DATABASE DETAIL", 503)
    monkeypatch.setenv("REVIEW_DATABASE_DSN", "private")
    monkeypatch.setattr(durable_demo, "create_review_engine", lambda dsn: engine)
    monkeypatch.setattr(durable_demo, "BusinessRepository", lambda value: repository)
    with pytest.raises(RuntimeError, match="BUSINESS_DATABASE_STARTUP_FAILED"):
        durable_demo.create_durable_demo_app()
    engine.dispose.assert_called_once()


def test_launcher_preserves_existing_configuration_and_closes_engine(monkeypatch):
    from backend.tools import local_demo

    config = AppConfig(gemini_structured_outputs_experimental=True)
    adapter, engine, repository = Adapter(), Mock(), Mock()
    monkeypatch.setenv("REVIEW_DATABASE_DSN", "private")
    monkeypatch.setattr(durable_demo, "create_review_engine", lambda dsn: engine)
    monkeypatch.setattr(durable_demo, "BusinessRepository", lambda value: repository)
    monkeypatch.setattr(local_demo, "create_local_demo_app", lambda: create_app(config, adapter,
                        demo_workflow=Mock()))
    app = durable_demo.create_durable_demo_app()
    assert app.state.config == config and app.state.extraction_adapter is adapter
    assert isinstance(app.state.demo_workflow, DurableWorkflow)
    repository.check_ready.assert_called_once()
    with TestClient(app):
        pass
    engine.dispose.assert_called_once()
