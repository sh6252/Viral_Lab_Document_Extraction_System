import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator

from backend.services.extraction import DocumentInput
from backend.services.extraction.errors import ProviderConnectionError
from backend.services.extraction.schema_validation import validate_extraction_result_schema
from backend.tools.extraction_smoke import (
    OfflineSmokeExtractionAdapter,
    SmokeRunOptions,
    SYNTHETIC_GROUND_TRUTH_PATH,
    SYNTHETIC_PDF_PATH,
    _build_request,
    _build_parser,
    create_synthetic_fixture,
    load_json,
    run_smoke_test,
)
from evaluation.scoring import score_extraction


ROOT = Path(__file__).resolve().parents[1]
GROUND_TRUTH_SCHEMA = json.loads(
    (ROOT / "evaluation" / "schemas" / "ground_truth.schema.json").read_text(
        encoding="utf-8"
    )
)


class ExtractionSmokeTests(unittest.TestCase):
    def test_provider_choices_follow_registry(self):
        with patch(
            "backend.tools.extraction_smoke.registered_provider_names",
            return_value=("future_provider",),
        ):
            args = _build_parser().parse_args(["--provider", "future_provider"])
        self.assertEqual(args.provider, "future_provider")

    def test_openai_requires_explicit_cli_authorization(self):
        with patch("backend.tools.extraction_smoke.build_extraction_adapter") as factory:
            report = run_smoke_test(SmokeRunOptions(provider="openai"))
        factory.assert_not_called()
        self.assertEqual(report["errors"][0]["code"], "PROVIDER_NOT_AUTHORIZED")
        self.assertEqual(report["provider"]["name"], "openai")

    def test_conflicting_provider_flags_do_not_construct_any_live_adapter(self):
        with patch("backend.tools.extraction_smoke.build_extraction_adapter") as factory:
            report = run_smoke_test(SmokeRunOptions(
                provider="openai", live_gemini=True, authorize_external_processing=True,
            ))
        factory.assert_not_called()
        self.assertEqual(report["errors"][0]["code"], "PROVIDER_NOT_AUTHORIZED")

    def test_openai_cli_selection_reaches_factory_and_propagates_request_policy(self):
        adapter = OfflineSmokeExtractionAdapter(load_json(SYNTHETIC_GROUND_TRUTH_PATH))
        with patch(
            "backend.tools.extraction_smoke.build_extraction_adapter", return_value=adapter
        ) as factory:
            report = run_smoke_test(SmokeRunOptions(
                provider="openai", authorize_external_processing=True,
            ))
        self.assertEqual(factory.call_args.args[0].default_extraction_adapter, "openai")
        self.assertEqual(report["extraction_status"], "succeeded")
        self.assertEqual(report["prompt_version"], "he-commitment-v1")
        self.assertTrue(report["pipeline_version"])
        request = _build_request(
            options=SmokeRunOptions(provider="openai", authorize_external_processing=True),
            ground_truth=load_json(SYNTHETIC_GROUND_TRUTH_PATH),
        )
        self.assertTrue(request.provider_policy.allow_external_phi)

    def test_synthetic_pdf_creation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "synthetic.pdf"
            truth_path = Path(temp_dir) / "synthetic.ground_truth.json"

            create_synthetic_fixture(pdf_path=pdf_path, ground_truth_path=truth_path)

            pdf_bytes = pdf_path.read_bytes()
            self.assertTrue(pdf_bytes.startswith(b"%PDF-"))
            self.assertTrue(pdf_bytes.rstrip().endswith(b"%%EOF"))
            self.assertGreater(pdf_path.stat().st_size, 500)
            self.assertTrue(truth_path.exists())

    def test_ground_truth_schema_validity(self):
        ground_truth = load_json(SYNTHETIC_GROUND_TRUTH_PATH)

        Draft202012Validator(GROUND_TRUTH_SCHEMA).validate(ground_truth)
        self.assertEqual(ground_truth["fixture_set"], "dev")

    def test_offline_mock_execution_succeeds(self):
        report = run_smoke_test(SmokeRunOptions())

        self.assertEqual(report["extraction_status"], "succeeded")
        self.assertEqual(report["provider"]["name"], "offline_smoke_mock")
        self.assertEqual(report["errors"], [])

    def test_canonical_extraction_result_validation(self):
        ground_truth = load_json(SYNTHETIC_GROUND_TRUTH_PATH)
        document_bytes = SYNTHETIC_PDF_PATH.read_bytes()
        request = _build_request(options=SmokeRunOptions(), ground_truth=ground_truth)
        adapter = OfflineSmokeExtractionAdapter(ground_truth)

        result = adapter.extract(
            request,
            document=DocumentInput(
                content=document_bytes,
                mime_type="application/pdf",
                filename=SYNTHETIC_PDF_PATH.name,
            ),
        )

        validate_extraction_result_schema(result)

    def test_correct_evaluation_scoring(self):
        ground_truth = load_json(SYNTHETIC_GROUND_TRUTH_PATH)
        report = run_smoke_test(SmokeRunOptions())

        scoring = report["scoring"]
        self.assertEqual(scoring["overall"]["evaluated_fields"], 7)
        self.assertEqual(scoring["overall"]["exact_matches"], 7)
        self.assertEqual(scoring["fields"]["central_numbers"]["precision"], 1.0)
        self.assertEqual(scoring["fields"]["central_numbers"]["recall"], 1.0)

        recomputed = score_extraction(
            ground_truth,
            {
                "schema_version": "1.0",
                "extraction_id": scoring["extraction_id"],
                "provider": scoring["provider"],
                "logical_forms": [
                    {
                        "form_id": "smoke-form-001",
                        "fields": {
                            name: {
                                "value_status": "present",
                                "values": [
                                    {"raw": value}
                                    for value in field_report["actual_values"]
                                ],
                            }
                            for name, field_report in report["field_results"].items()
                        },
                    }
                ],
            },
        )
        self.assertEqual(recomputed["overall"]["exact_matches"], 7)

    def test_preserves_leading_zeros(self):
        report = run_smoke_test(SmokeRunOptions())

        self.assertEqual(
            report["field_results"]["israeli_id"]["actual_values"],
            ["001234567"],
        )

    def test_preserves_multiple_central_numbers(self):
        report = run_smoke_test(SmokeRunOptions())

        self.assertEqual(
            report["field_results"]["central_numbers"]["actual_values"],
            ["00012345", "00012346"],
        )

    def test_preserves_multiple_commitment_numbers(self):
        report = run_smoke_test(SmokeRunOptions())

        self.assertEqual(
            report["field_results"]["commitment_numbers"]["actual_values"],
            ["HT-00077", "HT-00078"],
        )

    def test_missing_and_ambiguous_values_are_reported(self):
        report = run_smoke_test(SmokeRunOptions(mock_scenario="needs_review"))

        self.assertEqual(
            report["field_results"]["sample_receipt_date"]["value_status"],
            "missing",
        )
        self.assertEqual(report["field_results"]["sender"]["value_status"], "ambiguous")
        self.assertEqual(report["field_results"]["sender"]["review_status"], "needs_review")
        self.assertGreater(report["scoring"]["overall"]["missing_values"], 0)

    def test_unauthorized_external_execution_is_rejected(self):
        report = run_smoke_test(
            SmokeRunOptions(
                live_gemini=True,
                authorize_external_processing=False,
            )
        )

        self.assertEqual(report["extraction_status"], "failed")
        self.assertEqual(report["errors"][0]["code"], "PROVIDER_NOT_AUTHORIZED")

    def test_default_mode_makes_no_provider_network_calls(self):
        with patch(
            "backend.tools.extraction_smoke.build_extraction_adapter",
            side_effect=AssertionError("Live adapter must not be constructed"),
        ):
            report = run_smoke_test(SmokeRunOptions())

        self.assertEqual(report["extraction_status"], "succeeded")

    def test_safe_error_reporting_does_not_expose_secrets_or_document_content(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY": "VERY_SECRET_TEST_KEY"}, clear=False):
            report = run_smoke_test(
                SmokeRunOptions(
                    live_gemini=True,
                    authorize_external_processing=False,
                )
            )

        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("VERY_SECRET_TEST_KEY", serialized)
        self.assertNotIn("%PDF-1.4", serialized)
        self.assertIn("PROVIDER_NOT_AUTHORIZED", serialized)

    def test_live_failure_report_includes_only_safe_diagnostics(self):
        class _FailingAdapter:
            def extract(self, request, document):
                raise ProviderConnectionError(
                    "AI provider connection failed.",
                    diagnostics={
                        "exception_class": "ConnectError",
                        "exception_module": "httpx",
                    },
                )

        with patch(
            "backend.tools.extraction_smoke.build_extraction_adapter",
            return_value=_FailingAdapter(),
        ), patch.dict("os.environ", {"GEMINI_API_KEY": "VERY_SECRET_TEST_KEY"}, clear=False):
            report = run_smoke_test(
                SmokeRunOptions(
                    live_gemini=True,
                    authorize_external_processing=True,
                    redact=True,
                )
            )

        self.assertEqual(report["extraction_status"], "failed")
        self.assertEqual(report["errors"][0]["code"], "PROVIDER_CONNECTION_ERROR")
        self.assertEqual(
            report["errors"][0]["diagnostics"],
            {
                "exception_class": "ConnectError",
                "exception_module": "httpx",
            },
        )
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("VERY_SECRET_TEST_KEY", serialized)
        self.assertNotIn("%PDF-1.4", serialized)
        self.assertIn("[REDACTED]", serialized)


if __name__ == "__main__":
    unittest.main()
