"""Offline domain, HTTP, legacy-storage and XLSX regression for whole-form approval."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest
from pydantic import ValidationError

from backend.api.demo_schemas import DemoFinalizeCommand
from backend.persistence import business_codec as codec
from backend.persistence.business_repository import BusinessRepository
from backend.services.business_review import (
    BusinessReviewAuditEntry, BusinessReviewError, BusinessReviewResult, FormApprovalAction,
    FormApprovalAuditEntry, apply_business_review, validate_review_result,
)
from backend.services.form_review_policy import review_reasons
from backend.services.demo_workflow import DemoError
from tests.test_business_review import NOW, action, binding, canonical, reviewed
from tests.test_demo_api import Adapter, BASE, command, post, setup, upload
from tests.test_excel_export import read_workbook


def clean_result(*, two_forms=False):
    result = canonical(two_forms=two_forms)
    for form in result.logical_forms:
        if form.form_id != "form-1":
            continue
        for field in form.fields.model_dump():
            value = getattr(form.fields, field)
            if value:
                for v in value.values:
                    v.normalized = None
    return result


def approval(value, *, revision=0, decision="form-final-1", **changes):
    source = binding(value)
    values = dict(source_sha256=source.source_sha256, extraction_id=source.extraction_id,
        canonical_snapshot_sha256=source.canonical_snapshot_sha256, form_id="form-1", decision_id=decision,
        expected_revision=revision, operator_declaration="Synthetic human form reviewer",
        declared_at=NOW, attest_all_values=True)
    values.update(changes)
    return FormApprovalAction(**values)


def finalize(value, previous=None, **changes):
    rev = previous.record.revision if previous else 0
    return reviewed(value, [approval(value, revision=rev, **changes)], previous=previous, expected_revision=rev)


def finalize_command(run, **changes):
    values = dict(review_token=run["review_token"], expected_revision=run["selected_form"]["revision"],
        decision_id="form-final-command", operator_declaration="Synthetic form reviewer",
        declared_at="2026-10-20T00:00:00Z", attest_all_values=True)
    values.update(changes)
    return values


def http_finalize(client, run, body=None):
    from urllib.parse import quote
    return client.post(f"{BASE}/{run['run_id']}/forms/{quote(run['selected_form']['form_id'], safe='')}/finalize",
                       json=finalize_command(run) if body is None else body)


def test_one_form_event_not_seven_field_approvals_and_exact_snapshot():
    value = clean_result()
    original = value.model_dump_json()
    result = finalize(value)
    assert result.finalized and result.record.revision == 1 and result.record.review_status == "complete"
    assert len(result.audit) == 1 and isinstance(result.audit[0], FormApprovalAuditEntry)
    assert all(f.review_status == "accepted_by_form" and f.value_source == "human_form_attestation"
               for f in result.record.fields)
    assert result.record.fields[3].reviewed_values == ("0007", "008/9)", "0007")
    assert value.model_dump_json() == original
    assert result.canonical_snapshot_json == reviewed(value).canonical_snapshot_json
    validate_review_result(result)
    assert codec.decode(codec.encode(result), BusinessReviewResult) == result
    assert codec.decode(codec.encode(result.audit[0]), FormApprovalAuditEntry) == result.audit[0]
    assert codec.FORM_CODEC_VERSION in codec.encode(result)


@pytest.mark.parametrize("change,reason", [
    ({"review_status": "needs_review"}, "PROVIDER_REVIEW_REQUIRED"),
    ({"review_status": "needs_clarification"}, "PROVIDER_REVIEW_REQUIRED"),
    ({"notes": "Synthetic unresolved ambiguity"}, "PROVIDER_NOTES_REQUIRE_REVIEW"),
    ({"signals": {"confidence": .99}}, "PROVIDER_SIGNALS_REQUIRE_REVIEW"),
    ({"signals": {"conflicting_candidates": ["001", "002"]}}, "PROVIDER_SIGNALS_REQUIRE_REVIEW"),
    ({"values": [{"raw": "a"}, {"raw": "b"}]}, "CONTRADICTORY_SCALAR_VALUES"),
    ({"value_status": "missing", "values": []}, "MISSING_VALUE"),
])
def test_populated_uncertainty_conflicts_and_missing_are_hard_exceptions(change, reason):
    from backend.domain import FieldResult
    value = clean_result()
    data = value.logical_forms[0].fields.first_name.model_dump()
    data.update(change)
    value.logical_forms[0].fields.first_name = FieldResult.model_validate(data)
    initial = reviewed(value)
    assert reason in review_reasons(initial, initial.record.fields[0])
    with pytest.raises(BusinessReviewError, match="FORM_HAS_UNRESOLVED_FIELDS"):
        finalize(value)
    assert not initial.audit and initial.record.revision == 0


@pytest.mark.parametrize("raw,normalized", [("31.02.2026", None), ("18.08.2026", "2026-08-19"),
                                          ("18.08.2026", "bad")])
def test_invalid_date_or_normalization_cannot_finalize(raw, normalized):
    value = clean_result()
    v = value.logical_forms[0].fields.sample_receipt_date.values[0]
    v.raw, v.normalized = raw, normalized
    with pytest.raises(BusinessReviewError, match="FORM_HAS_UNRESOLVED_FIELDS"):
        finalize(value)


def test_normalized_identifier_loses_leading_zero_requires_review():
    value = clean_result()
    value.logical_forms[0].fields.israeli_id.values[0].normalized = "123"
    initial = reviewed(value)
    assert "UNVERIFIED_NORMALIZATION" in review_reasons(initial, initial.record.fields[2])


def test_explicit_correction_resolves_warning_and_stays_individual_human_correction():
    value = clean_result()
    value.logical_forms[0].fields.first_name.notes = "Synthetic uncertainty"
    corrected = reviewed(value, [action(value, values=("שרה",))])
    final = finalize(value, corrected)
    assert len(final.audit) == 2 and final.record.revision == 2
    assert final.record.fields[0].review_status == "approved_corrected"
    assert final.record.fields[0].reviewed_values == ("שרה",)
    assert final.record.fields[0].value_source == "human_corrected"


def test_blank_requires_acknowledgment_before_whole_form_approval():
    value = clean_result()
    value.logical_forms[0].fields.israeli_id.value_status = "missing"
    value.logical_forms[0].fields.israeli_id.values = []
    with pytest.raises(BusinessReviewError):
        finalize(value)
    blank = reviewed(value, [action(value, name="israeli_id", kind="acknowledge_blank")])
    final = finalize(value, blank)
    assert final.record.fields[2].review_status == "acknowledged_blank"
    assert final.record.fields[2].reviewed_values == ()


def test_replay_stale_revision_repeat_and_correction_require_new_attestation():
    value = clean_result()
    first = finalize(value)
    assert reviewed(value, [approval(value)], previous=first, expected_revision=0) is first
    with pytest.raises(BusinessReviewError, match="STALE_REVIEW_REVISION"):
        reviewed(value, [approval(value, decision="other")], previous=first, expected_revision=0)
    with pytest.raises(BusinessReviewError, match="FORM_ALREADY_FINALIZED"):
        finalize(value, first, decision="other")
    change = action(value, name="sample_receipt_date", values=("18.08.2026",), revision=1, decision_id="date-correction")
    corrected = reviewed(value, [change], previous=first, expected_revision=1)
    assert not corrected.finalized and first.finalized
    assert corrected.audit[:1] == first.audit
    second = finalize(value, corrected, decision="new-form-final")
    assert second.finalized and second.record.revision == 3
    assert codec.decode(codec.encode(second), BusinessReviewResult) == second


def test_tampering_cannot_fabricate_form_approved_values():
    value = clean_result()
    final = finalize(value)
    bad = replace(final, record=replace(final.record, fields=(replace(final.record.fields[0], reviewed_values=("tamper",)),)
                                                       + final.record.fields[1:]))
    with pytest.raises(BusinessReviewError):
        validate_review_result(bad)


@pytest.mark.parametrize("attestation", [False, 1, "true", None])
def test_literal_explicit_attestation_required(attestation):
    with pytest.raises(ValidationError):
        DemoFinalizeCommand.model_validate(dict(review_token="token", expected_revision=0, decision_id="d",
            operator_declaration="operator", declared_at="2026-01-01T00:00:00Z", attest_all_values=attestation))


def test_historical_codec_bytes_and_snapshots_unchanged():
    fixture = json.loads((Path(__file__).parent / "fixtures/business_review_v1.json").read_text(encoding="utf-8"))
    legacy = codec.decode(fixture["projection"], BusinessReviewResult)
    event = codec.decode(fixture["event"], BusinessReviewAuditEntry)
    assert codec.encode(legacy) == fixture["projection"]
    assert codec.encode(event) == fixture["event"]
    assert legacy.record.review_status == "complete" and not legacy.finalized


def test_http_one_click_reopen_export_and_one_event_per_form_without_provider_rerun():
    client, service, adapter = setup(Adapter(clean_result(two_forms=True)))
    run = upload(client).json()
    saved_snapshot = service._runs[run["run_id"]].canonical_snapshot_json
    body = finalize_command(run)
    response = http_finalize(client, run, body)
    assert response.status_code == 200
    final = response.json()
    assert final["selected_form"]["finalized"] is True
    assert final["selected_form"]["automatic_acceptance"] is False
    assert all(not f["requires_review"] for f in final["selected_form"]["fields"])
    assert http_finalize(client, run, body).json() == final
    assert http_finalize(client, final, finalize_command(final, decision_id="different")).status_code == 409
    reopened = client.get(f"{BASE}/{run['run_id']}").json()
    assert reopened == final and len(adapter.calls) == 1
    assert service._runs[run["run_id"]].canonical_snapshot_json == saved_snapshot
    history = client.get(f"{BASE}/{run['run_id']}/audit").json()
    assert len(history["forms"][0]["audit"]) == 1
    assert not history["forms"][1]["audit"]
    book = read_workbook(client.get(f"{BASE}/{run['run_id']}/export.xlsx").content)
    rows = [r for r in book["Forms"][1] if r["form_id"] == "form-1"]
    assert [r["commitment_numbers"] for r in rows] == ["00001", "00002"]
    assert all(r["form_finalized"] == "true" and r["first_name_value_source"] == "human_form_attestation" for r in rows)
    assert len(book["ReviewAudit"][1]) == 1
    assert book["ReviewAudit"][1][0]["field_name"] == "__form__"


def test_http_field_exception_blocks_then_routine_correction_needs_no_free_text():
    value = clean_result()
    value.logical_forms[0].fields.sample_receipt_date.notes = "Uncertain synthetic date"
    client, _, _ = setup(Adapter(value))
    run = upload(client).json()
    assert run["selected_form"]["fields"][4]["requires_review"] is True
    assert http_finalize(client, run).json()["error"]["code"] == "FORM_HAS_UNRESOLVED_FIELDS"
    body = command(run, name="sample_receipt_date", values=["18.08.2026"])
    del body["actions"][0]["reason"]
    saved = post(client, run, body)
    assert saved.status_code == 200
    final = http_finalize(client, saved.json())
    assert final.status_code == 200 and final.json()["selected_form"]["revision"] == 2
    audit = client.get(f"{BASE}/{run['run_id']}/audit").json()["forms"][0]["audit"]
    assert audit[0]["action"]["reason"] == "SYSTEM:ROUTINE_CORRECTION_NO_FREE_TEXT"


def test_saved_hyphen_date_correction_has_current_validation_blocker_not_historical_uncertainty():
    value = clean_result()
    original = value.logical_forms[0].fields.sample_receipt_date
    original.notes = "Synthetic date uncertainty retained for audit"
    original.review_status = "needs_review"
    client, workflow, provider = setup(Adapter(value))
    run = upload(client).json()
    snapshot = workflow._get(run["run_id"]).canonical_snapshot_json
    saved = post(client, run, command(run, name="sample_receipt_date", values=["18-08-2026"]))
    assert saved.status_code == 200
    reopened = client.get(f"{BASE}/{run['run_id']}").json()
    field = reopened["selected_form"]["fields"][4]
    assert field["reviewed_values"] == ["18-08-2026"]
    assert field["review_status"] == "approved_corrected"
    assert field["provider_review_status"] == "needs_review"
    assert field["review_reasons"] == ["INVALID_DATE"]
    assert field["requires_review"] is True
    assert http_finalize(client, reopened).json()["error"]["code"] == "FORM_HAS_UNRESOLVED_FIELDS"
    correction = command(reopened, name="sample_receipt_date", values=["18.08.2026"])
    correction["actions"][0]["decision_id"] = "valid-date-correction"
    fixed = post(client, reopened, correction)
    assert fixed.status_code == 200
    latest = client.get(f"{BASE}/{run['run_id']}").json()
    assert latest["selected_form"]["fields"][4]["review_reasons"] == []
    assert not any(f["requires_review"] for f in latest["selected_form"]["fields"])
    final = http_finalize(client, latest)
    assert final.status_code == 200
    assert client.get(f"{BASE}/{run['run_id']}").json() == final.json()
    history = client.get(f"{BASE}/{run['run_id']}/audit").json()["forms"][0]["audit"]
    assert len(history) == 3 and sum(e["action"]["action"] == "finalize_form" for e in history) == 1
    assert workflow._get(run["run_id"]).canonical_snapshot_json == snapshot
    assert len(provider.calls) == 1


def test_concurrent_http_finalization_and_wrong_token():
    client, _, _ = setup(Adapter(clean_result()))
    run = upload(client).json()
    assert http_finalize(client, run, finalize_command(run, review_token="wrong")).status_code == 404
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(http_finalize, client, run, finalize_command(run, decision_id=f"d-{i}")) for i in range(2)]
    assert sorted(f.result().status_code for f in futures) == [200, 409]


def test_finalization_retains_local_security_gate_and_never_invokes_provider():
    client, _, adapter = setup(Adapter(clean_result()))
    run = upload(client).json()
    response = client.post(f"{BASE}/{run['run_id']}/forms/form-1/finalize", json=finalize_command(run),
                           headers={"Origin": "https://untrusted.invalid"})
    assert response.status_code == 403 and len(adapter.calls) == 1


def test_minimal_migration_is_offline_and_nondestructive(monkeypatch):
    path = Path(__file__).parents[1] / "migrations/versions/0003_form_approval.py"
    spec = importlib.util.spec_from_file_location("form_approval_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    statements = []
    monkeypatch.setattr(module.op, "execute", lambda statement: statements.append(str(statement)))
    module.upgrade()
    assert statements == list(module.STATEMENTS)
    assert len(statements) == 2 and "'__form__'" in statements[1]
    assert all("DELETE" not in s and "UPDATE" not in s and "DROP TABLE" not in s for s in statements)
    with pytest.raises(RuntimeError):
        module.downgrade()


@pytest.mark.parametrize("definition", [None, "CHECK (field_name <> '__form__')", "CHECK (field_name = ANY (ARRAY['first_name'::text]))"])
def test_repository_rejects_absent_migration_before_transition_without_connecting(definition):
    engine = MagicMock()
    engine.dialect.name = "postgresql"
    conn = engine.begin.return_value.__enter__.return_value
    conn.execute.return_value.scalar_one_or_none.return_value = definition
    repository = BusinessRepository(engine)
    repository._load = Mock(return_value=object())
    transition = Mock()
    with pytest.raises(DemoError, match="FORM_APPROVAL_MIGRATION_REQUIRED"):
        repository.finalize_atomic("synthetic-document", transition)
    transition.assert_not_called()
    engine.begin.assert_called_once()
