"""Synthetic offline tests for composition, immutable sessions and review scope."""

import json
from dataclasses import FrozenInstanceError, asdict, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256

import pytest

from backend.domain import ExtractionResult, FieldResult, FieldValue
from backend.services.extraction.ocr_review import candidate_reference
from backend.services.extraction.review_workflow import ReviewOutcome, ReviewWorkflowService
from tests.test_ocr_candidates import IMAGE_HASH, candidate, canonical
from tests.test_ocr_review import decision

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
LATER = NOW + timedelta(seconds=1)
SERVICE = ReviewWorkflowService()


def result():
    return canonical(("001",), ("009",))


def prepare(extracted=None, candidates=(), **changes):
    args = dict(document_id="doc-1", form_id="form-1", session_id="session-1",
                form_page_images={"form-1": {1: IMAGE_HASH}}, source_status="succeeded",
                prepared_at=NOW)
    args.update(changes)
    return SERVICE.prepare_review(result() if extracted is None else extracted, candidates, **args)


def required(**changes):
    return prepare(candidates=[candidate("002")], **changes)


def apply(session, item_index=0, *, revision=None, received_at=LATER, **changes):
    action = decision(session.review_items[item_index], **changes)
    return SERVICE.apply_decision(session, action,
                                  expected_revision=session.revision if revision is None else revision,
                                  received_at=received_at)


def test_no_review_required_and_explicit_outside_scope_issues():
    session = prepare()
    assert session.overall_review_status == "no_review_required"
    assert session.review_items == session.pending_field_issues == ()
    assert session.revision == 0
    assert session.prepared_at == session.updated_at == NOW
    assert {issue.field_name for issue in session.outside_scope_review_issues} == {
        "first_name", "last_name", "israeli_id", "sample_receipt_date", "sender",
    }
    assert ReviewOutcome(session, False).outside_scope_review_issues == session.outside_scope_review_issues


def test_disagreement_creates_required_item_not_approval():
    session = required()
    assert session.overall_review_status == "review_required"
    assert session.review_items[0].approved_value is None
    assert session.supporting_assessment.fields[0].state == "disagreement"
    assert ReviewOutcome(session, False).unverified_references == (session.review_items[0].candidate_id,)


@pytest.mark.parametrize("review_status", ["unreviewed", "approved_as_extracted", "approved_corrected"])
def test_corroboration_alone_creates_no_task_and_no_approval(review_status):
    extracted = result()
    extracted.logical_forms[0].fields.central_numbers.review_status = review_status
    session = prepare(extracted, [candidate()])
    assert session.overall_review_status == "no_review_required"
    assert not session.review_items
    assert not ReviewOutcome(session, False).approved_values
    assert len(session.supporting_assessment.fields[0].ocr_candidates) == 1


def test_partial_support_alone_does_not_require_candidate_review():
    session = prepare(canonical(("001", "002"), ("009",)), [candidate()])
    assert session.supporting_assessment.fields[0].state == "partial_support"
    assert not session.review_items


@pytest.mark.parametrize("review_status", ["needs_review", "needs_clarification"])
def test_existing_canonical_review_obligation_survives_corroboration(review_status):
    extracted = result()
    extracted.logical_forms[0].fields.central_numbers.review_status = review_status
    before = extracted.model_dump(mode="json")
    session = prepare(extracted, [candidate()])
    assert session.overall_review_status == "review_required"
    assert len(session.review_items) == 1
    completed = apply(session).session
    assert completed.overall_review_status == "completed"
    assert json.loads(completed.canonical_snapshot_json) == before
    assert extracted.model_dump(mode="json") == before


def test_ocr_only_candidate_creates_item_without_filling_provider_field():
    extracted = canonical((), ("009",))
    session = prepare(extracted, [candidate("002")])
    assert session.supporting_assessment.fields[0].state == "ocr_only"
    assert len(session.review_items) == 1
    assert not session.pending_field_issues
    assert session.review_items[0].provider_values == ()
    assert not extracted.logical_forms[0].fields.central_numbers.values


