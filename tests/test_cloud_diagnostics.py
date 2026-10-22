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
from tests.test_gemini_adapter import _StatusError, _FakeResponse, _provider_payload


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
        assert detail["provider_status"] == "DEADLINE_EXCEEDED"
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


@pytest.mark.parametrize("timeout", [30, 90, 120])
def test_cloud_sdk_timeout_is_milliseconds_and_retries_remain_bounded(monkeypatch, timeout):
    from unittest.mock import Mock
    from google import genai
    from backend.cloud_app import _one_attempt_client
    factory = Mock()
    monkeypatch.setattr(genai, "Client", factory)
    _one_attempt_client("synthetic-offline-key", timeout)
    options = factory.call_args.kwargs["http_options"]
    assert options.timeout == timeout * 1000 and options.retry_options.attempts == 1


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


def unavailable():
    from google.genai.errors import APIError
    return APIError(503, {"error": {"code": 503, "status": "UNAVAILABLE", "message": "SECRET_PROVIDER_BODY"}})


@pytest.mark.parametrize("failures", [0, 1, 2, 3], ids=["success-once", "503-success", "503-503-success", "503-exhausted"])
def test_cloud_unavailable_retries_same_document_only_and_logs_final_attempts(monkeypatch, caplog, failures):
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    monkeypatch.setattr("backend.services.extraction.gemini_adapter.random.uniform", lambda low, high: (low + high) / 2)
    delays = []
    client, _, repository, saved, sdk = gemini_cloud(errors=[unavailable() for _ in range(failures)] +
        [_FakeResponse(_provider_payload())], retry_unavailable_only=True, sleep=delays.append)
    login(client)
    response = upload(client)
    attempts = min(failures + 1, 3)
    assert len(sdk.calls) == attempts and delays == pytest.approx([3, 8][:attempts - 1])
    assert response.status_code == (503 if failures == 3 else 201)
    assert repository.create.call_count == repository.complete.call_count == len(saved) == 1
    assert repository.create.call_args.args[0].run_id == repository.complete.call_args.args[0].run_id == response.json()["run_id"]
    assert all(call["model"] == "gemini-3.8-flash" for call in sdk.calls)
    if failures == 3:
        detail = response.json()["diagnostic"]
        assert detail["provider_attempt_count"] == 3 and detail["provider_http_status"] == 503
        assert detail["provider_status"] == "UNAVAILABLE" and detail["stage"] == "provider_request"
        assert detail["code"] == "PROVIDER_UNAVAILABLE" and response.json()["failure"]["retryable"] is True
        assert response.json()["selected_form"] is None
    else:
        assert response.json()["selected_form"]["finalized"] is False
    messages = [json.loads(record.message) for record in caplog.records if record.name == "uvicorn.error"]
    assert [message["provider_attempt_count"] for message in messages if message["event"] == "cloud_provider_attempt"] == list(range(1, attempts + 1))
    assert messages[-1]["event"] == "cloud_upload_finished" and messages[-1]["provider_attempt_count"] == attempts
    assert messages[-1]["code"] == ("PROVIDER_UNAVAILABLE" if failures == 3 else None)
    assert "SECRET_PROVIDER_BODY" not in caplog.text + response.text and "%PDF" not in caplog.text


@pytest.mark.parametrize("error", [_StatusError(504, status="DEADLINE_EXCEEDED"), _StatusError(401), _StatusError(403),
    _StatusError(400), _StatusError(404), _StatusError(429), _StatusError(500), _StatusError(502),
    _StatusError(503), _StatusError(503, status="INTERNAL"), httpx.ReadTimeout("SECRET"), ConnectionError("SECRET")])
def test_cloud_retry_policy_never_retries_other_failures(error):
    delays = []
    client, _, repository, _, sdk = gemini_cloud(errors=[error, _FakeResponse(_provider_payload())],
        retry_unavailable_only=True, sleep=delays.append)
    login(client)
    response = upload(client)
    assert response.status_code != 201 and len(sdk.calls) == 1 and delays == []
    assert repository.create.call_count == repository.complete.call_count == 1
    assert response.json()["diagnostic"]["provider_attempt_count"] == 1


def test_cloud_503_then_504_preserves_final_deadline_without_further_retry():
    delays = []
    client, _, _, _, sdk = gemini_cloud(errors=[unavailable(), _StatusError(504, status="DEADLINE_EXCEEDED"),
        _FakeResponse(_provider_payload())], retry_unavailable_only=True, sleep=delays.append)
    login(client)
    response = upload(client)
    detail = response.json()["diagnostic"]
    assert response.status_code == 504 and len(sdk.calls) == 2 and len(delays) == 1
    assert detail["provider_attempt_count"] == 2 and detail["provider_http_status"] == 504
    assert detail["provider_status"] == "DEADLINE_EXCEEDED" and detail["timeout_phase"] == "provider_deadline"


