"""Opt-in adapter integration with fake responses; all external access denied."""

from copy import deepcopy
from dataclasses import replace
import json
import socket

from jsonschema import Draft202012Validator
import pytest

from backend.core.config import AppConfig
from backend.domain import ProviderPolicySnapshot
from backend.services.extraction import gemini_adapter as adapter_module
from backend.services.extraction.errors import (
    ExtractionValidationError,
    InvalidProviderRequest,
    InvalidProviderResponse,
    MissingProviderCredentials,
    ProviderNotAuthorized,
)
from backend.services.extraction.gemini_adapter import GeminiExtractionAdapter
from backend.services.extraction.gemini_schema import build_gemini_response_schema
from backend.tools.extraction_smoke import SYNTHETIC_GROUND_TRUTH_PATH, load_json
from evaluation.scoring import score_extraction
from tests.test_gemini_adapter import (
    _FakeClient, _FakeResponse, _StatusError, _TimeoutSdkError,
    _authorized_config, _document, _provider_payload, _request,
)


@pytest.fixture(autouse=True)
def deny_real_network_and_sdk_clients(monkeypatch) -> None:
    def forbidden(*_args, **_kwargs):
        pytest.fail("OFFLINE_TEST_NETWORK_OR_SDK_CLIENT_FORBIDDEN")
    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(GeminiExtractionAdapter, "_create_google_client", staticmethod(forbidden))


def run_fake(response, *, enabled: bool = True):
    client = _FakeClient(response)
    adapter = GeminiExtractionAdapter(
        config=replace(_authorized_config(), gemini_structured_outputs_experimental=enabled),
        client_factory=lambda *_: client,
    )
    result = adapter.extract(_request(), _document())
    return client, result


@pytest.mark.parametrize("setting,expected", [
    (None, False), ("false", False), ("0", False), ("", False), ("invalid", False),
    ("true", True), ("1", True), ("YES", True), ("on", True),
])
def test_environment_setting_requires_explicit_opt_in(monkeypatch, setting, expected) -> None:
    monkeypatch.delenv("GEMINI_STRUCTURED_OUTPUTS_EXPERIMENTAL", raising=False)
    if setting is not None:
        monkeypatch.setenv("GEMINI_STRUCTURED_OUTPUTS_EXPERIMENTAL", setting)
    assert AppConfig().gemini_structured_outputs_experimental is False
    assert AppConfig.from_env().gemini_structured_outputs_experimental is expected


def test_default_request_remains_schema_free_and_enabled_only_adds_schema() -> None:
    plain_client, plain = run_fake(_FakeResponse(_provider_payload()), enabled=False)
    schema_client, structured = run_fake(_FakeResponse(_provider_payload()))
    plain_call, schema_call = plain_client.calls[0], schema_client.calls[0]
    assert plain == structured
    assert plain_call["contents"] == schema_call["contents"]
    assert plain_call["model"] == schema_call["model"]
    assert plain_call["config"].response_schema is None
    assert plain_call["config"].response_json_schema is None
    assert schema_call["config"].response_schema is None
    assert schema_call["config"].response_json_schema == build_gemini_response_schema()
    actual = schema_call["config"].model_dump(exclude_none=True)
    actual.pop("response_json_schema")
    assert actual == plain_call["config"].model_dump(exclude_none=True) == {
        "response_mime_type": "application/json", "temperature": 0.0,
    }


def test_disabled_option_does_not_build_a_generation_schema(monkeypatch) -> None:
    def forbidden():
        pytest.fail("Disabled experimental feature must not build a schema")
    monkeypatch.setattr(adapter_module, "build_gemini_response_schema", forbidden)
    run_fake(_FakeResponse(_provider_payload()), enabled=False)


