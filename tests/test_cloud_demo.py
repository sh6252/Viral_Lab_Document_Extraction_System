"""Public boundary regression; synthetic provider/repository only, no DB or network."""
from hashlib import sha256
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import Mock
from dataclasses import replace

from fastapi.testclient import TestClient
import pytest

from backend.cloud_app import CloudWorkflow, create_cloud_application
from backend.core.cloud_config import CloudConfig
from backend.core.config import AppConfig
from backend.core.cloud_diagnostics import observe_provider
from backend.core.demo_auth import COOKIE, DemoAuth, hash_password
from backend.persistence.business_repository import BusinessRepository
from backend.persistence.cloud_database import neon_url
from backend.services.extraction.errors import ProviderRateLimit, ProviderTimeout, ProviderUnavailable
from backend.services.extraction.gemini_adapter import GeminiExtractionAdapter
from tests.test_gemini_adapter import _FakeClient, _FakeResponse, _StatusError
from tests.test_demo_api import Adapter, BASE, command
from tests.test_excel_export import read_workbook
from tests.test_form_approval import clean_result, finalize_command

ORIGIN = "https://judges.example.test"
PASSWORD = "synthetic-invitation-password-only"
PDF = b"%PDF-1.7 synthetic test document"
PASSWORD_HASH = hash_password(PASSWORD)


@pytest.fixture(autouse=True)
def no_external_connections(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Cloud regression tests must never connect to Gemini or a database")
    monkeypatch.setattr("backend.cloud_app.create_cloud_engine", forbidden)
    monkeypatch.setattr("backend.cloud_app._one_attempt_client", forbidden)
    monkeypatch.setattr(GeminiExtractionAdapter, "_create_google_client", staticmethod(forbidden))


def setup_cloud(adapter=None, *, ready=None, auth=None, static_directory=None, origin=ORIGIN, app_config=None):
    config = CloudConfig(origin, PASSWORD_HASH, "submission_synthetic")
    repository = Mock(spec=BusinessRepository)
    saved = {}
    repository.create.side_effect = lambda run: saved.setdefault(run.run_id, run)
    repository.complete.side_effect = lambda run: saved.update({run.run_id: run})
    repository.get.side_effect = lambda key: saved[key]
    def transition(key, apply):
        saved[key] = apply(saved[key])
        return saved[key]
    repository.review_atomic.side_effect = transition
    repository.finalize_atomic.side_effect = transition
    repository.list_records.return_value = {"items": [], "total": 0, "offset": 0, "limit": 20}
    provider = adapter or Adapter(result=clean_result())
    workflow = CloudWorkflow(app_config or AppConfig(max_upload_bytes=5 * 1024 * 1024), provider, repository)
    readiness = ready or Mock()
    app = create_cloud_application(config, workflow, readiness, auth=auth, static_directory=static_directory)
    client = TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN, "X-Demo-Request": "1"})
    return client, provider, repository, saved


def login(client):
    response = client.post("/api/v1/auth/login", json={"password": PASSWORD})
    assert response.status_code == 200
    client.headers["X-Demo-CSRF"] = response.json()["csrf_token"]
    return response


def upload(client, content=PDF, *, mime="application/pdf", consent=True):
    return client.post(BASE, files={"file": ("synthetic", content, mime)},
                       data={"allow_external_processing": str(consent).lower()})


@pytest.mark.parametrize("method,path", [("get", "/api/v1/business-records"), ("post", BASE),
    ("get", BASE + "/x/source"), ("get", BASE + "/x/audit"), ("get", BASE + "/x/export.xlsx"),
    ("get", BASE + "/x"), ("post", BASE + "/x/forms/y/review"),
    ("post", BASE + "/x/forms/y/finalize"), ("post", "/api/v1/business-records/export.xlsx"),
    ("post", "/api/v1/extractions"), ("get", "/api/v1/review-sessions/x")])
def test_every_business_surface_denies_anonymous_before_body_or_database(method, path):
    client, provider, repository, _ = setup_cloud()
    response = getattr(client, method)(path)
    assert response.status_code == 401
    repository.get.assert_not_called()
    repository.create.assert_not_called()
    assert not provider.calls


