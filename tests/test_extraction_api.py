import unittest

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from backend.core.config import AppConfig
from backend.domain import (
    ExtractionFields,
    ExtractionResult,
    FieldResult,
    FieldValue,
    LogicalFormResult,
    ProviderMetadata,
)
from backend.main import create_app
from backend.services.extraction import GeminiExtractionAdapter
from backend.services.extraction.base import DocumentInput
from backend.services.extraction.errors import (
    InvalidProviderResponse,
    ProviderConnectionError,
    ProviderRateLimit,
    ProviderTimeout,
)
from tests.test_domain_models import EXTRACTION_SCHEMA


PDF_BYTES = b"%PDF-1.7\n% synthetic\n"
JPEG_BYTES = b"\xff\xd8\xff\xe0synthetic"
PNG_BYTES = b"\x89PNG\r\n\x1a\nsynthetic"


class FakeExtractionAdapter:
    provider_name = "fake"

    def __init__(self, result=None, error=None):
        self.result = result or _result()
        self.error = error
        self.calls = []

    def extract(self, request, document: DocumentInput):
        self.calls.append((request, document))
        if self.error:
            raise self.error
        return self.result


def _result() -> ExtractionResult:
    return ExtractionResult(
        extraction_id="fake-extraction-001",
        provider=ProviderMetadata(name="fake", mode="mock", version="test"),
        logical_forms=[
            LogicalFormResult(
                form_id="form-001",
                fields=ExtractionFields(
                    israeli_id=FieldResult(
                        value_status="present",
                        values=[FieldValue(raw="001234567")],
                        review_status="unreviewed",
                    ),
                    central_numbers=FieldResult(
                        value_status="present",
                        values=[FieldValue(raw="000001"), FieldValue(raw="000002")],
                        review_status="unreviewed",
                    ),
                    commitment_numbers=FieldResult(
                        value_status="present",
                        values=[FieldValue(raw="AUTH-0007"), FieldValue(raw="AUTH-0008")],
                        review_status="unreviewed",
                    ),
                    sender=FieldResult(
                        value_status="ambiguous",
                        values=[],
                        review_status="needs_review",
                        notes="synthetic ambiguous sender",
                    ),
                ),
            )
        ],
    )


def _multi_form_result() -> ExtractionResult:
    result = _result()
    second_form = LogicalFormResult(
        form_id="form-002",
        fields=ExtractionFields(
            central_numbers=FieldResult(
                value_status="present",
                values=[FieldValue(raw="000003")],
                review_status="unreviewed",
            )
        ),
    )
    return result.model_copy(update={"logical_forms": [result.logical_forms[0], second_form]})


def _invalid_result() -> ExtractionResult:
    return ExtractionResult.model_construct(
        schema_version="1.0",
        extraction_id="",
        provider=ProviderMetadata(name="fake", mode="mock"),
        logical_forms=[],
    )


def _client(adapter, *, max_upload_bytes=1024) -> TestClient:
    app = create_app(
        config=AppConfig(max_upload_bytes=max_upload_bytes),
        extraction_adapter=adapter,
    )
    return TestClient(app)


def _post(client: TestClient, content: bytes, content_type: str, filename="synthetic.pdf", data=None):
    return client.post(
        "/api/v1/extractions",
        data=data or {},
        files={"file": (filename, content, content_type)},
    )


