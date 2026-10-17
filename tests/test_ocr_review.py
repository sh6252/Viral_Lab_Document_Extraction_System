"""Synthetic human declarations only: no source images, OCR or provider calls."""

import json
from dataclasses import FrozenInstanceError, asdict, replace
from datetime import datetime, timezone

import pytest

from backend.services.extraction.ocr_candidates import ProviderValue
from backend.services.extraction.ocr_review import (
    OCRReviewDecision,
    OCRReviewItem,
    apply_review_decision,
    candidate_reference,
)
from tests.test_ocr_candidates import IMAGE_HASH, candidate, canonical


def decision(item, **changes):
    source = item.candidate
    data = dict(
        decision_id="decision-1", candidate_id=item.candidate_id, decision="approved",
        field_assignment_status="confirmed", value_verification_status="confirmed",
        reviewed_value=source.raw_value, review_reason="Synthetic exact source verification",
        reviewer_reference="synthetic-reviewer", reviewed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        source_image_sha256=source.source_image_sha256, document_id=source.document_id,
        form_id=source.form_id, page_number=source.page_number,
    )
    data.update(changes)
    return OCRReviewDecision(**data)


def test_no_review_even_with_provider_agreement_leaves_candidate_unverified():
    item = OCRReviewItem(candidate(), (ProviderValue("001"),))
    assert item.field_assignment_status == item.value_verification_status == "unverified"
    assert item.review_state == "unverified"
    assert item.approved_value is None
    assert item.review_reason is None
    assert not item.human_correction_occurred
    assert json.loads(item.to_json())["review_decision"] is None


@pytest.mark.parametrize("field_name", ["central_numbers", "commitment_numbers"])
def test_both_confirmed_with_explicit_decision_approve_separate_value(field_name):
    item = OCRReviewItem(candidate("944963", name=field_name))
    reviewed = apply_review_decision(item, decision(item))
    assert reviewed.review_state == "approved"
    assert reviewed.approved_value == "944963"
    assert reviewed.candidate.raw_value == "944963"
    assert reviewed.candidate.validation_status == "syntax_screened_unverified"
    assert item.review_state == "unverified"
    assert reviewed.field_assignment_status == reviewed.value_verification_status == "confirmed"
    assert not reviewed.human_correction_occurred


@pytest.mark.parametrize("check", ["field_assignment_status", "value_verification_status"])
@pytest.mark.parametrize("status", ["unverified", "unresolved", "rejected"])
def test_either_unconfirmed_check_blocks_approval(check, status):
    item = OCRReviewItem(candidate())
    with pytest.raises(ValueError, match="APPROVAL_REQUIRES_BOTH_CHECKS"):
        decision(item, **{check: status})


def test_field_assignment_alone_does_not_validate_wrong_digits_or_mutate_canonical():
    result = canonical(("947328",), review="needs_review")
    before = result.model_dump(mode="json")
    item = OCRReviewItem(candidate("47028"), (ProviderValue("947328"),))
    rejected = apply_review_decision(item, decision(
        item, decision="rejected", value_verification_status="rejected",
        review_reason="Synthetic verification: OCR digits differ from image",
    ))
    assert rejected.review_state == "rejected"
    assert rejected.approved_value is None
    assert rejected.field_assignment_status == "confirmed"
    assert rejected.value_verification_status == "rejected"
    assert rejected.candidate is item.candidate
    assert rejected.candidate.raw_value == "47028"
    assert rejected.review_reason == rejected.review_decision.review_reason
    assert result.model_dump(mode="json") == before
    record = json.loads(rejected.to_json())
    assert record["candidate"]["source_engine"] == "synthetic-ocr"
    assert record["candidate"]["source_run_id"] == "run-1"
    assert record["review_decision"]["source_image_sha256"] == IMAGE_HASH