def test_login_cookie_csrf_logout_and_forged_session():
    client, _, _, _ = setup_cloud()
    response = login(client)
    cookie = response.headers["set-cookie"]
    assert COOKIE in cookie and "HttpOnly" in cookie and "Secure" in cookie and "SameSite=strict" in cookie
    assert "Domain=" not in cookie
    assert PASSWORD not in response.text and client.cookies[COOKIE] not in response.text
    assert client.get("/api/v1/auth/session").status_code == 200
    assert client.post("/api/v1/auth/logout", headers={"X-Demo-CSRF": "wrong"}).status_code == 403
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.get("/api/v1/business-records").status_code == 401
    client.cookies.set(COOKIE, "forged-cookie")
    assert client.get("/api/v1/business-records").status_code == 401


def test_foreign_origins_missing_origin_bad_host_and_preflight_are_not_trusted():
    client, _, _, _ = setup_cloud()
    login(client)
    for origin in ("https://evil.test", "null", ORIGIN + ".evil", "http://judges.example.test"):
        response = client.get("/api/v1/business-records", headers={"Origin": origin})
        assert response.status_code == 403 and "access-control-allow-origin" not in response.headers
        assert client.post("/api/v1/auth/login", json={"password": PASSWORD}, headers={"Origin": origin}).status_code == 403
    del client.headers["Origin"]
    assert upload(client).status_code == 403
    client.headers["Origin"] = ORIGIN
    assert client.get("/api/v1/business-records", headers={"Host": "evil.test"}).status_code == 400
    good = client.options(BASE, headers={"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "X-Demo-CSRF,X-Demo-Request"})
    assert good.status_code == 200 and good.headers["access-control-allow-origin"] == ORIGIN
    assert client.options(BASE, headers={"Origin": "https://evil.test", "Access-Control-Request-Method": "POST"}).status_code == 403


def test_invalid_inputs_never_echo_password_and_docs_generic_extraction_disabled():
    client, _, _, _ = setup_cloud()
    assert client.post("/api/v1/auth/login", json={"password": PASSWORD, "unexpected": PASSWORD}).status_code == 422
    assert PASSWORD not in client.post("/api/v1/auth/login", json={"password": PASSWORD * 100}).text
    assert client.post("/api/v1/auth/login", json={"password": "wrong"}).status_code == 401
    login(client)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404
    assert client.post("/api/v1/extractions").status_code == 404


def test_expiry_revocation_rate_limit_and_restart_fail_closed():
    now = [100.0]
    auth = DemoAuth(PASSWORD_HASH, clock=lambda: now[0])
    client, _, _, _ = setup_cloud(auth=auth)
    login(client)
    now[0] += 3601
    assert client.get("/api/v1/auth/session").status_code == 401
    for _ in range(20):
        assert client.post("/api/v1/auth/login", json={"password": "incorrect"}).status_code == 401
    limited = client.post("/api/v1/auth/login", json={"password": PASSWORD})
    assert limited.status_code == 429 and limited.headers["retry-after"] == "300"
    now[0] += 301
    login(client)
    new_client, _, _, _ = setup_cloud()
    new_client.cookies.update(client.cookies)
    assert new_client.get("/api/v1/auth/session").status_code == 401


def test_stream_limits_and_readiness_prevent_provider_or_database_writes():
    client, provider, repository, _ = setup_cloud()
    login(client)
    response = client.post("/api/v1/business-records/export.xlsx", content=iter([b"x" * 70000, b"y" * 70000]),
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    assert upload(client, PDF * 200000).status_code == 413
    assert not provider.calls
    repository.create.assert_not_called()
    broken, provider, repository, _ = setup_cloud(ready=Mock(side_effect=RuntimeError("safe-code")))
    login(broken)
    assert broken.get("/api/v1/health").status_code == 200
    assert broken.get("/api/v1/ready").status_code == 503
    assert upload(broken).status_code == 503
    repository.create.assert_not_called()
    assert broken.post("/api/v1/auth/logout").status_code == 200


def test_authorized_upload_review_finalization_source_reopen_and_bulk_excel():
    client, provider, _, _ = setup_cloud()
    login(client)
    runs = []
    for _ in range(2):
        response = upload(client)
        assert response.status_code == 201
        run = response.json()
        assert run["selected_form"]["finalized"] is False
        runs.append(run)
    first = runs[0]
    assert client.get(f"{BASE}/{first['run_id']}/source").content == PDF
    assert client.get("/api/v1/business-records").status_code == 200
    reviewed = client.post(f"{BASE}/{first['run_id']}/forms/form-1/review", json=command(first)).json()
    final = client.post(f"{BASE}/{first['run_id']}/forms/form-1/finalize", json=finalize_command(reviewed))
    assert final.status_code == 200 and final.json()["selected_form"]["finalized"]
    first = final.json()
    reopened = client.get(f"{BASE}/{first['run_id']}").json()
    assert reopened["selected_form"]["finalized"]
    book = read_workbook(client.get(f"{BASE}/{first['run_id']}/export.xlsx").content)
    assert len(book["Forms"][1]) == 2
    selection = {"forms": [{"run_id": r["run_id"], "form_id": "form-1", "review_token": r["review_token"],
        "expected_revision": r["selected_form"]["revision"], "commitments": ["00001", "00002"]} for r in [first, runs[1]]]}
    bulk = client.post("/api/v1/business-records/export.xlsx", json=selection)
    assert bulk.status_code == 200 and len(read_workbook(bulk.content)["Forms"][1]) == 4
    assert len(provider.calls) == 2  # Reopen, review and exports never extract again.
    assert reopened["selected_form"]["automatic_acceptance"] is False


@pytest.mark.parametrize("exception,status", [(ProviderRateLimit("synthetic"), 429),
    (ProviderUnavailable("synthetic"), 503), (ProviderTimeout("synthetic"), 504)])
def test_provider_failure_http_status_preserved_without_fallback(exception, status):
    client, provider, _, saved = setup_cloud(Adapter(error=exception))
    login(client)
    response = upload(client)
    assert response.status_code == status
    assert response.json()["status"] == "failed" and response.json()["selected_form"] is None
    assert len(provider.calls) == 1 and all(run.status == "failed" for run in saved.values())


def test_static_build_served_from_same_origin_with_no_secret_files(tmp_path):
    (tmp_path / "index.html").write_text("<html>synthetic build</html>")
    client, _, _, _ = setup_cloud(static_directory=tmp_path)
    assert "synthetic build" in client.get("/").text
    assert client.get("/.env").status_code == 404
    assert "default-src 'self'" in client.get("/").headers["content-security-policy"]


@pytest.mark.parametrize("replace", [lambda s: s.replace("sslmode=require", "sslmode=disable"),
    lambda s: s.replace("channel_binding=require", "channel_binding=disable"),
    lambda s: s.replace("ep-demo.aws.neon.tech", "127.0.0.1"),
    lambda s: s.replace("ep-demo.aws.neon.tech", "ep-demo-pooler.aws.neon.tech"),
    lambda s: s.replace("submission_synthetic", "viral_lab"), lambda s: s + "&host=evil.test"])
def test_neon_dsn_fail_closed_without_connecting(replace):
    dsn = "postgresql://user:DO_NOT_PRINT@ep-demo.aws.neon.tech/submission_synthetic?sslmode=require&channel_binding=require"
    with pytest.raises(ValueError, match="DIRECT_NEON_SYNTHETIC_TLS_DSN_REQUIRED") as failure:
        neon_url(replace(dsn), "submission_synthetic")
    assert "DO_NOT_PRINT" not in str(failure.value)


def test_neon_driver_and_tls_parameters_preserved():
    url = neon_url("postgres://user:DO_NOT_PRINT@ep-demo.aws.neon.tech/submission_synthetic?sslmode=require&channel_binding=require", "submission_synthetic")
    assert url.drivername == "postgresql+psycopg" and url.query["channel_binding"] == "require"


@pytest.mark.parametrize("origin", ["http://example.test", "https://example.test/", "https://example.test?x=y",
    "https://user:password@example.test", "https://*.example.test", "https://example.test:0"])
def test_cloud_origin_must_be_an_exact_https_origin(origin):
    with pytest.raises(ValueError):
        CloudConfig(origin, PASSWORD_HASH, "submission_synthetic")


def test_cloud_environment_is_explicit_and_accepts_trusted_render_origin(monkeypatch):
    for name in ("APP_DEPLOYMENT", "DEMO_DATA_POLICY", "DEMO_PUBLIC_ORIGIN", "RENDER_EXTERNAL_URL"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match="EXPLICIT_CLOUD"):
        CloudConfig.from_env()
    monkeypatch.setenv("APP_DEPLOYMENT", "cloud")
    monkeypatch.setenv("DEMO_DATA_POLICY", "synthetic-only")
    monkeypatch.setenv("DEMO_PASSWORD_HASH", PASSWORD_HASH)
    monkeypatch.setenv("DEMO_DATABASE_NAME", "submission_synthetic")
    # Obsolete environment values cannot restrict otherwise valid uploads.
    monkeypatch.setenv("DEMO_ALLOWED_DOCUMENT_SHA256", "obsolete-and-ignored")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", ORIGIN)
    assert CloudConfig.from_env().origin == ORIGIN
    monkeypatch.setenv("DEMO_DATABASE_NAME", "viral_lab")
    with pytest.raises(ValueError, match="EXPLICIT_SYNTHETIC_DATABASE"):
        CloudConfig.from_env()


def test_only_one_upload_is_admitted_at_a_time_without_retrying_provider():
    entered, release = Event(), Event()
    provider = Adapter(result=clean_result())
    extract = provider.extract
    def blocked(request, document):
        entered.set()
        assert release.wait(10)
        return extract(request, document)
    provider.extract = blocked
    client, _, _, _ = setup_cloud(provider)
    login(client)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(upload, client)
            assert entered.wait(10)
            try:
                assert upload(client).status_code == 429
            finally:
                release.set()
            assert first.result().status_code == 201
    finally:
        release.set()
    assert len(provider.calls) == 1


def test_malformed_security_headers_are_denied_without_unhandled_exceptions():
    client, _, _, _ = setup_cloud()
    login(client)
    assert client.post("/api/v1/auth/logout", headers={"X-Demo-CSRF": b"\xff"}).status_code == 403
    response = client.post("/api/v1/business-records/export.xlsx", content=b"{}", headers={"Content-Length": "9" * 5000})
    assert response.status_code == 413


def gemini_cloud(*, config=None, errors=None, retry_unavailable_only=False, sleep=lambda _: None):
    config = config or AppConfig(ai_outbound_enabled=True, allow_external_phi=True,
        approved_ai_providers=("gemini",), default_extraction_adapter="gemini",
        gemini_api_key="synthetic-offline-key", max_upload_bytes=5 * 1024 * 1024)
    # Real adapter/canonical conversion; only the SDK transport is fake.
    payload = {"logical_forms": [{"form_id": "form-1", "fields": clean_result().logical_forms[0].fields.model_dump(mode="json")} ]}
    sdk = _FakeClient(*(errors if errors is not None else [_FakeResponse(payload) for _ in range(3)]))
    adapter = GeminiExtractionAdapter(config=config, max_document_bytes=config.max_upload_bytes,
        timeout_seconds=120, max_retries=2 if retry_unavailable_only else 0,
        retry_unavailable_only=retry_unavailable_only, sleep=sleep, client_factory=lambda *_: sdk, observer=observe_provider)
    return (*setup_cloud(adapter, app_config=config), sdk)


def test_arbitrary_pdf_png_jpeg_each_use_gemini_then_review_reopen_and_bulk_export(monkeypatch):
    monkeypatch.setenv("DEMO_ALLOWED_DOCUMENT_SHA256", "obsolete-value-must-not-restrict-files")
    client, _, _, saved, sdk = gemini_cloud()
    login(client)
    documents = [(b"%PDF-1.7 arbitrary synthetic PDF", "application/pdf"),
                 (b"\x89PNG\r\n\x1a\narbitrary synthetic PNG", "image/png"),
                 (b"\xff\xd8\xffarbitrary synthetic JPEG", "image/jpeg")]
    runs = []
    for content, mime in documents:
        response = upload(client, content, mime=mime)
        assert response.status_code == 201
        run = response.json()
        assert run["provider"] == "gemini" and run["model"] == AppConfig().gemini_model
        assert run["status"] == "succeeded" and run["selected_form"]["finalized"] is False
        assert run["selected_form"]["automatic_acceptance"] is False
        assert saved[run["run_id"]].source_sha256 == sha256(content).hexdigest()
        assert client.get(f"{BASE}/{run['run_id']}/source").content == content
        runs.append(run)
    assert len({r["run_id"] for r in runs}) == 3 and len(sdk.calls) == 3
    assert [call["contents"][0].inline_data.mime_type for call in sdk.calls] == [mime for _, mime in documents]
    first = runs[0]
    reviewed = client.post(f"{BASE}/{first['run_id']}/forms/form-1/review", json=command(first))
    assert reviewed.status_code == 200
    final = client.post(f"{BASE}/{first['run_id']}/forms/form-1/finalize", json=finalize_command(reviewed.json()))
    assert final.status_code == 200 and final.json()["selected_form"]["finalized"] is True
    runs[0] = final.json()
    assert client.get(f"{BASE}/{first['run_id']}").json()["selected_form"]["finalized"] is True
    selection = {"forms": [{"run_id": r["run_id"], "form_id": "form-1", "review_token": r["review_token"],
        "expected_revision": r["selected_form"]["revision"], "commitments": ["00001", "00002"]} for r in runs]}
    exported = client.post("/api/v1/business-records/export.xlsx", json=selection)
    assert exported.status_code == 200 and len(read_workbook(exported.content)["Forms"][1]) == 6
    assert len(sdk.calls) == 3  # Review/reopen/export do not send documents again.


def test_gemini_requires_authentication_csrf_and_explicit_per_document_consent():
    client, _, repository, _, sdk = gemini_cloud()
    assert upload(client).status_code == 401
    repository.create.assert_not_called()
    login(client)
    assert upload(client, consent=False).status_code == 403
    assert not sdk.calls
    assert client.post(BASE, files={"file": ("test.pdf", PDF, "application/pdf")}).status_code == 403
    assert not sdk.calls
    assert client.post(BASE, headers={"X-Demo-CSRF": "wrong"}, files={"file": ("test.pdf", PDF, "application/pdf")},
                       data={"allow_external_processing": "true"}).status_code == 403
    assert not sdk.calls


@pytest.mark.parametrize("flag,value,code", [("ai_outbound_enabled", False, "PROVIDER_NOT_AUTHORIZED"),
    ("allow_external_phi", False, "PROVIDER_NOT_AUTHORIZED"), ("approved_ai_providers", (), "PROVIDER_NOT_AUTHORIZED"),
    ("gemini_api_key", None, "MISSING_PROVIDER_CREDENTIALS")])
def test_gemini_configuration_policy_gates_remain_fail_closed(flag, value, code):
    config = AppConfig(ai_outbound_enabled=True, allow_external_phi=True, approved_ai_providers=("gemini",),
                       gemini_api_key="synthetic-offline-key", max_upload_bytes=5 * 1024 * 1024)
    client, _, _, _, sdk = gemini_cloud(config=replace(config, **{flag: value}))
    login(client)
    response = upload(client)
    assert response.json()["failure"]["code"] == code and response.json()["selected_form"] is None
    assert not sdk.calls


@pytest.mark.parametrize("error,status", [(_StatusError(429), 429), (_StatusError(503), 503), (TimeoutError("synthetic"), 504)])
def test_real_gemini_adapter_errors_are_preserved_without_retry_or_fallback(error, status):
    client, _, _, _, sdk = gemini_cloud(errors=[error])
    login(client)
    response = upload(client)
    assert response.status_code == status and response.json()["status"] == "failed"
    assert response.json()["selected_form"] is None and len(sdk.calls) == 1
    assert "SECRET_TEST_KEY" not in response.text


@pytest.mark.parametrize("content,mime", [(b"not a document", "application/pdf"), (PDF, "image/png"),
    (b"\x89PNG\r\n\x1a\n" + b"x" * (5 * 1024 * 1024), "image/png")],
    ids=["invalid-signature", "mime-mismatch", "oversize"])
def test_removing_hash_allowlist_does_not_bypass_file_validation_or_size(content, mime):
    client, _, repository, _, sdk = gemini_cloud()
    login(client)
    assert upload(client, content, mime=mime).status_code in {413, 415}
    assert not sdk.calls
    repository.create.assert_not_called()


@pytest.mark.parametrize("timeout", [None, "30", "90", "120"])
def test_production_factory_selects_existing_gemini_adapter_and_configured_model(monkeypatch, tmp_path, timeout, caplog):
    import json
    import logging
    from backend import cloud_app
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    monkeypatch.delenv("GEMINI_TIMEOUT_SECONDS", raising=False)
    if timeout is not None:
        monkeypatch.setenv("GEMINI_TIMEOUT_SECONDS", timeout)
    for name, value in {"APP_DEPLOYMENT": "cloud", "DEMO_DATA_POLICY": "synthetic-only", "DEMO_PUBLIC_ORIGIN": ORIGIN,
        "DEMO_PASSWORD_HASH": PASSWORD_HASH, "DEMO_DATABASE_NAME": "submission_synthetic", "MAX_UPLOAD_BYTES": "5242880",
        "DEFAULT_EXTRACTION_ADAPTER": "gemini", "AI_OUTBOUND_ENABLED": "true", "ALLOW_EXTERNAL_PHI": "true",
        "APPROVED_AI_PROVIDERS": "gemini", "GEMINI_API_KEY": "synthetic-offline-key", "GEMINI_MODEL": "existing-configured-model"}.items():
        monkeypatch.setenv(name, value)
    directory = tmp_path / "frontend" / "dist"
    directory.mkdir(parents=True)
    (directory / "index.html").write_text("synthetic factory test")
    monkeypatch.setattr(cloud_app, "__file__", str(tmp_path / "backend" / "cloud_app.py"))
    engine = Mock()
    engine.dialect.name = "postgresql"
    monkeypatch.setattr(cloud_app, "create_cloud_engine", Mock(return_value=engine))
    factory = Mock(side_effect=AssertionError("Factory startup must never call Gemini"))
    monkeypatch.setattr(cloud_app, "_one_attempt_client", factory)
    app = cloud_app.create_cloud_app()
    workflow = app.state.demo_workflow
    assert isinstance(workflow.adapter, GeminiExtractionAdapter)
    assert workflow.adapter._config.gemini_model == "existing-configured-model"
    assert workflow.adapter._max_retries == 2 and workflow.adapter._retry_unavailable_only is True
    assert workflow.config.max_upload_bytes == 5 * 1024 * 1024
    expected_timeout = int(timeout or "120")
    assert workflow.adapter._timeout_seconds == expected_timeout
    messages = [json.loads(record.message) for record in caplog.records if record.name == "uvicorn.error"]
    assert messages[-1]["sdk_timeout_ms"] == expected_timeout * 1000
    assert messages[-1]["sdk_attempts"] == 1 and messages[-1]["adapter_retries"] == 2
    assert messages[-1]["retry_only_http_503_unavailable"] is True
    factory.assert_not_called()
    engine.connect.assert_not_called()


@pytest.mark.parametrize("timeout", ["", "secret-invalid-value", "1.5", "29", "121", "-1"])
def test_cloud_timeout_invalid_environment_is_rejected_safely(monkeypatch, timeout):
    monkeypatch.setenv("APP_DEPLOYMENT", "cloud")
    monkeypatch.setenv("DEMO_DATA_POLICY", "synthetic-only")
    monkeypatch.setenv("GEMINI_TIMEOUT_SECONDS", timeout)
    with pytest.raises(ValueError, match="^GEMINI_TIMEOUT_SECONDS_MUST_BE_INTEGER_30_TO_120$"):
        CloudConfig.from_env()


@pytest.mark.parametrize("timeout", [29, 121, True, 90.5])
def test_cloud_timeout_direct_configuration_cannot_bypass_bounds(timeout):
    with pytest.raises(ValueError, match="^GEMINI_TIMEOUT_SECONDS_MUST_BE_INTEGER_30_TO_120$"):
        CloudConfig(ORIGIN, PASSWORD_HASH, "submission_synthetic", timeout)
