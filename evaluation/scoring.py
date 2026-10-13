"""Deterministic scoring for Virology Lab IDP extraction results.

The scorer intentionally treats every extracted business value as a string.
Identifier-like fields must preserve leading zeros and demo values that are
not valid real-world identifiers.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

REQUIRED_FIELDS = (
    "first_name",
    "last_name",
    "israeli_id",
    "central_numbers",
    "sample_receipt_date",
    "sender",
    "commitment_numbers",
)

MULTI_VALUE_FIELDS = frozenset({"central_numbers", "commitment_numbers"})

SCORABLE_TRUTH_STATUSES = frozenset({"present", "absent"})
REVIEW_REQUIRED_TRUTH_STATUSES = frozenset({"ambiguous", "unreadable"})
CLEAN_ACCEPTED_REVIEW_STATUSES = frozenset({"approved_as_extracted", "approved_corrected"})
PRESENT_STATUS = "present"
ABSENT_STATUS = "absent"
VERIFIED_STATUS = "verified"


def score_extraction(ground_truth: dict[str, Any], extraction_result: dict[str, Any]) -> dict[str, Any]:
    """Score an extraction result against a versioned ground-truth fixture.

    Unverified fields, including not-yet-annotated fields, are excluded from
    accuracy calculations. Verified fields with statuses other than ``present``
    or ``absent`` are tracked as non-scorable so unknown/unreadable/ambiguous
    values are not conflated with truly absent values.
    """

    truth_forms = _forms_by_id(ground_truth)
    predicted_forms = _forms_by_id(extraction_result)

    field_metrics = {_field: _empty_field_metrics(_field) for _field in REQUIRED_FIELDS}
    silent_error_metrics = _empty_silent_error_metrics()
    overall = {
        "evaluated_fields": 0,
        "exact_matches": 0,
        "missing_values": 0,
        "false_positive_values": 0,
        "skipped_unverified_fields": 0,
        "skipped_non_scorable_fields": 0,
    }

    for form_id, truth_form in truth_forms.items():
        predicted_form = predicted_forms.get(form_id, {})
        truth_fields = truth_form.get("fields", {})
        predicted_fields = predicted_form.get("fields", {})

        for field_name in REQUIRED_FIELDS:
            truth_field = truth_fields.get(field_name, {})
            predicted_field = predicted_fields.get(field_name, {})
            metrics = field_metrics[field_name]

            if truth_field.get("annotation_status") != VERIFIED_STATUS:
                overall["skipped_unverified_fields"] += 1
                metrics["skipped_unverified_fields"] += 1
                continue

            truth_status = truth_field.get("value_status")
            _score_silently_accepted_error(
                silent_error_metrics,
                field_name,
                truth_field,
                predicted_field,
            )

            if truth_status not in SCORABLE_TRUTH_STATUSES:
                overall["skipped_non_scorable_fields"] += 1
                metrics["skipped_non_scorable_fields"] += 1
                continue

            expected = _field_values(truth_field)
            predicted = _field_values(predicted_field)
            expected_set = set(expected)
            predicted_set = set(predicted)

            overall["evaluated_fields"] += 1
            metrics["evaluated_fields"] += 1

            missing_values = 0
            false_positive_values = 0
            exact_match = False

            if truth_status == ABSENT_STATUS:
                exact_match = len(predicted_set) == 0
                false_positive_values = len(predicted_set)
            else:
                exact_match = expected_set == predicted_set
                missing_values = len(expected_set - predicted_set)
                false_positive_values = len(predicted_set - expected_set)

            if exact_match:
                overall["exact_matches"] += 1
                metrics["exact_matches"] += 1

            overall["missing_values"] += missing_values
            overall["false_positive_values"] += false_positive_values
            metrics["missing_values"] += missing_values
            metrics["false_positive_values"] += false_positive_values

            if field_name in MULTI_VALUE_FIELDS:
                _add_multi_value_counts(metrics, expected_set, predicted_set, truth_status)

    _finalize_rates(overall)
    for metrics in field_metrics.values():
        _finalize_rates(metrics)

    return {
        "schema_version": "1.0",
        "fixture_id": ground_truth.get("fixture_id"),
        "extraction_id": extraction_result.get("extraction_id"),
        "provider": deepcopy(extraction_result.get("provider", {})),
        "overall": overall,
        "logical_forms": _logical_form_metrics(truth_forms, predicted_forms),
        "silent_errors": _finalize_silent_error_metrics(silent_error_metrics),
        "fields": field_metrics,
    }


def _forms_by_id(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    forms = payload.get("logical_forms", [])
    return {str(form["form_id"]): form for form in forms}


def _field_values(field: dict[str, Any]) -> list[str]:
    if field.get("value_status") != PRESENT_STATUS:
        return []

    values = []
    for item in field.get("values", []):
        if not isinstance(item, dict):
            raise TypeError("Field values must be objects with raw and optional normalized strings.")
        value = item.get("normalized", item.get("raw"))
        if value is None:
            continue
        if not isinstance(value, str):
            raise TypeError("Field values must remain strings; numeric coercion is not allowed.")
        values.append(value)
    return values


def _empty_field_metrics(field_name: str) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "field_name": field_name,
        "evaluated_fields": 0,
        "exact_matches": 0,
        "exact_match_rate": None,
        "missing_values": 0,
        "false_positive_values": 0,
        "skipped_unverified_fields": 0,
        "skipped_non_scorable_fields": 0,
    }
    if field_name in MULTI_VALUE_FIELDS:
        metrics.update(
            {
                "true_positive_values": 0,
                "precision": None,
                "recall": None,
                "f1": None,
            }
        )
    return metrics


def _logical_form_metrics(
    truth_forms: dict[str, dict[str, Any]],
    predicted_forms: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    truth_ids = set(truth_forms)
    predicted_ids = set(predicted_forms)
    matched_ids = truth_ids & predicted_ids
    expected_count = len(truth_ids)
    predicted_count = len(predicted_ids)

    return {
        "expected_count": expected_count,
        "predicted_count": predicted_count,
        "matched_count": len(matched_ids),
        "missing_count": len(truth_ids - predicted_ids),
        "unexpected_count": len(predicted_ids - truth_ids),
        "count_difference": predicted_count - expected_count,
        "count_accuracy": 1.0 if predicted_count == expected_count else 0.0,
        "detection_recall": _safe_ratio(len(matched_ids), expected_count),
    }


def _empty_silent_error_metrics() -> dict[str, Any]:
    return {
        "evaluated_accepted_fields": 0,
        "silently_accepted_errors": 0,
        "by_field": {
            field_name: {
                "evaluated_accepted_fields": 0,
                "silently_accepted_errors": 0,
            }
            for field_name in REQUIRED_FIELDS
        },
    }


def _score_silently_accepted_error(
    metrics: dict[str, Any],
    field_name: str,
    truth_field: dict[str, Any],
    predicted_field: dict[str, Any],
) -> None:
    review_status = predicted_field.get("review_status")
    if review_status not in CLEAN_ACCEPTED_REVIEW_STATUSES:
        return

    truth_status = truth_field.get("value_status")
    if truth_status not in SCORABLE_TRUTH_STATUSES | REVIEW_REQUIRED_TRUTH_STATUSES:
        return

    expected = set(_field_values(truth_field))
    predicted = set(_field_values(predicted_field))

    if truth_status == ABSENT_STATUS:
        is_error = len(predicted) > 0
    elif truth_status == PRESENT_STATUS:
        is_error = expected != predicted
    else:
        is_error = True

    metrics["evaluated_accepted_fields"] += 1
    if field_name in metrics["by_field"]:
        metrics["by_field"][field_name]["evaluated_accepted_fields"] += 1

    if is_error:
        metrics["silently_accepted_errors"] += 1
        if field_name in metrics["by_field"]:
            metrics["by_field"][field_name]["silently_accepted_errors"] += 1


def _finalize_silent_error_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    metrics["silent_error_rate"] = _safe_ratio(
        metrics["silently_accepted_errors"],
        metrics["evaluated_accepted_fields"],
    )
    for field_metrics in metrics["by_field"].values():
        field_metrics["silent_error_rate"] = _safe_ratio(
            field_metrics["silently_accepted_errors"],
            field_metrics["evaluated_accepted_fields"],
        )
    return metrics


def _add_multi_value_counts(
    metrics: dict[str, Any],
    expected_set: set[str],
    predicted_set: set[str],
    truth_status: str,
) -> None:
    if truth_status == ABSENT_STATUS:
        true_positive_values = 0
        missing_values = 0
        false_positive_values = len(predicted_set)
    else:
        true_positive_values = len(expected_set & predicted_set)
        missing_values = len(expected_set - predicted_set)
        false_positive_values = len(predicted_set - expected_set)

    metrics["true_positive_values"] += true_positive_values
    metrics["missing_values"] += 0  # Already counted at field level above.
    metrics["false_positive_values"] += 0  # Already counted at field level above.
    metrics["_multi_missing_values"] = metrics.get("_multi_missing_values", 0) + missing_values
    metrics["_multi_false_positive_values"] = (
        metrics.get("_multi_false_positive_values", 0) + false_positive_values
    )


def _finalize_rates(metrics: dict[str, Any]) -> None:
    evaluated = metrics["evaluated_fields"]
    metrics["exact_match_rate"] = _safe_ratio(metrics["exact_matches"], evaluated)

    if "true_positive_values" in metrics:
        tp = metrics["true_positive_values"]
        fp = metrics.pop("_multi_false_positive_values", metrics["false_positive_values"])
        fn = metrics.pop("_multi_missing_values", metrics["missing_values"])
        metrics["precision"] = _safe_ratio(tp, tp + fp)
        metrics["recall"] = _safe_ratio(tp, tp + fn)
        metrics["f1"] = _f1(metrics["precision"], metrics["recall"])


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None:
        return None
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)
