import json
import unittest
from base64 import urlsafe_b64decode
from dataclasses import replace
import socket

import httpx
import pytest
from jsonschema import Draft202012Validator

from backend.core.config import AppConfig
from backend.domain import (
    ExtractionPage,
    ExtractionRequest,
    ProviderPolicySnapshot,
)
from backend.services.extraction import DocumentInput, GeminiExtractionAdapter
from backend.services.extraction.errors import (
    DocumentTooLarge,
    InvalidProviderResponse,
    InvalidProviderRequest,
    MissingProviderCredentials,
    ProviderConnectionError,
    ProviderNotAuthorized,
    ProviderRateLimit,
    ProviderResourceUnavailable,
    ProviderTimeout,
    ProviderUnavailable,
    UnsupportedDocumentType,
)
from tests.test_domain_models import EXTRACTION_SCHEMA
from backend.services.extraction.gemini_schema import build_gemini_response_schema


class _FakeResponse:
    def __init__(self, payload=None, *, text=None):
        self.parsed = payload
        self.text = text


class _FakeModels:
    def __init__(self, fake_client):
        self._fake_client = fake_client

    def generate_content(self, **kwargs):
        self._fake_client.calls.append(kwargs)
        next_item = self._fake_client.responses.pop(0)
        if isinstance(next_item, Exception):
            raise next_item
        return next_item


class _FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.models = _FakeModels(self)


class _StatusError(Exception):
    def __init__(self, status_code, *, status=None):
        super().__init__("provider error with SECRET_TEST_KEY and raw payload")
        self.status_code = status_code
        self.status = status


class _NetworkSdkError(Exception):
    pass


class _TimeoutSdkError(Exception):
    pass


def _authorized_config(api_key="test-key") -> AppConfig:
    return AppConfig(
        ai_outbound_enabled=True,
        allow_external_phi=True,
        approved_ai_providers=("gemini",),
        gemini_api_key=api_key,
        gemini_model="gemini-test-model",
    )


def _request() -> ExtractionRequest:
    return ExtractionRequest(
        request_id="request-001",
        logical_form_id="form-001",
        document_id="document-001",
        pages=[
            ExtractionPage(
                page_id="page-001",
                page_index=0,
                image_uri="storage://rendered/page-001.png",
                width=100,
                height=200,
                rotation_degrees=0,
            )
        ],
        fields=[
            "first_name",
            "last_name",
            "israeli_id",
            "central_numbers",
            "sample_receipt_date",
            "sender",
            "commitment_numbers",
        ],
        pipeline_version="pipeline-test",
        provider_policy=ProviderPolicySnapshot(
            data_boundary="external_phi",
            allow_external_phi=True,
        ),
    )


def _document(mime_type="application/pdf", content=b"%PDF-1.7 synthetic"):
    return DocumentInput(content=content, mime_type=mime_type, filename="synthetic.pdf")


def _provider_payload():
    return {
        "logical_forms": [
            {
                "form_id": "form-001",
                "fields": {
                    "first_name": {
                        "value_status": "present",
                        "values": [{"raw": "Dana", "normalized": "Dana"}],
                    },
                    "last_name": {
                        "value_status": "present",
                        "values": [{"raw": "Cohen"}],
                    },
                    "israeli_id": {
                        "value_status": "present",
                        "values": [{"raw": "001234567"}],
                    },
                    "central_numbers": {
                        "value_status": "present",
                        "values": [{"raw": "000001"}, {"raw": "000002"}],
                    },
                    "sample_receipt_date": {
                        "value_status": "missing",
                        "values": [],
                    },
                    "sender": {
                        "value_status": "ambiguous",
                        "values": [],
                        "notes": "synthetic ambiguous sender",
                    },
                    "commitment_numbers": {
                        "value_status": "present",
                        "values": [{"raw": "AUTH-0007"}],
                    },
                },
            }
        ]
    }


