import copy
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "evaluation" / "schemas"


def load_schema(name):
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def field(status, values=None, annotation_status="verified"):
    return {
        "annotation_status": annotation_status,
        "value_status": status,
        "values": [{"raw": value} for value in (values or [])],
    }


def result_field(status, values=None, review_status=None):
    payload = {
        "value_status": status,
        "values": [{"raw": value} for value in (values or [])],
    }
    if review_status is not None:
        payload["review_status"] = review_status
    return payload


class SchemaValidationTests(unittest.TestCase):
    def setUp(self):
        self.ground_truth_validator = Draft202012Validator(
            load_schema("ground_truth.schema.json")
        )
        self.extraction_result_validator = Draft202012Validator(
            load_schema("extraction_result.schema.json")
        )

    def test_ground_truth_schema_accepts_fields_container_and_absent_truth(self):
        payload = {
            "schema_version": "1.0",
            "fixture_id": "synthetic-dev-001",
            "fixture_set": "dev",
            "source_document": {
                "filename": "synthetic.pdf",
                "sha256": "synthetic-sha256",
                "page_count": 1,
            },
            "logical_forms": [
                {
                    "form_id": "form-1",
                    "page_indices": [0],
                    "fields": {
                        "first_name": field("present", ["Dana"]),
                        "last_name": field("absent"),
                        "israeli_id": field("present", ["001234567"]),
                        "central_numbers": field("present", ["0001", "0002"]),
                        "sample_receipt_date": field("unknown"),
                        "sender": field("not_annotated", annotation_status="unverified"),
                        "commitment_numbers": field("ambiguous", ["AUTH-0007"]),
                    },
                }
            ],
        }

        self.ground_truth_validator.validate(payload)

    def test_ground_truth_schema_rejects_expected_fields_container(self):
        payload = {
            "schema_version": "1.0",
            "fixture_id": "synthetic-dev-001",
            "fixture_set": "dev",
            "source_document": {
                "filename": "synthetic.pdf",
                "sha256": "synthetic-sha256",
                "page_count": 1,
            },
            "logical_forms": [
                {
                    "form_id": "form-1",
                    "page_indices": [0],
                    "expected_fields": {},
                }
            ],
        }

        errors = list(self.ground_truth_validator.iter_errors(payload))

        self.assertTrue(errors)

    def test_ground_truth_schema_rejects_numeric_identifier_values(self):
        payload = {
            "schema_version": "1.0",
            "fixture_id": "synthetic-dev-001",
            "fixture_set": "dev",
            "source_document": {
                "filename": "synthetic.pdf",
                "sha256": "synthetic-sha256",
                "page_count": 1,
            },
            "logical_forms": [
                {
                    "form_id": "form-1",
                    "page_indices": [0],
                    "fields": {
                        "first_name": field("absent"),
                        "last_name": field("absent"),
                        "israeli_id": {
                            "annotation_status": "verified",
                            "value_status": "present",
                            "values": [{"raw": 123456789}],
                        },
                        "central_numbers": field("absent"),
                        "sample_receipt_date": field("absent"),
                        "sender": field("absent"),
                        "commitment_numbers": field("absent"),
                    },
                }
            ],
        }

        errors = list(self.ground_truth_validator.iter_errors(payload))

        self.assertTrue(errors)

    def test_extraction_schema_accepts_missing_output_and_review_status(self):
        payload = {
            "schema_version": "1.0",
            "extraction_id": "synthetic-result-001",
            "provider": {
                "name": "mock-provider",
                "mode": "mock",
                "version": "test",
            },
            "logical_forms": [
                {
                    "form_id": "form-1",
                    "fields": {
                        "israeli_id": result_field(
                            "present",
                            ["001234567"],
                            review_status="approved_as_extracted",
                        ),
                        "sender": result_field("missing", review_status="needs_review"),
                    },
                }
            ],
        }

        self.extraction_result_validator.validate(payload)

    def test_extraction_schema_rejects_absent_output_status(self):
        payload = {
            "schema_version": "1.0",
            "extraction_id": "synthetic-result-001",
            "provider": {
                "name": "mock-provider",
                "mode": "mock",
            },
            "logical_forms": [
                {
                    "form_id": "form-1",
                    "fields": {
                        "sender": result_field("absent"),
                    },
                }
            ],
        }

        errors = list(self.extraction_result_validator.iter_errors(payload))

        self.assertTrue(errors)

    def test_schema_validation_does_not_mutate_payloads(self):
        payload = {
            "schema_version": "1.0",
            "extraction_id": "synthetic-result-001",
            "provider": {
                "name": "mock-provider",
                "mode": "mock",
            },
            "logical_forms": [],
        }
        original = copy.deepcopy(payload)

        self.extraction_result_validator.validate(payload)

        self.assertEqual(payload, original)


if __name__ == "__main__":
    unittest.main()
