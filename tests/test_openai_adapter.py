"""Offline OpenAI request, response, policy and failure contract tests."""

import base64
import json
import sys
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from jsonschema import Draft202012Validator

from backend.core.config import AppConfig
from backend.domain import ProviderPolicySnapshot
from backend.services.extraction.errors import (
    DocumentTooLarge,
    InvalidProviderRequest,
    InvalidProviderResponse,
    MissingProviderCredentials,
    ProviderConnectionError,
    ProviderNotAuthorized,
    ProviderRateLimit,
    ProviderResourceUnavailable,
    ProviderTimeout,
    ProviderUnavailable,
    UnsupportedDocumentType,
)
from backend.services.extraction.openai_adapter import OpenAIExtractionAdapter
from tests.test_gemini_adapter import _document, _provider_payload, _request


class FakeResponses:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def authorized_config():
    return AppConfig(
        ai_outbound_enabled=True,
        allow_external_phi=True,
        approved_ai_providers=("openai",),
        openai_api_key="synthetic-test-key",
        openai_model="synthetic-model",
    )


def make_adapter(*responses, config=None, **kwargs):
    client = SimpleNamespace(responses=FakeResponses(*responses))
    return OpenAIExtractionAdapter(
        config=config or authorized_config(),
        client_factory=lambda *_: client,
        sleep=lambda _: None,
        **kwargs,
    ), client


def json_response(payload=None, **kwargs):
    return SimpleNamespace(
        output_text=json.dumps(payload if payload is not None else _provider_payload()),
        **kwargs,
    )


def test_pdf_request_and_canonical_output_preserve_values_without_retaining_response():
    adapter, client = make_adapter(json_response(status="completed"))
    document = replace(_document(), filename="sensitive-original-name.pdf")
    result = adapter.extract(_request(), document)

    assert result.provider.name == "openai"
    assert result.provider.version == "synthetic-model"
    fields = result.logical_forms[0].fields
    assert fields.israeli_id.values[0].raw == "001234567"
    assert [v.raw for v in fields.central_numbers.values] == ["000001", "000002"]
    assert fields.sender.review_status == "needs_review"
    assert fields.commitment_numbers.review_status == "unreviewed"
    call = client.responses.calls[0]
    assert call["model"] == "synthetic-model"
    assert call["store"] is False
    assert call["timeout"] == 60
    part = call["input"][0]["content"][1]
    assert part["type"] == "input_file"
    assert part["filename"] == "document.pdf"
    assert base64.b64decode(part["file_data"].split(",", 1)[1]) == document.content


@pytest.mark.parametrize("mime", ["image/png", "image/jpeg"])
def test_image_input_mapping(mime):
    adapter, client = make_adapter(json_response())
    adapter.extract(_request(), _document(mime, b"synthetic-image"))
    part = client.responses.calls[0]["input"][0]["content"][1]
    assert part["type"] == "input_image"
    assert part["image_url"].startswith(f"data:{mime};base64,")


@pytest.mark.parametrize(
    "changes,error",
    [
        ({"ai_outbound_enabled": False}, ProviderNotAuthorized),
        ({"allow_external_phi": False}, ProviderNotAuthorized),
        ({"approved_ai_providers": ()}, ProviderNotAuthorized),
        ({"approved_ai_providers": ("gemini",)}, ProviderNotAuthorized),
        ({"openai_api_key": None}, MissingProviderCredentials),
    ],
)
def test_server_policy_denies_before_client_construction(changes, error):
    factory = Mock(side_effect=AssertionError("No client construction allowed"))
    adapter = OpenAIExtractionAdapter(
        config=replace(authorized_config(), **changes), client_factory=factory
    )
    with pytest.raises(error):
        adapter.extract(_request(), _document())
    factory.assert_not_called()


@pytest.mark.parametrize(
    "policy",
    [
        ProviderPolicySnapshot(),
        ProviderPolicySnapshot(data_boundary="external_phi", allow_external_phi=False),
        ProviderPolicySnapshot(data_boundary="external_no_phi", allow_external_phi=True),
    ],
)
def test_request_policy_denies_before_client_construction(policy):
    factory = Mock(side_effect=AssertionError("No client construction allowed"))
    adapter = OpenAIExtractionAdapter(config=authorized_config(), client_factory=factory)
    with pytest.raises(ProviderNotAuthorized):
        adapter.extract(_request().model_copy(update={"provider_policy": policy}), _document())
    factory.assert_not_called()


@pytest.mark.parametrize(
    "document,limit,error",
    [
        (_document("text/plain", b"synthetic"), 100, UnsupportedDocumentType),
        (_document(content=b""), 100, UnsupportedDocumentType),
        (_document(content=b"12345"), 4, DocumentTooLarge),
    ],
)
def test_invalid_document_does_not_construct_client(document, limit, error):
    factory = Mock(side_effect=AssertionError("No client construction allowed"))
    adapter = OpenAIExtractionAdapter(
        config=authorized_config(), client_factory=factory, max_document_bytes=limit
    )
    with pytest.raises(error):
        adapter.extract(_request(), document)
    factory.assert_not_called()