@pytest.mark.parametrize("state", [None, "missing", "unreadable", "ambiguous", "not_processed"])
def test_required_field_without_candidate_stays_pending_even_without_review_metadata(state):
    extracted = result()
    extracted.logical_forms[0].fields.central_numbers = (
        None if state is None else FieldResult(value_status=state, values=[])
    )
    session = prepare(extracted)
    assert session.overall_review_status == "review_required"
    assert not session.review_items
    assert session.pending_field_issues[0].field_name == "central_numbers"
    assert ReviewOutcome(session, False).pending_field_issues == session.pending_field_issues


def test_present_field_explicitly_needing_review_without_ocr_is_pending():
    extracted = result()
    extracted.logical_forms[0].fields.central_numbers.review_status = "needs_review"
    session = prepare(extracted)
    assert session.pending_field_issues[0].reasons == ("EXISTING_REVIEW_REQUIRED",)


def test_partial_then_completed_preserves_other_items_and_history_order():
    session = prepare(candidates=[candidate("002"), candidate("003")])
    first = apply(session)
    assert first.session.overall_review_status == "partially_reviewed"
    assert first.session.revision == 1
    assert first.session.review_items[1] is session.review_items[1]
    second = apply(first.session, 1, decision_id="decision-2", received_at=LATER + timedelta(seconds=1))
    assert second.session.overall_review_status == "completed"
    assert second.session.revision == 2
    assert len(second.session.decision_history) == 2
    assert [event.decision.decision_id for event in second.session.decision_history] == ["decision-1", "decision-2"]
    assert second.session.updated_at == LATER + timedelta(seconds=1)
    assert session.revision == 0 and not session.decision_history


def test_unresolved_never_completes_even_when_all_items_have_actions():
    session = required()
    outcome = apply(session, decision="unresolved", field_assignment_status="unresolved", reviewed_value=None)
    assert outcome.session.overall_review_status == "partially_reviewed"
    assert outcome.unresolved_references == (session.review_items[0].candidate_id,)
    assert outcome.approved_values == outcome.rejected_references == ()


def test_rejection_closes_only_candidate_not_provider_or_business_field():
    extracted = canonical((), ("009",), review="needs_review")
    # Commitment has no candidate and its explicit review flag remains pending.
    extracted.logical_forms[0].fields.commitment_numbers.review_status = "unreviewed"
    session = prepare(extracted, [candidate("002")])
    outcome = apply(session, decision="rejected", value_verification_status="rejected")
    assert outcome.session.overall_review_status == "completed"
    assert outcome.rejected_references == (session.review_items[0].candidate_id,)
    assert not outcome.approved_values
    field = json.loads(outcome.session.canonical_snapshot_json)["logical_forms"][0]["fields"]["central_numbers"]
    assert field["value_status"] == "missing" and field["review_status"] == "needs_review"


def test_pending_issue_blocks_completion_after_every_candidate_is_terminal():
    session = prepare(canonical(("001",), ()), [candidate("002")])
    outcome = apply(session)
    assert len(outcome.session.review_items) == 1
    assert outcome.session.review_items[0].review_state == "approved"
    assert outcome.session.overall_review_status == "partially_reviewed"
    assert outcome.pending_field_issues[0].field_name == "commitment_numbers"


@pytest.mark.parametrize("status", ["succeeded", "disabled", "unavailable", "failed"])
def test_source_outcomes_with_empty_evidence_preserve_provider_success(status):
    extracted = result()
    before = extracted.model_dump(mode="json")
    session = prepare(extracted, source_status=status)
    assert session.supporting_assessment.source_status == status
    assert session.overall_review_status == "no_review_required"
    assert json.loads(session.canonical_snapshot_json) == before
    assert all(field.state == "no_support" for field in session.supporting_assessment.fields)


@pytest.mark.parametrize("status", ["disabled", "unavailable", "failed"])
def test_failed_or_unavailable_ocr_cannot_supply_candidates(status):
    with pytest.raises(ValueError, match="CANDIDATES_REQUIRE_SUCCESSFUL_OCR_SOURCE"):
        prepare(candidates=[candidate()], source_status=status)