def test_approval_also_leaves_canonical_provider_values_and_review_status_unchanged():
    result = canonical(("944963",), review="needs_review")
    before = result.model_dump(mode="json")
    field = result.logical_forms[0].fields.central_numbers
    snapshots = tuple(ProviderValue(value.raw, value.normalized) for value in field.values)
    item = OCRReviewItem(candidate("944963"), snapshots)
    reviewed = apply_review_decision(item, decision(item))
    assert reviewed.approved_value == "944963"
    assert reviewed.provider_values == snapshots
    assert result.model_dump(mode="json") == before
    assert field.review_status == "needs_review"


@pytest.mark.parametrize("reviewed_value", ["000947328", "00-947/328)", " 00A/123 "])
def test_explicit_correction_preserves_exact_text_zeros_punctuation_and_raw(reviewed_value):
    item = OCRReviewItem(candidate("47028"))
    reviewed = apply_review_decision(item, decision(item, reviewed_value=reviewed_value))
    assert reviewed.candidate.raw_value == "47028"
    assert reviewed.approved_value == reviewed_value
    assert reviewed.review_decision.reviewed_value == reviewed_value
    assert reviewed.human_correction_occurred
    payload = json.loads(reviewed.to_json())
    assert payload["candidate"]["raw_value"] == "47028"
    assert payload["approved_value"] == reviewed_value
    assert payload["human_correction_occurred"] is True


def test_original_leading_zeroes_are_never_normalized():
    item = OCRReviewItem(candidate("000123", normalized_value="123"))
    reviewed = apply_review_decision(item, decision(item))
    assert reviewed.approved_value == "000123"
    assert reviewed.candidate.normalized_value == "123"
    assert not reviewed.human_correction_occurred


@pytest.mark.parametrize("state", ["unresolved", "rejected"])
def test_nonapproval_cannot_expose_tentative_reviewed_value_for_use(state):
    item = OCRReviewItem(candidate())
    reviewed = apply_review_decision(item, decision(
        item, decision=state, field_assignment_status="unresolved", reviewed_value="001/2",
    ))
    assert reviewed.review_state == state
    assert reviewed.approved_value is None
    assert reviewed.review_decision.reviewed_value == "001/2"
    assert reviewed.human_correction_occurred


def test_explicit_rejection_does_not_require_a_replacement_value():
    item = OCRReviewItem(candidate())
    rejected = apply_review_decision(item, decision(
        item, decision="rejected", value_verification_status="rejected", reviewed_value=None,
    ))
    assert rejected.approved_value is None
    assert not rejected.human_correction_occurred


def test_rejected_check_cannot_be_hidden_under_unresolved():
    item = OCRReviewItem(candidate())
    with pytest.raises(ValueError, match="REJECTED_CHECK_REQUIRES_REJECTION"):
        decision(item, decision="unresolved", value_verification_status="rejected")