@pytest.mark.parametrize("status", ["incomplete", "failed", "cancelled", "in_progress"])
def test_non_completed_response_is_rejected_even_with_valid_json(status):
    adapter, _ = make_adapter(json_response(status=status))
    with pytest.raises(InvalidProviderResponse):
        adapter.extract(_request(), _document())


def test_refusal_is_rejected_even_alongside_valid_json():
    response = json_response(
        status="completed",
        output=[SimpleNamespace(content=[SimpleNamespace(type="refusal", refusal="private")])],
    )
    adapter, _ = make_adapter(response)
    with pytest.raises(InvalidProviderResponse) as caught:
        adapter.extract(_request(), _document())
    assert "private" not in str(caught.value)


@pytest.mark.parametrize("response", [SimpleNamespace(output_text="bad JSON"), {}, None])
def test_invalid_response_is_not_retried(response):
    adapter, client = make_adapter(response)
    with pytest.raises(InvalidProviderResponse):
        adapter.extract(_request(), _document())
    assert len(client.responses.calls) == 1


@pytest.mark.parametrize("as_dict", [True, False])
def test_output_blocks_can_supply_json(as_dict):
    block = {"type": "output_text", "text": json.dumps(_provider_payload())}
    response = {"status": "completed", "output": [{"content": [block]}]}
    if not as_dict:
        response = SimpleNamespace(output=[SimpleNamespace(content=[SimpleNamespace(**block)])])
    adapter, _ = make_adapter(response)
    assert adapter.extract(_request(), _document()).provider.name == "openai"


def test_request_schema_and_canonical_mapping_preserve_evidence():
    payload = _provider_payload()
    evidence = [{
        "page_index": 0, "source_kind": "vision_model",
        "text_snippet": "001234567",
        "bounding_box": {"x": 1, "y": 2, "width": 3, "height": 4},
    }]
    payload["logical_forms"][0]["fields"]["israeli_id"]["evidence"] = evidence
    adapter, client = make_adapter(json_response(payload))
    result = adapter.extract(_request(), _document())
    schema = client.responses.calls[0]["text"]["format"]["schema"]
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(payload)
    actual = result.logical_forms[0].fields.israeli_id.evidence[0]
    assert actual.page_index == 0
    assert actual.text_snippet == "001234567"
    assert actual.bounding_box.width == 3


class StatusError(Exception):
    def __init__(self, status, code=None):
        super().__init__("SECRET document text and key must not escape")
        self.status_code = status
        self.code = code


@pytest.mark.parametrize(
    "status,error,retryable",
    [
        (400, InvalidProviderRequest, False), (422, InvalidProviderRequest, False),
        (401, ProviderNotAuthorized, False), (403, ProviderNotAuthorized, False),
        (404, ProviderResourceUnavailable, False), (408, ProviderTimeout, True),
        (409, ProviderUnavailable, True), (429, ProviderRateLimit, True),
        (500, ProviderUnavailable, True), (503, ProviderUnavailable, True),
    ],
)
def test_http_errors_are_classified_and_retried_only_when_transient(status, error, retryable):
    adapter, client = make_adapter(StatusError(status), StatusError(status))
    with pytest.raises(error) as caught:
        adapter.extract(_request(), _document())
    assert caught.value.retryable is retryable
    assert len(client.responses.calls) == (2 if retryable else 1)
    assert "SECRET" not in str(caught.value)
    assert "SECRET" not in json.dumps(caught.value.safe_diagnostics())


@pytest.mark.parametrize("error", [TimeoutError("private"), ConnectionError("private")])
def test_transient_failure_can_recover(error):
    adapter, client = make_adapter(error, json_response())
    assert adapter.extract(_request(), _document()).provider.name == "openai"
    assert len(client.responses.calls) == 2


@pytest.mark.parametrize("quota", ["insufficient_quota", "billing_hard_limit_reached", "quota_exceeded"])
def test_exhausted_quota_is_not_retried(quota):
    adapter, client = make_adapter(StatusError(429, quota))
    with pytest.raises(ProviderRateLimit) as caught:
        adapter.extract(_request(), _document())
    assert caught.value.retryable is False
    assert len(client.responses.calls) == 1


def test_constructor_errors_are_safe_and_sdk_retry_is_disabled():
    factory = Mock(side_effect=ConnectionError("SECRET"))
    adapter = OpenAIExtractionAdapter(config=authorized_config(), client_factory=factory)
    with pytest.raises(ProviderConnectionError) as caught:
        adapter.extract(_request(), _document())
    assert "SECRET" not in str(caught.value)
    constructor = Mock()
    with patch.dict(sys.modules, {"openai": SimpleNamespace(OpenAI=constructor)}):
        OpenAIExtractionAdapter._create_openai_client("synthetic", 12)
    constructor.assert_called_once_with(api_key="synthetic", timeout=12, max_retries=0)


def test_missing_sdk_produces_safe_provider_error():
    with patch.dict(sys.modules, {"openai": None}):
        with pytest.raises(ProviderUnavailable):
            OpenAIExtractionAdapter._create_openai_client("synthetic", 12)