class GeminiExtractionAdapterTests(unittest.TestCase):
    def test_valid_provider_response_normalizes_to_canonical_schema(self):
        fake_client = _FakeClient(_FakeResponse(_provider_payload()))
        adapter = GeminiExtractionAdapter(
            config=_authorized_config(),
            client_factory=lambda api_key, timeout: fake_client,
        )

        result = adapter.extract(_request(), _document())
        payload = result.model_dump(mode="json", exclude_none=True)

        self.assertEqual(result.provider.name, "gemini")
        self.assertEqual(result.provider.mode, "vision_ai")
        self.assertEqual(result.provider.version, "gemini-test-model")
        fields = result.logical_forms[0].fields
        self.assertEqual(fields.israeli_id.values[0].raw, "001234567")
        self.assertEqual([value.raw for value in fields.central_numbers.values], ["000001", "000002"])
        self.assertEqual(fields.sample_receipt_date.review_status, "needs_review")
        self.assertEqual(fields.commitment_numbers.review_status, "unreviewed")
        Draft202012Validator(EXTRACTION_SCHEMA).validate(payload)
        self.assertEqual(len(fake_client.calls), 1)
        self.assertEqual(fake_client.calls[0]["model"], "gemini-test-model")

    def test_provider_json_text_response_is_supported(self):
        fake_client = _FakeClient(_FakeResponse(text=json.dumps(_provider_payload())))
        adapter = GeminiExtractionAdapter(
            config=_authorized_config(),
            client_factory=lambda api_key, timeout: fake_client,
        )

        result = adapter.extract(_request(), _document("image/png", b"png bytes"))

        self.assertEqual(result.logical_forms[0].form_id, "form-001")

    def test_policy_blocks_before_provider_call_when_outbound_disabled(self):
        fake_client = _FakeClient(_FakeResponse(_provider_payload()))
        adapter = GeminiExtractionAdapter(
            config=AppConfig(gemini_api_key="test-key"),
            client_factory=lambda api_key, timeout: fake_client,
        )

        with self.assertRaises(ProviderNotAuthorized):
            adapter.extract(_request(), _document())

        self.assertEqual(fake_client.calls, [])

    def test_policy_blocks_when_phi_transmission_is_not_allowed(self):
        config = AppConfig(
            ai_outbound_enabled=True,
            allow_external_phi=False,
            approved_ai_providers=("gemini",),
            gemini_api_key="test-key",
        )
        adapter = GeminiExtractionAdapter(config=config, client_factory=lambda api_key, timeout: _FakeClient())

        with self.assertRaises(ProviderNotAuthorized):
            adapter.extract(_request(), _document())

    def test_policy_blocks_when_gemini_is_not_approved(self):
        config = AppConfig(
            ai_outbound_enabled=True,
            allow_external_phi=True,
            approved_ai_providers=("other-provider",),
            gemini_api_key="test-key",
        )
        adapter = GeminiExtractionAdapter(config=config, client_factory=lambda api_key, timeout: _FakeClient())

        with self.assertRaises(ProviderNotAuthorized):
            adapter.extract(_request(), _document())

    def test_policy_blocks_when_credentials_are_missing(self):
        adapter = GeminiExtractionAdapter(
            config=_authorized_config(api_key=None),
            client_factory=lambda api_key, timeout: _FakeClient(),
        )

        with self.assertRaises(MissingProviderCredentials):
            adapter.extract(_request(), _document())

    def test_policy_blocks_when_request_does_not_permit_external_phi(self):
        request = _request().model_copy(
            update={"provider_policy": ProviderPolicySnapshot(data_boundary="local_only")}
        )
        adapter = GeminiExtractionAdapter(
            config=_authorized_config(),
            client_factory=lambda api_key, timeout: _FakeClient(),
        )

        with self.assertRaises(ProviderNotAuthorized):
            adapter.extract(request, _document())

    def test_unsupported_mime_type_is_rejected_before_provider_call(self):
        fake_client = _FakeClient(_FakeResponse(_provider_payload()))
        adapter = GeminiExtractionAdapter(
            config=_authorized_config(),
            client_factory=lambda api_key, timeout: fake_client,
        )

        with self.assertRaises(UnsupportedDocumentType):
            adapter.extract(_request(), _document("text/plain", b"synthetic"))

        self.assertEqual(fake_client.calls, [])

    def test_empty_document_is_rejected_before_provider_call(self):
        adapter = GeminiExtractionAdapter(
            config=_authorized_config(),
            client_factory=lambda api_key, timeout: _FakeClient(),
        )

        with self.assertRaises(UnsupportedDocumentType):
            adapter.extract(_request(), _document(content=b""))

    def test_document_too_large_is_rejected_before_provider_call(self):
        adapter = GeminiExtractionAdapter(
            config=_authorized_config(),
            client_factory=lambda api_key, timeout: _FakeClient(),
            max_document_bytes=4,
        )

        with self.assertRaises(DocumentTooLarge):
            adapter.extract(_request(), _document(content=b"12345"))

    def test_invalid_provider_json_is_rejected(self):
        fake_client = _FakeClient(_FakeResponse(text="not-json"))
        adapter = GeminiExtractionAdapter(
            config=_authorized_config(),
            client_factory=lambda api_key, timeout: fake_client,
        )

        with self.assertRaises(InvalidProviderResponse):
            adapter.extract(_request(), _document())

    def test_provider_cannot_mark_field_as_approved(self):
        payload = _provider_payload()
        payload["logical_forms"][0]["fields"]["israeli_id"]["review_status"] = "approved_as_extracted"
        fake_client = _FakeClient(_FakeResponse(payload))
        adapter = GeminiExtractionAdapter(
            config=_authorized_config(),
            client_factory=lambda api_key, timeout: fake_client,
        )

        with self.assertRaises(InvalidProviderResponse):
            adapter.extract(_request(), _document())

    def test_provider_cannot_return_values_for_missing_field(self):
        payload = _provider_payload()
        payload["logical_forms"][0]["fields"]["sample_receipt_date"]["values"] = [{"raw": "2026-10-14"}]
        fake_client = _FakeClient(_FakeResponse(payload))
        adapter = GeminiExtractionAdapter(
            config=_authorized_config(),
            client_factory=lambda api_key, timeout: fake_client,
        )

        with self.assertRaises(InvalidProviderResponse):
            adapter.extract(_request(), _document())

    def test_provider_timeout_is_retryable_and_retried(self):
        fake_client = _FakeClient(_TimeoutSdkError("timeout"), _FakeResponse(_provider_payload()))
        adapter = GeminiExtractionAdapter(
            config=_authorized_config(),
            client_factory=lambda api_key, timeout: fake_client,
            max_retries=1,
            sleep=lambda seconds: None,
        )

        result = adapter.extract(_request(), _document())

        self.assertEqual(result.extraction_id, "request-001")
        self.assertEqual(len(fake_client.calls), 2)

    def test_provider_timeout_is_reported_after_retries_are_exhausted(self):
        fake_client = _FakeClient(_TimeoutSdkError("timeout"))
        adapter = GeminiExtractionAdapter(
            config=_authorized_config(),
            client_factory=lambda api_key, timeout: fake_client,
            max_retries=0,
        )

        with self.assertRaises(ProviderTimeout):
            adapter.extract(_request(), _document())

    def test_provider_invalid_request_is_classified_without_retry(self):
        fake_client = _FakeClient(_StatusError(400, status="INVALID_ARGUMENT"))
        adapter = GeminiExtractionAdapter(
            config=_authorized_config(),
            client_factory=lambda api_key, timeout: fake_client,
            max_retries=2,
        )

        with self.assertRaises(InvalidProviderRequest) as context:
            adapter.extract(_request(), _document())

        self.assertEqual(len(fake_client.calls), 1)
        self.assertFalse(context.exception.retryable)
        self.assertEqual(context.exception.code, "INVALID_PROVIDER_REQUEST")
        self.assertEqual(
            context.exception.safe_diagnostics(),
            {
                "exception_class": "_StatusError",
                "exception_module": _StatusError.__module__,
                "http_status_code": 400,
                "provider_status": "INVALID_ARGUMENT",
            },
        )
        self.assertNotIn("SECRET_TEST_KEY", str(context.exception))

    def test_provider_authentication_error_is_classified_without_retry(self):
        for status_code in (401, 403):
            with self.subTest(status_code=status_code):
                fake_client = _FakeClient(_StatusError(status_code, status="PERMISSION_DENIED"))
                adapter = GeminiExtractionAdapter(
                    config=_authorized_config(),
                    client_factory=lambda api_key, timeout: fake_client,
                    max_retries=2,
                )

                with self.assertRaises(ProviderNotAuthorized) as context:
                    adapter.extract(_request(), _document())

                self.assertEqual(len(fake_client.calls), 1)
                self.assertFalse(context.exception.retryable)
                self.assertEqual(context.exception.code, "PROVIDER_NOT_AUTHORIZED")
                self.assertEqual(
                    context.exception.safe_diagnostics()["http_status_code"],
                    status_code,
                )
                self.assertEqual(
                    context.exception.safe_diagnostics()["provider_status"],
                    "PERMISSION_DENIED",
                )

    def test_provider_resource_unavailable_is_classified_without_retry(self):
        fake_client = _FakeClient(_StatusError(404, status="NOT_FOUND"))
        adapter = GeminiExtractionAdapter(
            config=_authorized_config(),
            client_factory=lambda api_key, timeout: fake_client,
            max_retries=2,
        )

        with self.assertRaises(ProviderResourceUnavailable) as context:
            adapter.extract(_request(), _document())

        self.assertEqual(len(fake_client.calls), 1)
        self.assertFalse(context.exception.retryable)
        self.assertEqual(context.exception.code, "PROVIDER_RESOURCE_UNAVAILABLE")
        self.assertEqual(context.exception.safe_diagnostics()["http_status_code"], 404)
        self.assertEqual(context.exception.safe_diagnostics()["provider_status"], "NOT_FOUND")

    def test_provider_rate_limit_is_classified(self):
        fake_client = _FakeClient(_StatusError(429, status="RESOURCE_EXHAUSTED"))
        adapter = GeminiExtractionAdapter(
            config=_authorized_config(),
            client_factory=lambda api_key, timeout: fake_client,
            max_retries=0,
        )

        with self.assertRaises(ProviderRateLimit) as context:
            adapter.extract(_request(), _document())

        self.assertTrue(context.exception.retryable)
        self.assertEqual(context.exception.safe_diagnostics()["http_status_code"], 429)
        self.assertEqual(
            context.exception.safe_diagnostics()["provider_status"],
            "RESOURCE_EXHAUSTED",
        )

    def test_provider_rate_limit_is_retried(self):
        fake_client = _FakeClient(
            _StatusError(429, status="RESOURCE_EXHAUSTED"),
            _FakeResponse(_provider_payload()),
        )
        adapter = GeminiExtractionAdapter(
            config=_authorized_config(),
            client_factory=lambda api_key, timeout: fake_client,
            max_retries=1,
            sleep=lambda seconds: None,
        )

        result = adapter.extract(_request(), _document())

        self.assertEqual(result.extraction_id, "request-001")
        self.assertEqual(len(fake_client.calls), 2)

    def test_provider_unavailable_is_classified(self):
        fake_client = _FakeClient(_StatusError(500, status="INTERNAL"))
        adapter = GeminiExtractionAdapter(
            config=_authorized_config(),
            client_factory=lambda api_key, timeout: fake_client,
            max_retries=0,
        )

        with self.assertRaises(ProviderUnavailable) as context:
            adapter.extract(_request(), _document())

        self.assertTrue(context.exception.retryable)
        self.assertEqual(context.exception.safe_diagnostics()["http_status_code"], 500)
        self.assertEqual(context.exception.safe_diagnostics()["provider_status"], "INTERNAL")

    def test_provider_unavailable_is_retried(self):
        fake_client = _FakeClient(
            _StatusError(500, status="INTERNAL"),
            _FakeResponse(_provider_payload()),
        )
        adapter = GeminiExtractionAdapter(
            config=_authorized_config(),
            client_factory=lambda api_key, timeout: fake_client,
            max_retries=1,
            sleep=lambda seconds: None,
        )

        result = adapter.extract(_request(), _document())

        self.assertEqual(result.extraction_id, "request-001")
        self.assertEqual(len(fake_client.calls), 2)

    def test_provider_connection_error_is_classified_and_retried(self):
        fake_client = _FakeClient(
            _NetworkSdkError("connection failed with SECRET_TEST_KEY"),
            _FakeResponse(_provider_payload()),
        )
        adapter = GeminiExtractionAdapter(
            config=_authorized_config(),
            client_factory=lambda api_key, timeout: fake_client,
            max_retries=1,
            sleep=lambda seconds: None,
        )

        result = adapter.extract(_request(), _document())

        self.assertEqual(result.extraction_id, "request-001")
        self.assertEqual(len(fake_client.calls), 2)

    def test_provider_connection_error_reports_safe_diagnostics(self):
        fake_client = _FakeClient(_NetworkSdkError("connection failed with SECRET_TEST_KEY"))
        adapter = GeminiExtractionAdapter(
            config=_authorized_config(),
            client_factory=lambda api_key, timeout: fake_client,
            max_retries=0,
        )

        with self.assertRaises(ProviderConnectionError) as context:
            adapter.extract(_request(), _document())

        self.assertEqual(context.exception.code, "PROVIDER_CONNECTION_ERROR")
        self.assertTrue(context.exception.retryable)
        self.assertEqual(
            context.exception.safe_diagnostics(),
            {
                "exception_class": "_NetworkSdkError",
                "exception_module": _NetworkSdkError.__module__,
            },
        )
        self.assertNotIn("SECRET_TEST_KEY", str(context.exception))


