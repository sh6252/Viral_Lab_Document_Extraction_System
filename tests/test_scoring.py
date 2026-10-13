import copy
import unittest

from evaluation.scoring import score_extraction


REQUIRED_FIELDS = (
    "first_name",
    "last_name",
    "israeli_id",
    "central_numbers",
    "sample_receipt_date",
    "sender",
    "commitment_numbers",
)


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


def ground_truth(fields):
    complete_fields = {name: field("absent") for name in REQUIRED_FIELDS}
    complete_fields.update(fields)
    return {
        "schema_version": "1.0",
        "fixture_id": "synthetic-fixture",
        "fixture_set": "dev",
        "source_document": {
            "filename": "synthetic.pdf",
            "sha256": "synthetic",
            "page_count": 1,
        },
        "logical_forms": [
            {
                "form_id": "form-1",
                "page_indices": [0],
                "fields": complete_fields,
            }
        ],
    }


def extraction(fields, provider_name="any-provider"):
    return {
        "schema_version": "1.0",
        "extraction_id": "result-1",
        "provider": {
            "name": provider_name,
            "mode": "mock",
            "version": "test",
        },
        "logical_forms": [
            {
                "form_id": "form-1",
                "fields": fields,
            }
        ],
    }


class ScoringTests(unittest.TestCase):
    def test_exact_single_value_match_counts_as_exact(self):
        score = score_extraction(
            ground_truth({"first_name": field("present", ["Dana"])}),
            extraction({"first_name": result_field("present", ["Dana"])}),
        )

        self.assertEqual(score["fields"]["first_name"]["evaluated_fields"], 1)
        self.assertEqual(score["fields"]["first_name"]["exact_matches"], 1)
        self.assertEqual(score["fields"]["first_name"]["exact_match_rate"], 1.0)

    def test_identifier_strings_preserve_leading_zeros_and_invalid_demo_values(self):
        score = score_extraction(
            ground_truth(
                {
                    "israeli_id": field("present", ["001234567"]),
                    "commitment_numbers": field("present", ["INVALID-DEMO-0007"]),
                }
            ),
            extraction(
                {
                    "israeli_id": result_field("present", ["1234567"]),
                    "commitment_numbers": result_field("present", ["INVALID-DEMO-0007"]),
                }
            ),
        )

        self.assertEqual(score["fields"]["israeli_id"]["exact_matches"], 0)
        self.assertEqual(score["fields"]["israeli_id"]["missing_values"], 1)
        self.assertEqual(score["fields"]["israeli_id"]["false_positive_values"], 1)
        self.assertEqual(score["fields"]["commitment_numbers"]["exact_matches"], 1)

    def test_multi_value_precision_recall_for_central_numbers(self):
        score = score_extraction(
            ground_truth({"central_numbers": field("present", ["001", "002", "003"])}),
            extraction({"central_numbers": result_field("present", ["001", "003", "999"])}),
        )

        metrics = score["fields"]["central_numbers"]
        self.assertEqual(metrics["true_positive_values"], 2)
        self.assertEqual(metrics["missing_values"], 1)
        self.assertEqual(metrics["false_positive_values"], 1)
        self.assertAlmostEqual(metrics["precision"], 2 / 3)
        self.assertAlmostEqual(metrics["recall"], 2 / 3)
        self.assertAlmostEqual(metrics["f1"], 2 / 3)

    def test_present_truth_with_missing_prediction_counts_missing_value(self):
        score = score_extraction(
            ground_truth({"last_name": field("present", ["Cohen"])}),
            extraction({"last_name": result_field("missing")}),
        )

        self.assertEqual(score["fields"]["last_name"]["missing_values"], 1)
        self.assertEqual(score["fields"]["last_name"]["false_positive_values"], 0)
        self.assertEqual(score["fields"]["last_name"]["exact_matches"], 0)

    def test_absent_truth_with_prediction_counts_false_positive(self):
        score = score_extraction(
            ground_truth({"sender": field("absent")}),
            extraction({"sender": result_field("present", ["Clalit"])}),
        )

        self.assertEqual(score["fields"]["sender"]["missing_values"], 0)
        self.assertEqual(score["fields"]["sender"]["false_positive_values"], 1)
        self.assertEqual(score["fields"]["sender"]["exact_matches"], 0)

    def test_absent_truth_without_prediction_is_exact(self):
        score = score_extraction(
            ground_truth({"sample_receipt_date": field("absent")}),
            extraction({"sample_receipt_date": result_field("missing")}),
        )

        self.assertEqual(score["fields"]["sample_receipt_date"]["exact_matches"], 1)
        self.assertEqual(score["fields"]["sample_receipt_date"]["exact_match_rate"], 1.0)

    def test_unverified_not_annotated_field_is_excluded_from_accuracy(self):
        score = score_extraction(
            ground_truth(
                {
                    "first_name": field(
                        "not_annotated",
                        annotation_status="unverified",
                    )
                }
            ),
            extraction({"first_name": result_field("present", ["Wrong"])}),
        )

        self.assertEqual(score["fields"]["first_name"]["evaluated_fields"], 0)
        self.assertEqual(score["fields"]["first_name"]["skipped_unverified_fields"], 1)
        self.assertIsNone(score["fields"]["first_name"]["exact_match_rate"])
        self.assertEqual(score["fields"]["first_name"]["false_positive_values"], 0)

    def test_verified_unknown_is_not_treated_as_absent(self):
        score = score_extraction(
            ground_truth({"first_name": field("unknown")}),
            extraction({"first_name": result_field("present", ["Dana"])}),
        )

        self.assertEqual(score["fields"]["first_name"]["evaluated_fields"], 0)
        self.assertEqual(score["fields"]["first_name"]["skipped_non_scorable_fields"], 1)
        self.assertEqual(score["fields"]["first_name"]["false_positive_values"], 0)

    def test_common_provider_format_accepts_arbitrary_provider_name(self):
        score = score_extraction(
            ground_truth({"first_name": field("present", ["Dana"])}),
            extraction(
                {"first_name": result_field("present", ["Dana"])},
                provider_name="future-approved-vision-provider",
            ),
        )

        self.assertEqual(score["provider"]["name"], "future-approved-vision-provider")
        self.assertEqual(score["fields"]["first_name"]["exact_matches"], 1)

    def test_numeric_values_are_rejected_to_prevent_identifier_coercion(self):
        bad_result = extraction({"israeli_id": {"value_status": "present", "values": [{"raw": 123}]}})

        with self.assertRaises(TypeError):
            score_extraction(ground_truth({"israeli_id": field("present", ["123"])}), bad_result)

    def test_scoring_does_not_mutate_provider_payload(self):
        result = extraction({"first_name": result_field("present", ["Dana"])})
        original = copy.deepcopy(result)

        score_extraction(ground_truth({"first_name": field("present", ["Dana"])}), result)

        self.assertEqual(result, original)

    def test_logical_form_metrics_count_matched_missing_and_unexpected_forms(self):
        truth = ground_truth({"first_name": field("present", ["Dana"])})
        truth["logical_forms"].append(
            {
                "form_id": "form-2",
                "page_indices": [1],
                "fields": {name: field("absent") for name in REQUIRED_FIELDS},
            }
        )
        result = extraction({"first_name": result_field("present", ["Dana"])})
        result["logical_forms"].append(
            {
                "form_id": "unexpected-form",
                "fields": {},
            }
        )

        score = score_extraction(truth, result)

        self.assertEqual(score["logical_forms"]["expected_count"], 2)
        self.assertEqual(score["logical_forms"]["predicted_count"], 2)
        self.assertEqual(score["logical_forms"]["matched_count"], 1)
        self.assertEqual(score["logical_forms"]["missing_count"], 1)
        self.assertEqual(score["logical_forms"]["unexpected_count"], 1)
        self.assertEqual(score["logical_forms"]["count_accuracy"], 1.0)
        self.assertEqual(score["logical_forms"]["detection_recall"], 0.5)

    def test_silently_accepted_wrong_value_is_counted_when_review_status_is_available(self):
        score = score_extraction(
            ground_truth({"israeli_id": field("present", ["001234567"])}),
            extraction(
                {
                    "israeli_id": result_field(
                        "present",
                        ["1234567"],
                        review_status="approved_as_extracted",
                    )
                }
            ),
        )

        self.assertEqual(score["silent_errors"]["evaluated_accepted_fields"], 1)
        self.assertEqual(score["silent_errors"]["silently_accepted_errors"], 1)
        self.assertEqual(score["silent_errors"]["silent_error_rate"], 1.0)
        self.assertEqual(
            score["silent_errors"]["by_field"]["israeli_id"]["silently_accepted_errors"],
            1,
        )

    def test_silent_error_metric_is_not_evaluated_without_review_status(self):
        score = score_extraction(
            ground_truth({"israeli_id": field("present", ["001234567"])}),
            extraction({"israeli_id": result_field("present", ["1234567"])}),
        )

        self.assertEqual(score["silent_errors"]["evaluated_accepted_fields"], 0)
        self.assertEqual(score["silent_errors"]["silently_accepted_errors"], 0)
        self.assertIsNone(score["silent_errors"]["silent_error_rate"])

    def test_silently_accepted_ambiguous_truth_is_counted_as_error(self):
        score = score_extraction(
            ground_truth({"commitment_numbers": field("ambiguous", ["AUTH-0007"])}),
            extraction(
                {
                    "commitment_numbers": result_field(
                        "present",
                        ["AUTH-0007"],
                        review_status="approved_as_extracted",
                    )
                }
            ),
        )

        self.assertEqual(score["silent_errors"]["evaluated_accepted_fields"], 1)
        self.assertEqual(score["silent_errors"]["silently_accepted_errors"], 1)


if __name__ == "__main__":
    unittest.main()