@pytest.mark.parametrize("failed", [None, RuntimeError("synthetic failure"), {"error": "failed"}])
def test_provider_failure_is_not_canonical_preparation_input(failed):
    with pytest.raises(ValueError, match="SUCCESSFUL_CANONICAL_RESULT_REQUIRED"):
        SERVICE.prepare_review(failed, (), document_id="doc-1", form_id="form-1",
                               form_page_images={"form-1": {1: IMAGE_HASH}}, source_status="failed",
                               session_id="session-1", prepared_at=NOW)


def test_empty_and_duplicate_form_results_and_missing_selected_form_fail():
    extracted = result()
    extracted.logical_forms = []
    with pytest.raises(ValueError, match="SUCCESSFUL_CANONICAL_RESULT_REQUIRED"):
        prepare(extracted)
    extracted = result()
    extracted.logical_forms.append(extracted.logical_forms[0].model_copy(deep=True))
    with pytest.raises(ValueError, match="DUPLICATE_FORM_ID"):
        prepare(extracted)
    with pytest.raises(ValueError, match="SELECTED_FORM_NOT_FOUND"):
        prepare(form_id="other-form")


def multi_form():
    extracted = result()
    other = extracted.logical_forms[0].model_copy(deep=True)
    other.form_id = "form-2"
    extracted.logical_forms.append(other)
    return extracted, {"form-1": {1: IMAGE_HASH}, "form-2": {1: IMAGE_HASH}}


def test_multiple_forms_on_same_page_are_isolated_with_complete_snapshot_and_assessment():
    extracted, pages = multi_form()
    batch = [candidate("002", form_id="form-2"), candidate()]
    first = prepare(extracted, batch, form_page_images=pages)
    second = prepare(extracted, batch, form_page_images=pages, form_id="form-2", session_id="session-2")
    assert first.overall_review_status == "no_review_required"
    assert second.overall_review_status == "review_required"
    assert first.review_items == ()
    assert len(first.supporting_assessment.fields) == 4
    assert len(json.loads(first.canonical_snapshot_json)["logical_forms"]) == 2
    assert len(first.form_page_images) == 2
    action = decision(second.review_items[0])
    with pytest.raises(ValueError, match="CANDIDATE_NOT_ACTIONABLE"):
        SERVICE.apply_decision(first, action, expected_revision=0, received_at=LATER)
    reviewed = SERVICE.apply_decision(second, action, expected_revision=0, received_at=LATER)
    assert reviewed.session.overall_review_status == "completed"
    assert reviewed.session.canonical_snapshot_json == first.canonical_snapshot_json
    assert second.revision == first.revision == 0


def test_complete_form_provenance_required_even_for_single_selected_form():
    extracted, _ = multi_form()
    with pytest.raises(ValueError, match="UNAMBIGUOUS_FORM_PROVENANCE_REQUIRED"):
        prepare(extracted)


def test_duplicate_occurrences_deduplicate_tasks_not_assessment_evidence_or_fingerprint():
    value = candidate("002")
    one = prepare(candidates=[value])
    two = prepare(candidates=[value, value])
    assert one.review_items == two.review_items
    assert len(two.supporting_assessment.fields[0].ocr_candidates) == 2
    assert one.input_fingerprint != two.input_fingerprint
    assert apply(two).session.overall_review_status == "completed"


def test_distinct_provenance_remains_separate_and_input_order_is_irrelevant():
    batch = [candidate("002"), candidate("002", source_run_id="run-2")]
    first = prepare(candidates=batch)
    second = prepare(candidates=reversed(batch))
    assert len(first.review_items) == 2
    assert first == second
    assert first.input_fingerprint == second.input_fingerprint
    assert first.review_items[0].candidate_id != first.review_items[1].candidate_id