@pytest.mark.parametrize("mime,content", [
    ("application/pdf", b"%PDF-1.7 synthetic SDK fixture"),
    ("image/png", b"\x89PNG\r\n\x1a\nsynthetic SDK fixture"),
    ("image/jpeg", b"\xff\xd8\xffsynthetic SDK fixture"),
], ids=["pdf", "png", "jpeg"])
@pytest.mark.parametrize("structured", [False, True], ids=["json-mode", "json-schema"])
def test_gemini_38_installed_sdk_serializes_documents_and_parses_canonical_output(monkeypatch, mime, content, structured):
    """Exercise the installed SDK HTTP conversion with transport blocked offline."""
    from google import genai
    from google.genai import types

    def forbidden(*_args, **_kwargs):
        pytest.fail("SDK compatibility tests must never open a network connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    calls = []

    def respond(request):
        assert request.method == "POST"
        assert request.url.path.endswith("/models/gemini-3.8-flash:generateContent")
        body = json.loads(request.content)
        inline = body["contents"][0]["parts"][0]["inlineData"]
        assert inline["mimeType"] == mime and urlsafe_b64decode(inline["data"]) == content
        generation = body["generationConfig"]
        assert generation["responseMimeType"] == "application/json"
        assert "temperature" not in generation  # Gemini 3.8 migration requirement.
        if structured:
            assert generation["responseJsonSchema"] == build_gemini_response_schema()
        else:
            assert "responseJsonSchema" not in generation
        calls.append(body)
        return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": [
            {"text": json.dumps(_provider_payload())}]}, "finishReason": "STOP"}]})

    with httpx.Client(transport=httpx.MockTransport(respond), trust_env=False) as transport:
        with genai.Client(vertexai=False, api_key="synthetic-offline-key", http_options=types.HttpOptions(
            httpx_client=transport, retry_options=types.HttpRetryOptions(attempts=1))) as client:
            adapter = GeminiExtractionAdapter(config=replace(_authorized_config(), gemini_model="gemini-3.8-flash",
                gemini_structured_outputs_experimental=structured), client_factory=lambda *_: client, max_retries=0)
            result = adapter.extract(_request(), _document(mime, content))
    assert len(calls) == 1 and result.provider.version == "gemini-3.8-flash"
    assert result.logical_forms[0].fields.israeli_id.values[0].raw == "001234567"
    Draft202012Validator(EXTRACTION_SCHEMA).validate(result.model_dump(mode="json", exclude_none=True))


def test_gemini_38_model_defaults_and_explicit_environment_override(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    assert AppConfig().gemini_model == AppConfig.from_env().gemini_model == "gemini-3.8-flash"
    monkeypatch.setenv("GEMINI_MODEL", "explicit-test-model")
    assert AppConfig.from_env().gemini_model == "explicit-test-model"


if __name__ == "__main__":
    unittest.main()