def test_candidate_item_decision_and_provider_snapshot_are_immutable():
    item = OCRReviewItem(candidate(), (ProviderValue("123"),))
    before = asdict(item.candidate)
    action = decision(item)
    apply_review_decision(item, action)
    for target, attribute, value in (
        (item.candidate, "raw_value", "99"), (item, "review_decision", action),
        (action, "decision", "rejected"), (item.provider_values[0], "raw_value", "99"),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(target, attribute, value)
    assert asdict(item.candidate) == before


def test_deterministic_reference_equality_and_audit_serialization():
    first = OCRReviewItem(candidate())
    second = OCRReviewItem(candidate())
    assert first == second
    assert first.candidate_id == candidate_reference(second.candidate)
    reviewed = apply_review_decision(first, decision(first))
    same = apply_review_decision(second, decision(second))
    assert reviewed == same
    assert reviewed.to_json() == same.to_json() == reviewed.to_json()
    audit = json.loads(reviewed.to_json())
    assert audit["version"] == "ocr-review-v1"
    assert audit["review_decision"]["reviewer_reference"] == "synthetic-reviewer"
    assert audit["review_decision"]["reviewed_at"] == "2026-01-01T00:00:00+00:00"


@pytest.mark.parametrize("name", ["first_name", "last_name", "israeli_id", "sample_receipt_date", "sender"])
def test_unsupported_field_cannot_enter_review(name):
    with pytest.raises(ValueError, match="UNSUPPORTED_FIELD"):
        OCRReviewItem(candidate(name=name))


@pytest.mark.parametrize("change", [
    {"document_id": "other-doc"}, {"form_id": "other-form"}, {"page_number": 2},
    {"source_image_sha256": "b" * 64}, {"candidate_id": "c" * 64},
])
def test_scope_mismatch_rejected_even_when_both_checks_confirmed(change):
    item = OCRReviewItem(candidate())
    with pytest.raises(ValueError, match="REVIEW_PROVENANCE_MISMATCH"):
        apply_review_decision(item, decision(item, **change))
    assert item.review_state == "unverified"


def test_reference_binds_engine_run_and_assignment_not_just_value():
    original = candidate()
    for different in (
        replace(original, source_run_id="other-run"),
        replace(original, source_engine_version="other-version"),
        replace(original, field_assignment_evidence=replace(
            original.field_assignment_evidence, reference="other-label")),
    ):
        assert candidate_reference(original) != candidate_reference(different)


def test_duplicate_replay_is_idempotent_but_conflicting_decisions_never_overwrite():
    item = OCRReviewItem(candidate())
    action = decision(item)
    reviewed = apply_review_decision(item, action)
    assert apply_review_decision(reviewed, replace(action)) is reviewed
    for conflicting in (replace(action, reviewer_note="Changed same ID"),
                        replace(action, decision_id="another-decision")):
        with pytest.raises(ValueError, match="REVIEW_DECISION_CONFLICT"):
            apply_review_decision(reviewed, conflicting)
    assert reviewed.review_decision == action


@pytest.mark.parametrize("change, error", [
    ({"reviewed_value": None}, "APPROVAL_REQUIRES_BOTH_CHECKS"),
    ({"reviewed_value": " "}, "NONEMPTY_TEXT_REQUIRED"),
    ({"review_reason": ""}, "NONEMPTY_TEXT_REQUIRED"),
    ({"reviewer_reference": ""}, "NONEMPTY_TEXT_REQUIRED"),
    ({"reviewed_at": datetime(2026, 1, 1)}, "TIMEZONE_AWARE_REVIEW_TIME_REQUIRED"),
    ({"page_number": True}, "ONE_BASED_PAGE_REQUIRED"),
    ({"source_image_sha256": "bad"}, "LOWERCASE_SHA256_REQUIRED"),
    ({"decision": "auto_approved"}, "UNKNOWN_REVIEW_DECISION"),
    ({"value_verification_status": "probably"}, "UNKNOWN_VERIFICATION_STATUS"),
])
def test_malformed_or_incomplete_decisions_fail_explicitly(change, error):
    with pytest.raises(ValueError, match=error):
        decision(OCRReviewItem(candidate()), **change)


def test_mutable_provider_list_is_not_accepted():
    with pytest.raises(ValueError, match="IMMUTABLE_PROVIDER_SNAPSHOTS_REQUIRED"):
        OCRReviewItem(candidate(), [ProviderValue("001")])


def test_non_candidate_and_non_decision_inputs_rejected():
    with pytest.raises(ValueError, match="SCREENED_CANDIDATE_REQUIRED"):
        OCRReviewItem(None)
    item = OCRReviewItem(candidate())
    with pytest.raises(ValueError, match="EXPLICIT_REVIEW_DECISION_REQUIRED"):
        OCRReviewItem(item.candidate, review_decision={})
    with pytest.raises(ValueError, match="REVIEW_ITEM_AND_DECISION_REQUIRED"):
        apply_review_decision(item, None)