def test_exact_replay_precedes_stale_revision_and_changes_no_timestamp_or_history():
    session = required()
    action = decision(session.review_items[0])
    accepted = SERVICE.apply_decision(session, action, expected_revision=0, received_at=LATER)
    replay = SERVICE.apply_decision(accepted.session, replace(action), expected_revision=0,
                                    received_at=LATER + timedelta(days=1))
    assert replay.replay is True
    assert replay.session is accepted.session
    assert replay.session.updated_at == LATER
    assert len(replay.session.decision_history) == replay.session.revision == 1


def test_id_uniqueness_is_session_wide_and_conflicts_are_atomic():
    session = prepare(candidates=[candidate("002"), candidate("003")])
    outcome = apply(session)
    before = asdict(outcome.session)
    with pytest.raises(ValueError, match="REVIEW_DECISION_ID_CONFLICT"):
        apply(outcome.session, reviewer_note="altered same ID")
    with pytest.raises(ValueError, match="REVIEW_DECISION_ID_CONFLICT"):
        apply(outcome.session, 1)
    with pytest.raises(ValueError, match="REVIEW_DECISION_CONFLICT"):
        apply(outcome.session, decision_id="new-id")
    assert asdict(outcome.session) == before


@pytest.mark.parametrize("revision", [-1, 1, True])
def test_stale_new_revision_fails_atomically(revision):
    session = required()
    before = asdict(session)
    with pytest.raises(ValueError, match="STALE_REVIEW_REVISION"):
        apply(session, revision=revision)
    assert asdict(session) == before


@pytest.mark.parametrize("change", [
    {"document_id": "other-document"}, {"form_id": "other-form"},
    {"page_number": 2}, {"source_image_sha256": "b" * 64},
])
def test_decision_provenance_mismatch_is_atomic(change):
    session = required()
    before = asdict(session)
    with pytest.raises(ValueError, match="REVIEW_PROVENANCE_MISMATCH"):
        apply(session, **change)
    assert asdict(session) == before


@pytest.mark.parametrize("change", [
    {"document_id": "other-document"}, {"form_id": "other-form"},
    {"page_number": 2}, {"source_image_sha256": "b" * 64},
])
def test_candidate_scope_mismatch_cannot_prepare(change):
    with pytest.raises(ValueError, match="CANDIDATE_PROVENANCE_MISMATCH"):
        prepare(candidates=[candidate("002", **change)])


def test_evidence_only_candidate_cannot_receive_decision():
    session = prepare(candidates=[candidate()])
    from backend.services.extraction.ocr_review import OCRReviewItem
    action = decision(OCRReviewItem(candidate()))
    with pytest.raises(ValueError, match="CANDIDATE_NOT_ACTIONABLE"):
        SERVICE.apply_decision(session, action, expected_revision=0, received_at=LATER)


def test_deep_snapshot_isolation_and_exact_original_values_statuses_and_nulls():
    extracted = result()
    field = extracted.logical_forms[0].fields.central_numbers
    field.signals = {"nested": {"tokens": ["001"]}}
    before = extracted.model_dump(mode="json")
    session = prepare(extracted, [candidate("002")])
    assert json.loads(session.canonical_snapshot_json) == before
    reconstructed = ExtractionResult.model_validate_json(session.canonical_snapshot_json)
    reconstructed.logical_forms[0].fields.central_numbers.values[0].raw = "999"
    field.values[0].raw = "123"
    field.signals["nested"]["tokens"].append("999")
    extracted.logical_forms.append(extracted.logical_forms[0].model_copy(deep=True))
    assert json.loads(session.canonical_snapshot_json) == before
    assert session.canonical_snapshot_sha256 == sha256(session.canonical_snapshot_json.encode()).hexdigest()
    assert session.canonical_snapshot_encoding_version == "canonical-json-v1"
    assert session.review_items[0].provider_values[0].raw_value == "001"
    assert apply(session).session.canonical_snapshot_json == session.canonical_snapshot_json