@pytest.mark.parametrize("representation", ["text", "parsed"])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("defect", ["missing_values", "null_values", "missing_status", "missing_raw", "numeric_raw", "invalid_status", "non_json"])
def test_malformed_payloads_stay_nonretryable_without_repair(enabled, representation, defect) -> None:
    payload = _provider_payload()
    field = payload["logical_forms"][0]["fields"]["israeli_id"]
    if defect == "missing_values":
        field.clear()
        field["value_status"] = "missing"
    elif defect == "null_values":
        field["values"] = None
    elif defect == "missing_status":
        field.pop("value_status")
    elif defect == "missing_raw":
        field["values"] = [{}]
    elif defect == "numeric_raw":
        field["values"] = [{"raw": 123}]
    elif defect == "invalid_status":
        field["value_status"] = "absent"
    before = deepcopy(payload)
    value = "not JSON" if defect == "non_json" else payload
    response = _FakeResponse(value) if representation == "parsed" else _FakeResponse(
        text=value if isinstance(value, str) else json.dumps(value),
    )
    client = _FakeClient(response)
    adapter = GeminiExtractionAdapter(
        config=replace(_authorized_config(), gemini_structured_outputs_experimental=enabled),
        client_factory=lambda *_: client, max_retries=2,
        sleep=lambda _: pytest.fail("Malformed responses must not retry"),
    )
    with pytest.raises(InvalidProviderResponse) as caught:
        adapter.extract(_request(), _document())
    assert caught.value.code == "INVALID_PROVIDER_RESPONSE"
    assert caught.value.retryable is False
    assert len(client.calls) == 1
    assert payload == before


@pytest.mark.parametrize("warning", ["needs_review", "needs_clarification"])
@pytest.mark.parametrize("status", ["present", "missing", "unreadable", "ambiguous", "not_processed"])
def test_opt_in_preserves_all_candidate_metadata_and_multiple_form_isolation(warning, status) -> None:
    payload = _provider_payload()
    first = payload["logical_forms"][0]
    first["fields"]["first_name"]["values"] = [{"raw": "דנה"}]
    candidate = {
        "value_status": status, "values": [{"raw": "0001"}, {"raw": "0002"}] if status == "present" else [],
        "review_status": warning, "notes": "Synthetic role uncertainty",
        "signals": {"confidence": 0.99, "title": "synthetic", "nested": [None, {"flag": True}]},
        "evidence": [{"page_index": 0, "source_kind": "sticker", "text_snippet": "synthetic",
                      "bounding_box": {"x": 1, "y": 2, "width": 3, "height": 4}}],
    }
    first["fields"]["central_numbers"] = deepcopy(candidate)
    second = deepcopy(first)
    second["form_id"] = "form-002"
    second["fields"]["israeli_id"]["values"] = [{"raw": "009999999"}]
    payload["logical_forms"].append(second)
    before = deepcopy(payload)
    Draft202012Validator(build_gemini_response_schema()).validate(payload)
    _, result = run_fake(_FakeResponse(payload))
    assert len(result.logical_forms) == 2
    assert result.logical_forms[0].fields.first_name.values[0].raw == "דנה"
    assert result.logical_forms[0].fields.israeli_id.values[0].raw == "001234567"
    assert result.logical_forms[1].fields.israeli_id.values[0].raw == "009999999"
    assert result.logical_forms[0].fields.central_numbers.model_dump(mode="json", exclude_none=True) == candidate
    assert payload == before


@pytest.mark.parametrize("omission", ["whole_field", "null_field", "optional_metadata"])
def test_existing_optional_properties_remain_optional(omission) -> None:
    payload = _provider_payload()
    fields = payload["logical_forms"][0]["fields"]
    if omission == "whole_field":
        fields.pop("israeli_id")
    elif omission == "null_field":
        fields["israeli_id"] = None
    else:
        fields["israeli_id"] = {"value_status": "missing", "values": []}
    _, result = run_fake(_FakeResponse(payload))
    if omission != "optional_metadata":
        assert result.logical_forms[0].fields.israeli_id is None
    else:
        assert result.logical_forms[0].fields.israeli_id.values == []


@pytest.mark.parametrize("gate", ["outbound", "phi", "allowlist", "credential", "request_boundary", "request_phi"])
def test_opt_in_cannot_bypass_authorization_before_client_or_schema(monkeypatch, gate) -> None:
    config = replace(_authorized_config(), gemini_structured_outputs_experimental=True)
    request = _request()
    if gate == "outbound":
        config = replace(config, ai_outbound_enabled=False)
    elif gate == "phi":
        config = replace(config, allow_external_phi=False)
    elif gate == "allowlist":
        config = replace(config, approved_ai_providers=())
    elif gate == "credential":
        config = replace(config, gemini_api_key=None)
    else:
        request = request.model_copy(update={"provider_policy": ProviderPolicySnapshot(
            data_boundary="local_only" if gate == "request_boundary" else "external_phi",
            allow_external_phi=False,
        )})
    def forbidden(*_args, **_kwargs):
        pytest.fail("Denied requests must not build schema or construct clients")
    monkeypatch.setattr(adapter_module, "build_gemini_response_schema", forbidden)
    adapter = GeminiExtractionAdapter(config=config, client_factory=forbidden)
    expected = MissingProviderCredentials if gate == "credential" else ProviderNotAuthorized
    with pytest.raises(expected):
        adapter.extract(request, _document())


