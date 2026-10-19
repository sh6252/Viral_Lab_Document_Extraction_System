"""Synthetic all-seven-field review, exactness, scope and atomic replay tests."""

from dataclasses import FrozenInstanceError, asdict, replace
from datetime import datetime, timezone
import json

import pytest

from backend.domain import ExtractionResult, FieldResult
from backend.services.business_review import (
    FIELDS, MULTI_FIELDS, BusinessReviewAction, BusinessReviewError, OriginalField,
    SourceBinding, apply_business_review, original_field, snapshot_canonical, snapshot_sha256,
    validate_review_result,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def canonical(*, two_forms=False):
    fields = {name: {"value_status": "present", "values": [{"raw": "טקסט בדיקה", "normalized": "NORMALIZED"}],
                     "review_status": "unreviewed"} for name in FIELDS}
    fields["israeli_id"]["values"] = [{"raw": "000123", "normalized": "123"}]
    fields["sample_receipt_date"]["values"] = [{"raw": "01/02/2026"}]
    fields["central_numbers"]["values"] = [{"raw": "0007"}, {"raw": "008/9)"}, {"raw": "0007"}]
    fields["commitment_numbers"]["values"] = [{"raw": "00001"}, {"raw": "00002"}]
    forms = [{"form_id": "form-1", "fields": fields}]
    if two_forms:
        forms.append({"form_id": "form-2", "fields": {"first_name": {"value_status": "unreadable", "values": []}}})
    return ExtractionResult.model_validate({"schema_version": "1.0", "extraction_id": "extract-synthetic",
        "provider": {"name": "mock", "mode": "mock", "version": "synthetic-v1"}, "logical_forms": forms})


def binding(value=None, **changes):
    value = canonical() if value is None else value
    args = dict(document_id="document-synthetic", source_sha256="a" * 64,
                extraction_id=value.extraction_id, canonical_snapshot_sha256=snapshot_sha256(value),
                data_origin="historical_replay", snapshot_kind="preserved",
                prompt_version="synthetic-prompt-v1", pipeline_version="synthetic-pipeline-v1")
    args.update(changes)
    return SourceBinding(**args)


def action(value=None, source=None, *, name="first_name", form="form-1", kind="correct_and_approve",
           values=None, revision=0, decision_id="decision-1", **changes):
    value = canonical() if value is None else value
    source = binding(value) if source is None else source
    original = original_field(value, form, name)
    if values is None:
        values = tuple(v.raw for v in original.values) if kind == "approve_as_extracted" else (
            () if kind in {"reject", "acknowledge_blank", "leave_unresolved"} else ("000123",))
    args = dict(source_sha256=source.source_sha256, extraction_id=source.extraction_id,
        canonical_snapshot_sha256=source.canonical_snapshot_sha256, form_id=form, field_name=name,
        decision_id=decision_id, expected_revision=revision, operator_declaration="Synthetic operator (declared)",
        declared_at=NOW, reason="Synthetic source inspection", original=original, action=kind, reviewed_values=values)
    args.update(changes)
    return BusinessReviewAction(**args)


def reviewed(value=None, actions=(), source=None, **kwargs):
    value = canonical() if value is None else value
    return apply_business_review(value, binding(value) if source is None else source, "form-1", actions, **kwargs)


def complete(value=None):
    value = canonical() if value is None else value
    return reviewed(value, [action(value, name=name, kind="approve_as_extracted", revision=i, decision_id=f"d-{i}")
                            for i, name in enumerate(FIELDS)])


@pytest.mark.parametrize("name", FIELDS)
def test_approve_each_field_preserves_whole_raw_list(name):
    original = canonical()
    result = reviewed(original, [action(original, name=name, kind="approve_as_extracted")])
    field = next(f for f in result.record.fields if f.field_name == name)
    assert field.review_status == "approved_provider"
    assert field.reviewed_values == tuple(v.raw for v in original_field(original, "form-1", name).values)
    assert field.value_source == "human_approved_provider"
    assert result.record.review_status == "unresolved"


@pytest.mark.parametrize("name", FIELDS)
def test_correct_each_field_uses_exact_operator_list(name):
    values = (" 000-7/9) עברית ", "000-7/9) עברית", " 000-7/9) עברית ") if name in MULTI_FIELDS else (" 000-7/9) עברית ",)
    result = reviewed(actions=[action(name=name, values=values)])
    field = next(f for f in result.record.fields if f.field_name == name)
    assert field.reviewed_values == values and field.value_status == "present"
    assert field.review_status == "approved_corrected" and field.value_source == "human_corrected"


@pytest.mark.parametrize("kind,state", [("acknowledge_blank", "acknowledged_blank"),
    ("reject", "rejected_unresolved"), ("leave_unresolved", "unresolved")])
def test_blank_rejected_unresolved_are_distinct(kind, state):
    result = reviewed(actions=[action(kind=kind)])
    field = result.record.fields[0]
    assert field.review_status == state and field.reviewed_values == ()
    assert field.original.value_status == "present" and field.original.values
    assert result.audit[0].action.original.values == field.original.values


def test_no_actions_does_not_approve_any_provider_field():
    result = reviewed()
    assert all(f.review_status == "unreviewed" and f.reviewed_values == () for f in result.record.fields)
    assert result.record.unresolved_fields == FIELDS and result.record.review_status == "unresolved"
    assert result.record.revision == 0 and not result.audit


def test_complete_only_all_seven_terminal_and_not_rejection():
    result = complete()
    assert result.record.review_status == "complete" and not result.record.unresolved_fields
    again = reviewed(actions=[action(kind="reject", revision=7, decision_id="reject-after-approval")],
                     previous=result, expected_revision=7)
    assert again.record.review_status == "unresolved" and len(again.audit) == 8
    final = reviewed(actions=[action(kind="acknowledge_blank", revision=8, decision_id="ack-after-rejection")],
                     previous=again, expected_revision=8)
    assert final.record.review_status == "complete" and final.record.fields[0].value_source == "human_approved_blank"


def test_rejection_can_be_resolved_by_later_explicit_correction_with_history():
    first = reviewed(actions=[action(kind="reject")])
    second = reviewed(actions=[action(revision=1, decision_id="correction")], previous=first, expected_revision=1)
    assert second.record.fields[0].review_status == "approved_corrected"
    assert second.audit[0].new_review_state == "rejected_unresolved"
    assert first.record.revision == 1


def test_tentative_unresolved_text_is_audit_only():
    result = reviewed(actions=[action(kind="leave_unresolved", values=("tentative correction",))])
    assert result.record.fields[0].reviewed_values == ()
    assert result.audit[0].action.reviewed_values == ("tentative correction",)


def test_frozen_detached_snapshot_and_caller_lists_are_not_mutated():
    value = canonical()
    before = value.model_dump(mode="json")
    values = ["00-7", "00-7", "09/"]
    item = action(value, name="central_numbers", values=values)
    actions = [item]
    result = reviewed(value, actions)
    assert value.model_dump(mode="json") == before and actions == [item] and values == ["00-7", "00-7", "09/"]
    values.append("later")
    value.logical_forms[0].fields.central_numbers.values[0].raw = "changed later"
    assert result.record.fields[3].reviewed_values == ("00-7", "00-7", "09/")
    assert json.loads(result.canonical_snapshot_json) == before
    reconstructed = ExtractionResult.model_validate_json(result.canonical_snapshot_json)
    reconstructed.logical_forms.clear()
    with pytest.raises(FrozenInstanceError):
        result.record.revision = 5
    with pytest.raises(FrozenInstanceError):
        result.record.fields[0].original.values[0].raw = "different"


@pytest.mark.parametrize("changes,code", [
    ({"form_id": "foreign-form"}, "ACTION_SCOPE_MISMATCH"),
    ({"source_sha256": "b"*64}, "ACTION_SCOPE_MISMATCH"),
    ({"canonical_snapshot_sha256": "c"*64}, "ACTION_SCOPE_MISMATCH"),
    ({"extraction_id": "foreign-extraction"}, "ACTION_SCOPE_MISMATCH"),
    ({"original": OriginalField("missing", "needs_review", ())}, "ORIGINAL_FIELD_MISMATCH"),
    ({"field_name": "unknown"}, "UNKNOWN_BUSINESS_FIELD"),
    ({"expected_revision": True}, "INVALID_REVIEW_REVISION"),
    ({"declared_at": datetime(2026, 1, 1)}, "AWARE_DECLARED_TIMESTAMP_REQUIRED"),
])
def test_action_scope_and_input_errors_are_safe(changes, code):
    with pytest.raises(BusinessReviewError, match=f"^{code}$"):
        reviewed(actions=[replace(action(), **changes)])


@pytest.mark.parametrize("kind,values,code", [
    ("approve_as_extracted", ("different",), "WHOLE_PROVIDER_LIST_REQUIRED"),
    ("correct_and_approve", (), "USE_ACKNOWLEDGE_BLANK"),
    ("correct_and_approve", (" ",), "APPROVED_VALUES_MUST_BE_NONBLANK"),
    ("correct_and_approve", ("a", "b"), "SCALAR_REQUIRES_ONE_VALUE"),
    ("acknowledge_blank", ("a",), "BLANK_REQUIRES_EMPTY_LIST"),
    ("reject", ("a",), "REJECTION_REQUIRES_EMPTY_LIST"),
])
def test_invalid_action_semantics(kind, values, code):
    with pytest.raises(BusinessReviewError, match=f"^{code}$"):
        reviewed(actions=[action(kind=kind, values=values)])


@pytest.mark.parametrize("status", [None, "missing", "unreadable", "ambiguous", "not_processed"])
def test_nonpresent_or_omitted_remain_distinct_and_can_be_corrected(status):
    value = canonical()
    value.logical_forms[0].fields.first_name = None if status is None else FieldResult(value_status=status, values=[])
    result = reviewed(value)
    assert result.record.fields[0].original.value_status == (status or "omitted")
    assert reviewed(value, [action(value)]).record.fields[0].review_status == "approved_corrected"
    with pytest.raises(BusinessReviewError, match="WHOLE_PROVIDER_LIST_REQUIRED"):
        reviewed(value, [action(value, kind="approve_as_extracted", values=())])


def test_multi_approval_rejects_subsets_order_changes_and_preserves_duplicates():
    full = tuple(v.raw for v in original_field(canonical(), "form-1", "central_numbers").values)
    for values in (full[:1], full[::-1][1:], tuple(reversed(full[:2]))):
        with pytest.raises(BusinessReviewError, match="WHOLE_PROVIDER_LIST_REQUIRED"):
            reviewed(actions=[action(name="central_numbers", kind="approve_as_extracted", values=values)])
    assert reviewed(actions=[action(name="central_numbers", kind="approve_as_extracted")]).record.fields[3].reviewed_values == full


def test_replay_before_revision_is_idempotent_and_conflicting_replay_rejected():
    item = action()
    first = reviewed(actions=[item])
    assert reviewed(actions=[item], previous=first, expected_revision=0) is first
    with pytest.raises(BusinessReviewError, match="CONFLICTING_DECISION_REPLAY"):
        reviewed(actions=[replace(item, reason="changed")], previous=first, expected_revision=1)
    with pytest.raises(BusinessReviewError, match="STALE_REVIEW_REVISION"):
        reviewed(actions=[action(decision_id="new", revision=0)], previous=first, expected_revision=0)


def test_batch_duplicate_ids_rejected_and_whole_batch_atomic():
    first = reviewed()
    original = asdict(first)
    for batch in ([action(), action()], [action(), action(decision_id="different", revision=999)]):
        with pytest.raises(BusinessReviewError):
            reviewed(actions=batch, previous=first)
        assert asdict(first) == original


def test_exact_inputs_deterministic_and_review_hash_matches_snapshot():
    assert complete() == complete()
    value = canonical()
    assert snapshot_canonical(value)[0] == snapshot_canonical(value.model_dump_json())[0]
    assert reviewed(value).record.binding.canonical_snapshot_sha256 == snapshot_sha256(value)


def test_snapshot_source_and_previous_mismatch_unknown_form_and_duplicates():
    value = canonical()
    with pytest.raises(BusinessReviewError, match="SOURCE_SNAPSHOT_MISMATCH"):
        reviewed(value, source=binding(value, canonical_snapshot_sha256="b"*64))
    with pytest.raises(BusinessReviewError, match="UNKNOWN_LOGICAL_FORM"):
        apply_business_review(value, binding(value), "foreign")
    with pytest.raises(BusinessReviewError, match="PREVIOUS_REVIEW_SCOPE_MISMATCH"):
        reviewed(value, source=binding(value, source_sha256="b"*64), previous=reviewed(value))
    value.logical_forms.append(value.logical_forms[0].model_copy(deep=True))
    with pytest.raises(BusinessReviewError, match="DUPLICATE_FORM_ID"):
        snapshot_canonical(value)


def test_multi_form_isolation_and_form_local_decision_ids():
    value = canonical(two_forms=True)
    source = binding(value)
    first = reviewed(value, [action(value)], source)
    second = apply_business_review(value, source, "form-2", [action(value, form="form-2")])
    assert first.record.form_id != second.record.form_id
    assert second.audit[0].action.decision_id == first.audit[0].action.decision_id
    with pytest.raises(BusinessReviewError, match="ACTION_SCOPE_MISMATCH"):
        reviewed(value, [action(value, form="form-2")], source)


def test_modified_projection_or_history_fails_closed():
    result = complete()
    with pytest.raises(BusinessReviewError, match="INVALID_BUSINESS_REVIEW_RESULT"):
        validate_review_result(replace(result, record=replace(result.record, revision=999)))
    with pytest.raises(BusinessReviewError, match="INVALID_BUSINESS_REVIEW_RESULT"):
        validate_review_result(replace(result, audit=result.audit[:-1]))


@pytest.mark.parametrize("changes", [{"data_origin": "live"}, {"snapshot_kind": "live"},
    {"data_origin": "unknown"}, {"source_sha256": "not-a-hash"}, {"relationship_status": "paired"}])
def test_explicit_origin_hash_and_no_inferred_pairings(changes):
    with pytest.raises(BusinessReviewError):
        binding(**changes)
