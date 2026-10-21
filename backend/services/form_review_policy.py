"""Conservative exception policy; eligibility is never confidence or approval."""

from datetime import date
import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.services.business_review import BusinessReviewResult, ReviewedField

POLICY_VERSION = "human-form-attestation-v1"
RESOLVED = frozenset({"approved_provider", "approved_corrected", "acknowledged_blank", "accepted_by_form"})
MULTIPLE = frozenset({"central_numbers", "commitment_numbers"})


def parsed_date(value: str) -> date | None:
    try:
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            return date.fromisoformat(value)
        match = re.fullmatch(r"([0-9]{1,2})([./])([0-9]{1,2})\2([0-9]{4})", value)
        if match:
            return date(int(match[4]), int(match[3]), int(match[1]))
    except ValueError:
        pass
    return None


def review_reasons(result: "BusinessReviewResult", field: "ReviewedField") -> tuple[str, ...]:
    """Hard exceptions need explicit field resolution before the form attestation.

    An absence of these exceptions conveys no verified AI confidence. Every other
    value still requires the operator's explicit whole-form attestation.
    """
    if field.review_status == "acknowledged_blank":
        return ()
    reviewed = field.review_status in RESOLVED
    values = field.reviewed_values if reviewed else tuple(v.raw for v in field.original.values)
    reasons: set[str] = set()
    if field.review_status in {"unresolved", "rejected_unresolved"}:
        reasons.add("UNRESOLVED_REVIEW")
    if not values or any(not value.strip() for value in values):
        reasons.add("MISSING_VALUE")
    if field.field_name not in MULTIPLE and len(values) > 1:
        reasons.add("CONTRADICTORY_SCALAR_VALUES")
    if field.field_name == "sample_receipt_date" and any(parsed_date(v) is None for v in values):
        reasons.add("INVALID_DATE")
    if not reviewed:
        if field.original.value_status != "present":
            reasons.add("PROVIDER_VALUE_UNCERTAIN")
        if field.original.review_status not in {None, "unreviewed"}:
            reasons.add("PROVIDER_REVIEW_REQUIRED")
        # Inspect the immutable canonical field, not a client-supplied flag.
        form = next(f for f in json.loads(result.canonical_snapshot_json)["logical_forms"]
                    if f["form_id"] == result.record.form_id)
        original = form["fields"][field.field_name]
        if original:
            if original.get("notes", ""):
                reasons.add("PROVIDER_NOTES_REQUIRE_REVIEW")
            if original.get("signals"):
                reasons.add("PROVIDER_SIGNALS_REQUIRE_REVIEW")
        for value in field.original.values:
            normalized = value.normalized
            if normalized is not None:
                invalid = not normalized.strip()
                if field.field_name == "sample_receipt_date":
                    invalid |= parsed_date(normalized) is None or parsed_date(normalized) != parsed_date(value.raw)
                elif field.field_name in {"israeli_id", *MULTIPLE}:
                    # No undocumented identifier normalizer or numeric conversion.
                    invalid |= normalized != value.raw
                if invalid:
                    reasons.add("UNVERIFIED_NORMALIZATION")
    return tuple(sorted(reasons))
