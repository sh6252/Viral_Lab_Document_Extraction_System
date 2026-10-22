"""Offline cloud failure attribution, logging privacy and single-worker responsiveness."""
from concurrent.futures import ThreadPoolExecutor
import json
import logging
from threading import Event

import httpx
import pytest

from backend.core import cloud_diagnostics as diagnostic
from backend.services.demo_workflow import DemoError
from backend.services.extraction.gemini_adapter import GeminiExtractionAdapter
from tests.test_cloud_demo import gemini_cloud, login, setup_cloud, upload, BASE
from tests.test_demo_api import Adapter
from tests.test_gemini_adapter import _StatusError


@pytest.mark.parametrize("error,status,code,kind,provider_http", [
    (_StatusError(503, status="UNAVAILABLE"), 503, "PROVIDER_UNAVAILABLE", "provider_http", 503),
    (_StatusError(504, status="DEADLINE_EXCEEDED"), 504, "PROVIDER_TIMEOUT", "provider_http", 504),
    (httpx.ReadTimeout("SECRET_PROVIDER_BODY"), 504, "PROVIDER_TIMEOUT", "client_timeout", None),
    (httpx.ConnectTimeout("SECRET_PROVIDER_BODY"), 504, "PROVIDER_TIMEOUT", "client_timeout", None),
    (_StatusError(429), 429, "PROVIDER_RATE_LIMIT", "provider_http", 429),
])
def test_provider_status_and_client_timeout_are_distinct_and_safe(caplog, error, status, code, kind, provider_http):
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    client, adapter, _, _, sdk = gemini_cloud(errors=[error])
    login(client)
    response = upload(client)
    detail = response.json()["diagnostic"]
    assert response.status_code == status and response.json()["failure"]["code"] == code
    assert detail["failure_kind"] == kind and detail["provider_http_status"] == provider_http
    assert detail["stage"] == "provider_request" and detail["run_id"] == response.json()["run_id"]
    if isinstance(error, httpx.ReadTimeout):
        assert detail["timeout_phase"] == "read"
    if isinstance(error, httpx.ConnectTimeout):
        assert detail["timeout_phase"] == "connect"
    if provider_http == 504:
        assert detail["timeout_phase"] == "provider_deadline"
    assert detail["request_id"] == response.headers["x-request-id"]
    assert detail["elapsed_ms"] >= 0 and len(sdk.calls) == 1
    assert "SECRET_TEST_KEY" not in caplog.text + response.text
    assert "SECRET_PROVIDER_BODY" not in caplog.text + response.text
    assert "synthetic-offline-key" not in caplog.text + response.text
    assert response.json()["review_token"] not in caplog.text
    assert "%PDF" not in caplog.text
    messages = [json.loads(r.message) for r in caplog.records if r.name == "uvicorn.error"]
    assert messages[-1]["event"] == "cloud_upload_finished"
    assert messages[-1]["stage"] == "provider_request"


@pytest.mark.parametrize("where,stage,code", [
    ("readiness", "database_readiness", "CLOUD_DATABASE_NOT_READY"),
    ("create", "database_create", "CLOUD_DATABASE_CREATE_FAILED"),
    ("complete", "database_complete", "CLOUD_DATABASE_COMPLETE_FAILED"),
])
def test_storage_failures_have_distinct_codes_and_never_log_raw_errors(caplog, where, stage, code):
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    ready = (lambda: (_ for _ in ()).throw(ValueError("SECRET_DSN_AND_SQL"))) if where == "readiness" else None
    client, adapter, repository, _ = setup_cloud(ready=ready)
    if where != "readiness":
        getattr(repository, where).side_effect = DemoError("BUSINESS_STORAGE_UNAVAILABLE", 503)
    login(client)
    response = upload(client)
    assert response.status_code == 503 and response.json()["error"]["code"] == code
    assert response.json()["diagnostic"]["stage"] == stage
    assert "SECRET_DSN_AND_SQL" not in caplog.text + response.text
    assert len(adapter.calls) == (1 if where == "complete" else 0)