class ExtractionApiTests(unittest.TestCase):
    def test_successful_pdf_upload_with_fake_adapter(self):
        adapter = FakeExtractionAdapter()
        response = _post(_client(adapter), PDF_BYTES, "application/pdf")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(adapter.calls[0][1].mime_type, "application/pdf")
        self.assertEqual(response.json()["provider"]["name"], "fake")

    def test_successful_jpeg_upload_with_fake_adapter(self):
        adapter = FakeExtractionAdapter()
        response = _post(_client(adapter), JPEG_BYTES, "image/jpeg", filename="synthetic.jpg")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(adapter.calls[0][1].mime_type, "image/jpeg")

    def test_successful_png_upload_with_fake_adapter(self):
        adapter = FakeExtractionAdapter()
        response = _post(_client(adapter), PNG_BYTES, "image/png", filename="synthetic.png")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(adapter.calls[0][1].mime_type, "image/png")

    def test_unsupported_file_type_is_rejected_before_adapter_call(self):
        adapter = FakeExtractionAdapter()
        response = _post(_client(adapter), b"not a supported document", "text/plain")

        self.assertEqual(response.status_code, 415)
        self.assertEqual(response.json()["error"]["code"], "UNSUPPORTED_DOCUMENT_TYPE")
        self.assertEqual(adapter.calls, [])

    def test_empty_upload_is_rejected_before_adapter_call(self):
        adapter = FakeExtractionAdapter()
        response = _post(_client(adapter), b"", "application/pdf")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "INVALID_UPLOAD")
        self.assertEqual(adapter.calls, [])

    def test_oversized_upload_is_rejected_before_adapter_call(self):
        adapter = FakeExtractionAdapter()
        response = _post(_client(adapter, max_upload_bytes=4), PDF_BYTES, "application/pdf")

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["error"]["code"], "DOCUMENT_TOO_LARGE")
        self.assertEqual(adapter.calls, [])

    def test_spoofed_mime_type_is_rejected_before_adapter_call(self):
        adapter = FakeExtractionAdapter()
        response = _post(_client(adapter), PDF_BYTES, "image/png")

        self.assertEqual(response.status_code, 415)
        self.assertEqual(response.json()["error"]["code"], "UNSUPPORTED_DOCUMENT_TYPE")
        self.assertEqual(adapter.calls, [])

    def test_fake_adapter_returning_multiple_logical_forms_is_preserved(self):
        response = _post(
            _client(FakeExtractionAdapter(result=_multi_form_result())),
            PDF_BYTES,
            "application/pdf",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["form_id"] for item in response.json()["logical_forms"]],
            ["form-001", "form-002"],
        )

    def test_multiple_central_numbers_are_preserved(self):
        response = _post(_client(FakeExtractionAdapter()), PDF_BYTES, "application/pdf")

        values = response.json()["logical_forms"][0]["fields"]["central_numbers"]["values"]
        self.assertEqual([item["raw"] for item in values], ["000001", "000002"])

    def test_multiple_commitment_numbers_are_preserved(self):
        response = _post(_client(FakeExtractionAdapter()), PDF_BYTES, "application/pdf")

        values = response.json()["logical_forms"][0]["fields"]["commitment_numbers"]["values"]
        self.assertEqual([item["raw"] for item in values], ["AUTH-0007", "AUTH-0008"])

    def test_leading_zeros_are_preserved(self):
        response = _post(_client(FakeExtractionAdapter()), PDF_BYTES, "application/pdf")

        self.assertEqual(
            response.json()["logical_forms"][0]["fields"]["israeli_id"]["values"][0]["raw"],
            "001234567",
        )

    def test_ambiguous_fields_marked_for_review_are_preserved(self):
        response = _post(_client(FakeExtractionAdapter()), PDF_BYTES, "application/pdf")

        sender = response.json()["logical_forms"][0]["fields"]["sender"]
        self.assertEqual(sender["value_status"], "ambiguous")
        self.assertEqual(sender["review_status"], "needs_review")

    def test_unauthorized_external_processing_is_blocked(self):
        factory_calls = []

        def client_factory(api_key, timeout):
            factory_calls.append((api_key, timeout))
            raise AssertionError("Gemini client must not be created when policy blocks")

        config = AppConfig(
            ai_outbound_enabled=False,
            allow_external_phi=False,
            approved_ai_providers=(),
            gemini_api_key="placeholder",
            default_extraction_adapter="gemini",
        )
        adapter = GeminiExtractionAdapter(config=config, client_factory=client_factory)
        client = TestClient(create_app(config=config, extraction_adapter=adapter))

        response = _post(
            client,
            PDF_BYTES,
            "application/pdf",
            data={"allow_external_processing": "true"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "PROVIDER_NOT_AUTHORIZED")
        self.assertEqual(factory_calls, [])

    def test_unauthorized_invalid_upload_causes_zero_provider_calls(self):
        adapter = FakeExtractionAdapter()
        response = _post(_client(adapter), b"not supported", "application/pdf")

        self.assertEqual(response.status_code, 415)
        self.assertEqual(adapter.calls, [])

    def test_adapter_timeout_maps_to_safe_http_error(self):
        adapter = FakeExtractionAdapter(error=ProviderTimeout("AI provider request timed out."))
        response = _post(_client(adapter), PDF_BYTES, "application/pdf")

        self.assertEqual(response.status_code, 504)
        self.assertEqual(response.json()["error"]["code"], "PROVIDER_TIMEOUT")

    def test_rate_limit_maps_to_safe_http_error(self):
        adapter = FakeExtractionAdapter(error=ProviderRateLimit("AI provider rate limit was reached."))
        response = _post(_client(adapter), PDF_BYTES, "application/pdf")

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"]["code"], "PROVIDER_RATE_LIMIT")

    def test_invalid_extraction_result_is_rejected(self):
        adapter = FakeExtractionAdapter(result=_invalid_result())
        response = _post(_client(adapter), PDF_BYTES, "application/pdf")

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "EXTRACTION_VALIDATION_ERROR")

    def test_invalid_provider_response_maps_to_safe_http_error(self):
        adapter = FakeExtractionAdapter(
            error=InvalidProviderResponse("AI provider response did not match the expected structure.")
        )
        response = _post(_client(adapter), PDF_BYTES, "application/pdf")

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "INVALID_PROVIDER_RESPONSE")

    def test_http_error_response_does_not_expose_provider_diagnostics(self):
        adapter = FakeExtractionAdapter(
            error=ProviderConnectionError(
                "AI provider connection failed.",
                diagnostics={
                    "exception_class": "ConnectError",
                    "exception_module": "httpx",
                    "http_status_code": 503,
                    "provider_status": "UNAVAILABLE",
                },
            )
        )
        response = _post(_client(adapter), PDF_BYTES, "application/pdf")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {
                "error": {
                    "code": "PROVIDER_CONNECTION_ERROR",
                    "message": "AI provider connection failed.",
                    "retryable": True,
                }
            },
        )

    def test_success_response_validates_against_canonical_schema(self):
        response = _post(_client(FakeExtractionAdapter()), PDF_BYTES, "application/pdf")

        self.assertEqual(response.status_code, 200)
        Draft202012Validator(EXTRACTION_SCHEMA).validate(response.json())

    def test_fake_adapter_tests_make_no_external_network_requests(self):
        adapter = FakeExtractionAdapter()
        response = _post(_client(adapter), PDF_BYTES, "application/pdf")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(adapter.calls[0][0].provider_policy.data_boundary, "local_only")


if __name__ == "__main__":
    unittest.main()
