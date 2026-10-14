import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator
from pydantic import ValidationError

from backend.domain.models import (
    Evidence,
    ExtractionFields,
    ExtractionPage,
    ExtractionRequest,
    ExtractionResult,
    FieldResult,
    FieldValue,
    LogicalFormResult,
    ProviderPolicySnapshot,
    ProviderMetadata,
)


ROOT = Path(__file__).resolve().parents[1]
EXTRACTION_SCHEMA = json.loads(
    (ROOT / "evaluation" / "schemas" / "extraction_result.schema.json").read_text(
        encoding="utf-8"
    )
)


class DomainModelTests(unittest.TestCase):
    def test_canonical_extraction_result_validates(self):
        result = ExtractionResult(
            extraction_id="synthetic-result-1",
            provider=ProviderMetadata(name="mock-provider", mode="mock", version="test"),
            logical_forms=[
                LogicalFormResult(
                    form_id="form-1",
                    fields=ExtractionFields(
                        first_name=FieldResult(
                            value_status="present",
                            values=[FieldValue(raw="Dana")],
                            review_status="unreviewed",
                        ),
                        last_name=FieldResult(value_status="present", values=[FieldValue(raw="Cohen")]),
                        israeli_id=FieldResult(
                            value_status="present",
                            values=[FieldValue(raw="001234567")],
                        ),
                        central_numbers=FieldResult(
                            value_status="present",
                            values=[FieldValue(raw="0001"), FieldValue(raw="0002")],
                        ),
                        sample_receipt_date=FieldResult(
                            value_status="present",
                            values=[FieldValue(raw="2026-10-14", normalized="2026-10-14")],
                        ),
                        sender=FieldResult(value_status="present", values=[FieldValue(raw="Synthetic Sender")]),
                        commitment_numbers=FieldResult(
                            value_status="present",
                            values=[FieldValue(raw="AUTH-0007")],
                            evidence=[
                                Evidence(
                                    page_index=0,
                                    source_kind="ocr_text",
                                    text_snippet="synthetic evidence",
                                )
                            ],
                            signals={"pattern_match": True},
                        ),
                    ),
                )
            ],
        )

        self.assertEqual(result.logical_forms[0].fields.israeli_id.values[0].raw, "001234567")

    def test_missing_field_value_is_represented_without_guessing(self):
        field = FieldResult(value_status="missing", values=[], review_status="needs_review")

        self.assertEqual(field.value_status, "missing")
        self.assertEqual(field.values, [])

    def test_multi_value_identifiers_preserve_string_values_and_leading_zeros(self):
        fields = ExtractionFields(
            central_numbers=FieldResult(
                value_status="present",
                values=[FieldValue(raw="0001"), FieldValue(raw="0002")],
            ),
            commitment_numbers=FieldResult(
                value_status="present",
                values=[FieldValue(raw="REQ-0003"), FieldValue(raw="REQ-0004")],
            ),
        )

        self.assertEqual([item.raw for item in fields.central_numbers.values], ["0001", "0002"])
        self.assertEqual(
            [item.raw for item in fields.commitment_numbers.values],
            ["REQ-0003", "REQ-0004"],
        )

    def test_numeric_identifier_values_are_rejected(self):
        with self.assertRaises(ValidationError):
            FieldResult(value_status="present", values=[FieldValue(raw=123456789)])

    def test_invalid_field_structures_are_rejected(self):
        with self.assertRaises(ValidationError):
            ExtractionFields(unexpected_field=FieldResult(value_status="missing", values=[]))

    def test_serialized_extraction_result_validates_against_existing_schema(self):
        result = ExtractionResult(
            extraction_id="synthetic-result-1",
            provider=ProviderMetadata(name="mock-provider", mode="mock"),
            logical_forms=[
                LogicalFormResult(
                    form_id="form-1",
                    fields=ExtractionFields(
                        israeli_id=FieldResult(
                            value_status="present",
                            values=[FieldValue(raw="001234567")],
                            review_status="approved_as_extracted",
                        ),
                        sender=FieldResult(value_status="missing", values=[]),
                    ),
                )
            ],
        )
        payload = result.model_dump(mode="json", exclude_none=True)

        Draft202012Validator(EXTRACTION_SCHEMA).validate(payload)

    def test_extraction_request_preserves_document_identity_and_pipeline_version(self):
        request = ExtractionRequest(
            request_id="request-1",
            logical_form_id="form-1",
            document_id="document-1",
            pages=[
                ExtractionPage(
                    page_id="page-1",
                    page_index=0,
                    image_uri="storage://rendered/page-1.png",
                    width=2480,
                    height=3508,
                    rotation_degrees=0,
                    ocr_text_uri="storage://ocr/page-1.json",
                )
            ],
            fields=["first_name", "israeli_id", "central_numbers"],
            pipeline_version="pipeline-test",
            provider_policy=ProviderPolicySnapshot(),
        )

        self.assertEqual(request.document_id, "document-1")
        self.assertEqual(request.pipeline_version, "pipeline-test")
        self.assertFalse(request.provider_policy.allow_external_phi)


if __name__ == "__main__":
    unittest.main()
