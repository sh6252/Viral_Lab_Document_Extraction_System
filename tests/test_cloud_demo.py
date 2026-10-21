"""Public boundary regression; synthetic provider/repository only, no DB or network."""
from hashlib import sha256
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

from backend.cloud_app import CloudWorkflow, create_cloud_application
from backend.core.cloud_config import CloudConfig
from backend.core.config import AppConfig
from backend.core.demo_auth import COOKIE, DemoAuth, hash_password
from backend.persistence.business_repository import BusinessRepository
from backend.persistence.cloud_database import neon_url
from backend.services.extraction.errors import ProviderRateLimit, ProviderTimeout, ProviderUnavailable
from tests.test_demo_api import Adapter, BASE, command
from tests.test_excel_export import read_workbook
from tests.test_form_approval import clean_result, finalize_command

ORIGIN = "https://judges.example.test"
PASSWORD = "synthetic-invitation-password-only"
PDF = b"%PDF-1.7 synthetic allowlisted only"
PASSWORD_HASH = hash_password(PASSWORD)


def setup_cloud(adapter=None, *, ready=None, auth=None, static_directory=None, origin=ORIGIN):
    config = CloudConfig(origin, PASSWORD_HASH, "submission_synthetic", frozenset({sha256(PDF).hexdigest()}))
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
    workflow = CloudWorkflow(AppConfig(max_upload_bytes=5 * 1024 * 1024), provider, repository,
                             allowed_hashes=config.allowed_document_hashes)
    readiness = ready or Mock()
    app = create_cloud_application(config, workflow, readiness, auth=auth, static_directory=static_directory)
    client = TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN, "X-Demo-Request": "1"})
    return client, provider, repository, saved


def login(client):
    response = client.post("/api/v1/auth/login", json={"password": PASSWORD})
    assert response.status_code == 200
    client.headers["X-Demo-CSRF"] = response.json()["csrf_token"]
    return response


def upload(client, content=PDF):
    return client.post(BASE, files={"file": ("synthetic.pdf", content, "application/pdf")},
                       data={"allow_external_processing": "true"})


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


def test_allowlist_stream_limits_and_readiness_prevent_provider_or_database_writes():
    client, provider, repository, _ = setup_cloud()
    login(client)
    assert upload(client, b"%PDF-1.7 unapproved document").status_code == 403
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
        CloudConfig(origin, PASSWORD_HASH, "submission_synthetic", frozenset())


def test_cloud_environment_is_explicit_and_accepts_trusted_render_origin(monkeypatch):
    for name in ("APP_DEPLOYMENT", "DEMO_DATA_POLICY", "DEMO_PUBLIC_ORIGIN", "RENDER_EXTERNAL_URL"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match="EXPLICIT_CLOUD"):
        CloudConfig.from_env()
    monkeypatch.setenv("APP_DEPLOYMENT", "cloud")
    monkeypatch.setenv("DEMO_DATA_POLICY", "synthetic-only")
    monkeypatch.setenv("DEMO_PASSWORD_HASH", PASSWORD_HASH)
    monkeypatch.setenv("DEMO_DATABASE_NAME", "submission_synthetic")
    monkeypatch.setenv("DEMO_ALLOWED_DOCUMENT_SHA256", "")
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