@pytest.mark.parametrize("where", ["readiness", "create", "complete"])
def test_cloud_retries_never_wrap_database_operations(where):
    client, _, repository, _, sdk = gemini_cloud(errors=[_FakeResponse(_provider_payload())], retry_unavailable_only=True,
        sleep=lambda _: pytest.fail("Database failure must not cause provider retry"))
    if where == "readiness":
        # Middleware captures the readiness callable when its stack is built.
        for middleware in client.app.user_middleware:
            if "readiness" in middleware.kwargs:
                middleware.kwargs["readiness"] = lambda: (_ for _ in ()).throw(ValueError("SECRET_DSN"))
    else:
        getattr(repository, where).side_effect = DemoError("BUSINESS_STORAGE_UNAVAILABLE", 503)
    login(client)
    response = upload(client)
    assert response.status_code == 503 and response.json()["diagnostic"]["stage"].startswith("database")
    assert len(sdk.calls) == (1 if where == "complete" else 0)
    assert repository.create.call_count == (0 if where == "readiness" else 1)
    assert repository.complete.call_count == (1 if where == "complete" else 0)


def test_next_document_succeeds_after_exhausted_retry_without_duplicate_runs():
    client, _, repository, saved, sdk = gemini_cloud(errors=[unavailable(), unavailable(), unavailable(),
        _FakeResponse(_provider_payload())], retry_unavailable_only=True)
    login(client)
    failed = upload(client)
    success = upload(client, b"%PDF-1.7 next synthetic document")
    assert failed.status_code == 503 and success.status_code == 201
    assert failed.json()["diagnostic"]["provider_attempt_count"] == 3
    assert len(sdk.calls) == 4 and repository.create.call_count == repository.complete.call_count == len(saved) == 2
    assert failed.json()["run_id"] != success.json()["run_id"]
    assert client.get(f"{BASE}/{success.json()['run_id']}/export.xlsx").status_code == 200


@pytest.mark.parametrize("fraction", [0, 1])
def test_cloud_backoff_jitter_has_bounded_3s_and_8s_delays(monkeypatch, fraction):
    monkeypatch.setattr("backend.services.extraction.gemini_adapter.random.uniform", lambda low, high: low + (high - low) * fraction)
    delays = []
    client, _, _, _, _ = gemini_cloud(errors=[unavailable(), unavailable(), unavailable()], retry_unavailable_only=True, sleep=delays.append)
    login(client)
    assert upload(client).status_code == 503
    assert delays == pytest.approx([2.4, 6.4] if fraction == 0 else [3.6, 9.6])


@pytest.mark.parametrize("retries", [-1, 3])
def test_unavailable_retry_mode_rejects_unbounded_budgets(retries):
    from backend.core.config import AppConfig
    with pytest.raises(ValueError, match="UNAVAILABLE_RETRIES_MUST_BE_0_TO_2"):
        GeminiExtractionAdapter(config=AppConfig(), max_retries=retries, retry_unavailable_only=True)


@pytest.mark.parametrize("status,expected_calls", [(503, 3), (504, 1)])
def test_installed_sdk_does_not_multiply_cloud_retry_attempts(monkeypatch, status, expected_calls):
    import socket
    from google import genai
    from backend.cloud_app import _one_attempt_client
    from backend.services.extraction.errors import ProviderTimeout, ProviderUnavailable
    from tests.test_gemini_adapter import _authorized_config, _request, _document
    original_client = genai.Client
    calls, delays = [], []
    def forbidden(*args, **kwargs):
        pytest.fail("Retry regression must never open a network connection")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    def respond(request):
        calls.append(request)
        assert request.extensions["timeout"]["read"] == 120
        return httpx.Response(status, json={"error": {"code": status,
            "status": "UNAVAILABLE" if status == 503 else "DEADLINE_EXCEEDED", "message": "SECRET_PROVIDER_BODY"}})
    with httpx.Client(transport=httpx.MockTransport(respond), trust_env=False) as transport:
        def factory(*, api_key, http_options):
            assert http_options.timeout == 120000 and http_options.retry_options.attempts == 1
            return original_client(vertexai=False, api_key=api_key,
                http_options=http_options.model_copy(update={"httpx_client": transport}))
        monkeypatch.setattr(genai, "Client", factory)
        with _one_attempt_client("synthetic-offline-key", 120) as client:
            adapter = GeminiExtractionAdapter(config=_authorized_config(), timeout_seconds=120,
                client_factory=lambda *_: client, max_retries=2, retry_unavailable_only=True, sleep=delays.append)
            with pytest.raises(ProviderUnavailable if status == 503 else ProviderTimeout):
                adapter.extract(_request(), _document())
    assert len(calls) == expected_calls and len(delays) == expected_calls - 1