def test_unexpected_backend_exception_is_not_presented_as_provider_failure(caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    client, _, _, _ = setup_cloud()
    def fail(*_):
        raise RuntimeError("SECRET_INTERNAL_CONTENT")
    monkeypatch.setattr(client.app.state.demo_workflow, "create", fail)
    login(client)
    response = upload(client)
    assert response.status_code == 503 and response.json()["error"]["code"] == "CLOUD_BACKEND_UNEXPECTED"
    assert "SECRET_INTERNAL_CONTENT" not in caplog.text + response.text


def test_single_event_loop_serves_health_and_rejects_second_upload_while_provider_waits():
    entered, released = Event(), Event()
    adapter = Adapter()
    original = adapter.extract
    def slow(request, document):
        entered.set()
        assert released.wait(4)
        return original(request, document)
    adapter.extract = slow
    client, _, _, _ = setup_cloud(adapter)
    with client, ThreadPoolExecutor(max_workers=2) as executor:
        login(client)
        future = executor.submit(upload, client)
        try:
            assert entered.wait(2)
            health = executor.submit(client.get, "/api/v1/health").result(timeout=1)
            assert health.status_code == 200
            assert executor.submit(upload, client).result(timeout=1).status_code == 429
        finally:
            released.set()
        assert future.result(timeout=2).status_code == 201


def test_two_files_isolate_failure_and_success_and_success_exports():
    client, _, _, saved, sdk = gemini_cloud()
    sdk.responses.insert(0, _StatusError(503))
    login(client)
    failed = upload(client).json()
    success = upload(client, b"%PDF-1.7 different synthetic file").json()
    assert failed["status"] == "failed" and success["status"] == "succeeded"
    assert failed["run_id"] != success["run_id"] and len(saved) == 2 and len(sdk.calls) == 2
    assert success["selected_form"]["finalized"] is False
    assert client.get(f"{BASE}/{success['run_id']}/export.xlsx").status_code == 200


def test_google_sdk_errors_expose_only_allowlisted_status_and_preserve_504():
    from google.genai.errors import APIError
    from backend.services.extraction.provider_errors import safe_provider_diagnostics
    error = APIError(504, {"error": {"code": 504, "status": "DEADLINE_EXCEEDED", "message": "SECRET_RAW_RESPONSE"}})
    safe = safe_provider_diagnostics(error)
    assert safe["http_status_code"] == 504 and safe["provider_status"] == "DEADLINE_EXCEEDED"
    assert "SECRET_RAW_RESPONSE" not in json.dumps(safe)


def test_cloud_sdk_timeout_is_milliseconds_and_retries_remain_bounded(monkeypatch):
    from unittest.mock import Mock
    from google import genai
    from backend.cloud_app import _one_attempt_client
    factory = Mock()
    monkeypatch.setattr(genai, "Client", factory)
    _one_attempt_client("synthetic-offline-key", 45)
    options = factory.call_args.kwargs["http_options"]
    assert options.timeout == 45000 and options.retry_options.attempts == 1


def test_client_initialization_failure_has_safe_specific_stage(caplog):
    from backend.core.config import AppConfig
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    def fail(*_):
        raise ValueError("SECRET_CLIENT_CONFIGURATION")
    adapter = GeminiExtractionAdapter(config=AppConfig(ai_outbound_enabled=True, allow_external_phi=True,
        approved_ai_providers=("gemini",), gemini_api_key="synthetic-offline-key"), client_factory=fail,
        max_retries=0, observer=diagnostic.observe_provider)
    client, _, _, _ = setup_cloud(adapter)
    login(client)
    response = upload(client)
    detail = response.json()["diagnostic"]
    assert detail["code"] == "CLOUD_PROVIDER_CLIENT_INIT_FAILED" and detail["stage"] == "provider_client"
    assert "SECRET_CLIENT_CONFIGURATION" not in response.text + caplog.text