def test_unsupported_schema_http_400_fails_once_with_safe_diagnostics() -> None:
    client = _FakeClient(_StatusError(400, status="INVALID_ARGUMENT"), _FakeResponse(_provider_payload()))
    adapter = GeminiExtractionAdapter(
        config=replace(_authorized_config(), gemini_structured_outputs_experimental=True),
        client_factory=lambda *_: client, max_retries=2,
        sleep=lambda _: pytest.fail("Schema rejection must not retry or fall back"),
    )
    with pytest.raises(InvalidProviderRequest) as caught:
        adapter.extract(_request(), _document())
    assert caught.value.retryable is False
    assert caught.value.code == "INVALID_PROVIDER_REQUEST"
    assert caught.value.safe_diagnostics()["http_status_code"] == 400
    assert caught.value.safe_diagnostics()["provider_status"] == "INVALID_ARGUMENT"
    assert "SECRET_TEST_KEY" not in str(caught.value)
    assert "SECRET_TEST_KEY" not in json.dumps(caught.value.safe_diagnostics())
    assert len(client.calls) == 1
    assert client.calls[0]["config"].response_json_schema == build_gemini_response_schema()


def test_eligible_transport_retry_keeps_identical_schema() -> None:
    client = _FakeClient(_TimeoutSdkError("synthetic timeout"), _FakeResponse(_provider_payload()))
    delays = []
    adapter = GeminiExtractionAdapter(
        config=replace(_authorized_config(), gemini_structured_outputs_experimental=True),
        client_factory=lambda *_: client, max_retries=1, sleep=delays.append,
    )
    adapter.extract(_request(), _document())
    assert delays == [1]
    assert len(client.calls) == 2
    assert all(call["config"].response_json_schema == build_gemini_response_schema() for call in client.calls)


@pytest.mark.parametrize("invalid", ["duplicate_ids", "missing_ids", "conflicting_shapes", "empty_forms", "present_empty", "missing_populated", "terminal_review"])
def test_canonical_validation_remains_authoritative(invalid) -> None:
    payload = _provider_payload()
    first = payload["logical_forms"][0]
    if invalid in {"duplicate_ids", "missing_ids"}:
        if invalid == "missing_ids":
            first.pop("form_id")
        payload["logical_forms"].append(deepcopy(first))
    elif invalid == "conflicting_shapes":
        payload["fields"] = deepcopy(first["fields"])
    elif invalid == "empty_forms":
        payload["logical_forms"] = []
    elif invalid == "present_empty":
        first["fields"]["israeli_id"]["values"] = []
    elif invalid == "missing_populated":
        first["fields"]["israeli_id"]["value_status"] = "missing"
    else:
        first["fields"]["israeli_id"]["review_status"] = "approved_as_extracted"
    with pytest.raises(InvalidProviderResponse) as caught:
        run_fake(_FakeResponse(payload))
    assert caught.value.retryable is False


def test_final_result_validation_is_still_required(monkeypatch) -> None:
    def fail_final(_result):
        raise ExtractionValidationError("Synthetic final schema rejection.")
    monkeypatch.setattr(adapter_module, "validate_extraction_result_schema", fail_final)
    with pytest.raises(ExtractionValidationError):
        run_fake(_FakeResponse(_provider_payload()))


def test_mocked_labeled_development_fixture_has_identical_results_and_scores() -> None:
    """Transport/configuration parity against labeled synthetic data, not model accuracy."""
    truth = load_json(SYNTHETIC_GROUND_TRUTH_PATH)
    before = deepcopy(truth)
    payload = {"logical_forms": [{
        "form_id": truth["logical_forms"][0]["form_id"],
        "fields": {
            name: {"value_status": "present", "values": deepcopy(field["values"])}
            for name, field in truth["logical_forms"][0]["fields"].items()
        },
    }]}
    outputs, scores = [], []
    for enabled in (False, True):
        _, result = run_fake(_FakeResponse(deepcopy(payload)), enabled=enabled)
        output = result.model_dump(mode="json", exclude_none=True)
        outputs.append(output)
        scores.append(score_extraction(truth, output))
    assert outputs[0] == outputs[1]
    assert scores[0] == scores[1]
    assert scores[0]["overall"]["exact_matches"] == 7
    assert scores[0]["silent_errors"]["silently_accepted_errors"] == 0
    assert truth == before
