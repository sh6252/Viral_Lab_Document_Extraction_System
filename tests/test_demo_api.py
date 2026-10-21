"""Synthetic complete local HTTP review/download flow. No provider, OCR or DB."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from urllib.parse import quote

from fastapi.testclient import TestClient
import pytest

from backend.core.config import AppConfig
from backend.main import create_app
from backend.services.demo_workflow import DemoWorkflow
from backend.services.extraction.errors import ProviderRateLimit, ProviderTimeout
from tests.test_business_review import FIELDS, canonical
from tests.test_excel_export import read_workbook

BASE = "/api/v1/demo-runs"
PDF = b"%PDF-1.7\n% synthetic only\n"


class Adapter:
    provider_name = "synthetic"

    def __init__(self, result=None, error=None):
        self.result = canonical(two_forms=True) if result is None else result
        self.error, self.calls = error, []

    def extract(self, request, document):
        self.calls.append((request, document))
        if self.error:
            raise self.error
        return self.result


def setup(adapter=None, **limits):
    config = AppConfig()
    adapter = Adapter() if adapter is None else adapter
    service = DemoWorkflow(config, adapter, **limits)
    client = TestClient(create_app(config, adapter, demo_workflow=service), base_url="http://127.0.0.1:8000",
                        client=("127.0.0.1", 50001), headers={"X-Local-Demo": "1"})
    return client, service, adapter


def upload(client, *, content=PDF, mime="application/pdf", consent=True, **extra):
    return client.post(BASE, files={"file": ("synthetic.pdf", content, mime)},
                       data={"allow_external_processing": str(consent).lower(), **extra})


def command(run, *, name="first_name", action="correct_and_approve", values=None, **changes):
    field = next(f for f in run["selected_form"]["fields"] if f["field_name"] == name)
    if values is None:
        values = [v["raw"] for v in field["provider_values"]] if action == "approve_as_extracted" else (
            [] if action in {"acknowledge_blank", "reject", "leave_unresolved"} else [" 000/7) שם סינתטי "])
    body = {"review_token": run["review_token"], "expected_revision": run["selected_form"]["revision"],
        "operator_declaration": "Declared synthetic operator", "declared_at": "2026-01-01T00:00:00Z",
        "actions": [{"field_name": name, "decision_id": "decision-one", "action": action,
                     "reviewed_values": values, "reason": "Synthetic source inspection"}]}
    body.update(changes)
    return body


def post(client, run, body, form="form-1"):
    return client.post(f"{BASE}/{run['run_id']}/forms/{quote(form, safe='')}/review", json=body)


@pytest.mark.parametrize("content,mime", [(PDF, "application/pdf"), (b"\x89PNG\r\n\x1a\nsynthetic", "image/png"),
                                        (b"\xff\xd8\xffsynthetic", "image/jpeg")])
def test_upload_all_types_preserves_all_forms_and_no_autoapproval(content, mime):
    client, service, adapter = setup()
    result = upload(client, content=content, mime=mime)
    assert result.status_code == 201
    run = result.json()
    assert run["status"] == "succeeded" and run["data_origin"] == "live"
    assert run["extraction_id"] == adapter.result.extraction_id
    assert run["prompt_version"] == "he-commitment-v3-lab-commitments" and run["pipeline_version"] == "api-v1"
    assert run["form_ids"] == ["form-1", "form-2"]
    assert [f["field_name"] for f in run["selected_form"]["fields"]] == list(FIELDS)
    assert all(f["review_status"] == "unreviewed" for f in run["selected_form"]["fields"])
    assert len(adapter.calls) == 1 and adapter.calls[0][1].mime_type == mime
    assert adapter.calls[0][0].provider_policy.allow_external_phi is True
    stored = service._runs[run["run_id"]]
    assert stored.source == content and stored.canonical_snapshot_json


@pytest.mark.parametrize("error", [ProviderTimeout("PRIVATE provider text"), ProviderRateLimit("PRIVATE quota body")])
def test_provider_failure_is_separate_failure_workbook_with_no_fake_form(error):
    client, service, adapter = setup(Adapter(error=error))
    response = upload(client)
    assert response.status_code == 201
    run = response.json()
    assert run["status"] == "failed" and run["selected_form"] is None and run["form_ids"] == []
    assert run["extraction_id"] is None and run["prompt_version"] == "he-commitment-v3-lab-commitments"
    assert run["failure"]["code"] == error.code and "PRIVATE" not in response.text
    data = client.get(f"{BASE}/{run['run_id']}/export.xlsx").content
    book = read_workbook(data)
    assert not book["Forms"][1] and len(book["ProcessingFailures"][1]) == 1
    assert not book["ReviewAudit"][1] and len(adapter.calls) == 1


def test_missing_consent_makes_zero_provider_calls_and_never_falls_back():
    client, _, adapter = setup()
    run = upload(client, consent=False).json()
    assert run["status"] == "failed" and run["failure"]["code"] == "PROVIDER_NOT_AUTHORIZED"
    assert not adapter.calls and run["data_origin"] == "live"


@pytest.mark.parametrize("kind,state", [("approve_as_extracted", "approved_provider"),
    ("correct_and_approve", "approved_corrected"), ("acknowledge_blank", "acknowledged_blank"),
    ("reject", "rejected_unresolved"), ("leave_unresolved", "unresolved")])
def test_all_five_dispositions_reuse_business_review_without_mutation(kind, state):
    client, service, adapter = setup()
    original = adapter.result.model_dump_json()
    run = upload(client).json()
    snapshot = service._runs[run["run_id"]].canonical_snapshot_json
    response = post(client, run, command(run, action=kind))
    assert response.status_code == 200
    field = response.json()["selected_form"]["fields"][0]
    assert field["review_status"] == state
    assert adapter.result.model_dump_json() == original
    assert service._runs[run["run_id"]].canonical_snapshot_json == snapshot
    assert response.json()["selected_form"]["review_status"] == "unresolved"


def test_exact_multivalue_edit_unicode_formula_and_all_form_export():
    client, _, adapter = setup()
    run = upload(client).json()
    values = ["000123", "A,B", "000123", " עברית /) "]
    body = command(run, name="central_numbers", values=values)
    assert post(client, run, body).status_code == 200
    next_run = client.get(f"{BASE}/{run['run_id']}").json()
    dangerous = command(next_run, values=["=HYPERLINK(\"https://example.invalid\")"])
    dangerous["actions"][0]["decision_id"] = "second-decision"
    assert post(client, next_run, dangerous).status_code == 200
    response = client.get(f"{BASE}/{run['run_id']}/export.xlsx")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert response.headers["content-disposition"] == 'attachment; filename="reviewed-demo.xlsx"'
    assert response.headers["cache-control"] == "no-store"
    book = read_workbook(response.content)
    assert tuple(book) == ("Forms", "ProcessingFailures", "ReviewAudit")
    assert len(book["Forms"][1]) == 2 and len(book["ReviewAudit"][1]) == 2
    import json
    assert json.loads(book["Forms"][1][0]["central_numbers"]) == values
    assert book["Forms"][1][0]["first_name"].startswith("=HYPERLINK")
    assert book["Forms"][1][1]["record_review_status"] == "unresolved" and len(adapter.calls) == 1


def test_all_seven_actions_can_complete_one_form_but_other_form_stays_unresolved():
    client, _, _ = setup()
    run = upload(client).json()
    body = command(run)
    body["actions"] = [{"field_name": name, "decision_id": f"d-{i}", "action": "approve_as_extracted",
        "reviewed_values": [v["raw"] for v in run["selected_form"]["fields"][i]["provider_values"]],
        "reason": "Inspect source"} for i, name in enumerate(FIELDS)]
    result = post(client, run, body)
    assert result.status_code == 200 and result.json()["selected_form"]["review_status"] == "complete"
    assert result.json()["selected_form"]["revision"] == 7
    second = client.get(f"{BASE}/{run['run_id']}", params={"form_id": "form-2"}).json()
    assert second["selected_form"]["revision"] == 0
    book = read_workbook(client.get(f"{BASE}/{run['run_id']}/export.xlsx").content)
    assert book["Forms"][1][0]["israeli_id"] == "000123"
    assert book["Forms"][1][0]["sender"] == "טקסט בדיקה"


def test_unknown_ids_cross_run_and_forged_snapshot_rejected_without_echo():
    client, _, _ = setup()
    first, second = upload(client).json(), upload(client).json()
    assert first["run_id"] != second["run_id"] and first["review_token"] != second["review_token"]
    assert client.get(f"{BASE}/unknown").status_code == 404
    assert client.get(f"{BASE}/{first['run_id']}", params={"form_id": ""}).status_code == 404
    assert post(client, first, command(first), form="foreign").status_code == 404
    assert post(client, second, command(first)).status_code == 404
    for extra in ("canonical_snapshot", "source_sha256", "extraction_id", "form_id", "data_origin"):
        response = post(client, first, command(first, **{extra: "PRIVATE_FORGED_CONTENT"}))
        assert response.status_code == 422 and "PRIVATE" not in response.text
    assert upload(client, data_origin="historical_replay").status_code == 422
    assert client.post(BASE+"/replay", json={"path": "C:/PRIVATE"}).status_code == 405


def test_atomic_replay_conflict_stale_and_concurrent_same_command():
    client, _, _ = setup()
    run = upload(client).json()
    body = command(run)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: post(client, run, body), range(2)))
    assert all(r.status_code == 200 for r in responses)
    assert all(r.json()["selected_form"]["revision"] == 1 for r in responses)
    changed = deepcopy(body)
    changed["actions"][0]["reason"] = "different"
    assert post(client, run, changed).status_code == 409
    changed["actions"][0]["decision_id"] = "different"
    assert post(client, run, changed).status_code == 409
    assert len(read_workbook(client.get(f"{BASE}/{run['run_id']}/export.xlsx").content)["ReviewAudit"][1]) == 1


@pytest.mark.parametrize("headers", [{"Origin": "https://evil.invalid"}, {"Sec-Fetch-Site": "cross-site"},
    {"Host": "evil.invalid"}, {"X-Local-Demo": "0"}])
def test_foreign_browser_or_host_denied_before_provider(headers):
    client, _, adapter = setup()
    assert client.post(BASE, files={"file": ("test.pdf", PDF, "application/pdf")}, headers=headers).status_code == 403
    assert not adapter.calls


def test_remote_peer_and_default_app_do_not_expose_local_demo():
    adapter = Adapter()
    app = create_app(extraction_adapter=adapter, demo_workflow=DemoWorkflow(AppConfig(), adapter))
    remote = TestClient(app, base_url="http://127.0.0.1:8000", client=("192.0.2.1", 1234), headers={"X-Local-Demo": "1"})
    assert remote.get(BASE+"/unknown").status_code == 403
    assert TestClient(create_app()).get(BASE+"/unknown").status_code == 404


def test_capacity_and_expiration_and_result_bounds_are_explicit():
    clock = [0.0]
    client, service, adapter = setup(max_runs=1, ttl_seconds=10, clock=lambda: clock[0])
    run = upload(client).json()
    assert upload(client).status_code == 503 and len(adapter.calls) == 1
    clock[0] = 11
    assert client.get(f"{BASE}/{run['run_id']}").status_code == 404
    assert upload(client).status_code == 201
    tiny, _, _ = setup(max_source_bytes=2)
    assert upload(tiny).status_code == 503
    tiny_result, _, _ = setup(max_snapshot_bytes=10)
    failed = upload(tiny_result).json()
    assert failed["status"] == "failed" and failed["form_ids"] == []


def test_existing_extraction_and_review_boundaries_remain_compatible(monkeypatch):
    from backend.services.extraction.gemini_adapter import GeminiExtractionAdapter
    monkeypatch.setattr(GeminiExtractionAdapter, "extract", lambda *args: pytest.fail("no real provider"))
    client, _, adapter = setup()
    response = client.post("/api/v1/extractions", files={"file": ("test.pdf", PDF, "application/pdf")})
    assert response.status_code == 200 and response.json()["logical_forms"][0]["form_id"] == "form-1"
    assert client.get("/api/v1/review-sessions/synthetic").status_code == 401
    assert client.get("/api/v1/health").status_code == 200


def test_no_ocr_or_provider_after_upload_and_validation_is_safe(monkeypatch):
    import socket
    from backend.services.extraction.review_workflow import ReviewWorkflowService
    from backend.services.extraction import ocr_candidates
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kw: pytest.fail("no network"))
    monkeypatch.setattr(ocr_candidates, "assess_ocr_candidates", lambda *args, **kw: pytest.fail("no OCR assessment"))
    monkeypatch.setattr(ReviewWorkflowService, "prepare_review", lambda *args, **kw: pytest.fail("no OCR workflow"))
    client, _, adapter = setup()
    run = upload(client).json()
    adapter.extract = lambda *args: pytest.fail("review/export must not extract")
    body = command(run)
    body["operator_declaration"] = ""
    assert post(client, run, body).status_code == 422
    body = command(run)
    body["expected_revision"] = True
    assert post(client, run, body).status_code == 422
    assert client.get(f"{BASE}/{run['run_id']}").status_code == 200
    assert post(client, run, command(run)).status_code == 200
    assert client.get(f"{BASE}/{run['run_id']}/export.xlsx").status_code == 200


def test_spoofed_empty_and_oversized_uploads_do_not_call_provider():
    client, _, adapter = setup()
    assert upload(client, content=b"").status_code == 400
    assert upload(client, mime="image/png").status_code == 415
    assert upload(client, content=b"invalid").status_code == 415
    assert not adapter.calls


def test_local_launcher_pins_one_adapter_and_sdk_attempt_without_policy_override(monkeypatch):
    from backend.tools.local_demo import _one_attempt_client, create_local_demo_app
    from google import genai
    captured = {}
    monkeypatch.setattr(genai, "Client", lambda **kwargs: captured.update(kwargs) or object())
    _one_attempt_client("synthetic-key", 60)
    assert captured["http_options"].retry_options.attempts == 1
    assert captured["http_options"].timeout == 60000
    monkeypatch.setenv("DEFAULT_EXTRACTION_ADAPTER", "gemini")
    monkeypatch.setenv("AI_OUTBOUND_ENABLED", "false")
    app = create_local_demo_app()
    assert app.state.demo_workflow.adapter._max_retries == 0
    assert app.state.config.ai_outbound_enabled is False