def test_provenance_mapping_and_candidate_instances_are_isolated_immutable():
    pages = {"form-1": {1: IMAGE_HASH}}
    value = candidate("002")
    session = prepare(candidates=[value], form_page_images=pages)
    original = asdict(value)
    pages["form-1"][1] = "b" * 64
    reviewed = apply(session)
    assert session.form_page_images[0].source_image_sha256 == IMAGE_HASH
    assert asdict(value) == original
    for target, name, replacement in (
        (session, "revision", 99), (session.review_items[0], "review_decision", None),
        (value, "raw_value", "999"), (session.form_page_images[0], "page_number", 99),
        (reviewed, "replay", True),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(target, name, replacement)


def test_preparation_is_deterministic_and_fingerprint_binds_provenance_and_canonical():
    first = required()
    assert first == required()
    changed_source = prepare(candidates=[candidate("002", source_image_sha256="b" * 64)],
                             form_page_images={"form-1": {1: "b" * 64}})
    changed_value = prepare(canonical(("004",), ("009",)), [candidate("002")])
    changed_source_status = prepare(source_status="failed")
    assert first.input_fingerprint != changed_source.input_fingerprint
    assert first.input_fingerprint != changed_value.input_fingerprint
    assert prepare().input_fingerprint != changed_source_status.input_fingerprint
    assert first.canonical_snapshot_sha256 != changed_value.canonical_snapshot_sha256


def test_corrected_approved_value_remains_separate_exact_and_auditable():
    session = required()
    original = session.canonical_snapshot_json
    outcome = apply(session, reviewed_value=" 000-12/3) ")
    value = outcome.approved_values[0]
    assert value.reviewed_value == " 000-12/3) "
    assert value.candidate_id == candidate_reference(session.review_items[0].candidate)
    assert value.field_name == "central_numbers"
    assert value.candidate.raw_value == "002"
    assert value.candidate.document_id == session.document_id
    assert outcome.session.review_items[0].human_correction_occurred
    assert outcome.session.canonical_snapshot_json == original


def test_outside_scope_explicit_flag_is_preserved_not_an_ocr_task():
    extracted = result()
    extracted.logical_forms[0].fields.sender = FieldResult(
        value_status="present", values=[FieldValue(raw="Synthetic sender")], review_status="needs_review",
    )
    session = prepare(extracted)
    issue = next(issue for issue in session.outside_scope_review_issues if issue.field_name == "sender")
    assert issue.reasons == ("EXISTING_REVIEW_REQUIRED",)
    assert session.overall_review_status == "no_review_required"
    assert not session.review_items


@pytest.mark.parametrize("change", [
    {"input_fingerprint": "a" * 64}, {"canonical_snapshot_sha256": "a" * 64},
    {"revision": 7}, {"overall_review_status": "completed"},
    {"session_id": "other-session"}, {"workflow_version": "future-version"},
    {"canonical_snapshot_json": "{}"},
])
def test_inconsistent_session_or_changed_snapshot_cannot_reuse_old_decisions(change):
    session = required()
    action = decision(session.review_items[0])
    with pytest.raises(ValueError, match="INVALID_REVIEW_SESSION"):
        SERVICE.apply_decision(replace(session, **change), action, expected_revision=0, received_at=LATER)
    assert session.revision == 0


def test_evidence_change_and_inconsistent_history_are_rejected():
    session = required()
    assessment = session.supporting_assessment
    field = assessment.fields[0]
    changed = replace(session, supporting_assessment=replace(
        assessment, fields=(replace(field, ocr_candidates=(candidate("999"),)),) + assessment.fields[1:],
    ))
    with pytest.raises(ValueError, match="INVALID_REVIEW_SESSION"):
        apply(changed)
    accepted = apply(session).session
    corrupted = replace(accepted, decision_history=accepted.decision_history * 2)
    with pytest.raises(ValueError, match="INVALID_REVIEW_SESSION"):
        apply(corrupted)


def test_unknown_source_and_inconsistent_canonical_fields_fail_explicitly():
    with pytest.raises(ValueError, match="UNKNOWN_OCR_SOURCE_STATUS"):
        prepare(source_status="unknown")
    extracted = result()
    extracted.logical_forms[0].fields.central_numbers.values = []
    with pytest.raises(ValueError, match="SANITIZED_CANONICAL_FIELDS_REQUIRED"):
        prepare(extracted)


@pytest.mark.parametrize("change", [
    {"prepared_at": datetime(2026, 1, 1)}, {"session_id": " "}, {"document_id": ""},
])
def test_explicit_workflow_inputs_are_required(change):
    with pytest.raises(ValueError):
        prepare(**change)


def test_invalid_receipt_time_does_not_change_session():
    session = required()
    with pytest.raises(ValueError, match="TIMEZONE_AWARE_WORKFLOW_TIME_REQUIRED"):
        apply(session, received_at=datetime(2026, 1, 1))
    assert session.revision == 0


def test_old_decision_replay_returns_latest_session_after_other_item_was_reviewed():
    session = prepare(candidates=[candidate("002"), candidate("003")])
    first = apply(session).session
    latest = apply(first, 1, decision_id="decision-2").session
    replay = SERVICE.apply_decision(
        latest, first.decision_history[0].decision, expected_revision=0, received_at=LATER,
    )
    assert replay.replay and replay.session is latest
    assert latest.revision == 2
    with pytest.raises(ValueError, match="REVIEW_DECISION_CONFLICT"):
        apply(latest, decision_id="decision-3")


def test_new_action_with_old_revision_after_partial_review_fails():
    partial = apply(prepare(candidates=[candidate("002"), candidate("003")])).session
    before = asdict(partial)
    with pytest.raises(ValueError, match="STALE_REVIEW_REVISION"):
        apply(partial, 1, decision_id="decision-2", revision=0)
    assert asdict(partial) == before


def test_unresolved_action_cannot_be_silently_replaced_with_later_approval():
    unresolved = apply(required(), decision="unresolved", field_assignment_status="unresolved").session
    with pytest.raises(ValueError, match="REVIEW_DECISION_CONFLICT"):
        apply(unresolved, decision_id="later-approval")
    assert unresolved.overall_review_status == "partially_reviewed"


def test_mapping_and_nested_json_key_order_do_not_change_snapshot_or_fingerprint():
    first = result()
    second = result()
    first.logical_forms[0].fields.central_numbers.signals = {"a": 1, "b": {"c": 2, "d": 3}}
    second.logical_forms[0].fields.central_numbers.signals = {"b": {"d": 3, "c": 2}, "a": 1}
    pages = {"form-1": {1: IMAGE_HASH, 2: "b" * 64}}
    reversed_pages = {"form-1": {2: "b" * 64, 1: IMAGE_HASH}}
    assert prepare(first, form_page_images=pages) == prepare(second, form_page_images=reversed_pages)


def test_existing_assessment_and_review_gate_are_composed(monkeypatch):
    import backend.services.extraction.review_workflow as workflow
    assessment_calls, review_calls = [], []
    assess = workflow.assess_ocr_candidates
    review = workflow.apply_review_decision

    def observed_assess(*args, **kwargs):
        assessment_calls.append(True)
        return assess(*args, **kwargs)

    def observed_review(*args, **kwargs):
        review_calls.append(True)
        return review(*args, **kwargs)

    monkeypatch.setattr(workflow, "assess_ocr_candidates", observed_assess)
    monkeypatch.setattr(workflow, "apply_review_decision", observed_review)
    outcome = apply(required())
    assert assessment_calls and review_calls
    assert outcome.session.overall_review_status == "completed"


def test_invalid_canonical_error_is_safe_and_does_not_include_value():
    extracted = result()
    # Simulate bypassing Pydantic validation; do not include its rich error text.
    extracted.logical_forms[0].fields.central_numbers.values[0].raw = {"private": "SYNTHETIC_PRIVATE"}
    with pytest.raises(ValueError, match="^INVALID_CANONICAL_RESULT$") as error:
        prepare(extracted)
    assert "SYNTHETIC_PRIVATE" not in str(error.value)
