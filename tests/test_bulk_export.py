"""Offline ASGI export/review integration; no database or provider network."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from backend.persistence import business_codec as codec
from backend.services.business_review import BusinessReviewResult
from backend.services.excel_export import export_workbook
from backend.services.extraction.errors import ProviderRateLimit, ProviderTimeout, ProviderUnavailable
from tests.test_business_review import complete
from tests.test_demo_api import Adapter, BASE, PDF, setup, upload
from tests.test_excel_export import read_workbook
from tests.test_form_approval import clean_result, http_finalize

URL = "/api/v1/business-records/export.xlsx"


def selection(run, commitments=None):
    form = run["selected_form"]
    values = next(f for f in form["fields"] if f["field_name"] == "commitment_numbers")
    return {"run_id": run["run_id"], "review_token": run["review_token"], "form_id": form["form_id"],
            "expected_revision": form["revision"], "commitments": commitments if commitments is not None else
            [v["raw"] for v in values["provider_values"]]}


def test_two_documents_mixed_finalized_and_unresolved_exact_rows_audit_and_no_mutations():
    client, workflow, adapter = setup(Adapter(clean_result()))
    first = http_finalize(client, upload(client).json()).json()
    second = upload(client).json()
    before = deepcopy(workflow._runs)
    body = {"forms": [selection(first), selection(second), selection(first)]}
    response = client.post(URL, json=body)
    assert response.status_code == 200
    book = read_workbook(response.content)
    rows = book["Forms"][1]
    assert len(rows) == 4
    assert {r["document_id"] for r in rows} == {first["run_id"], second["run_id"]}
    pending = [r for r in rows if r["document_id"] == second["run_id"]]
    assert all(r["commitment_numbers"] == "" and r["first_name"] == "" for r in pending)
    assert {r["export_selected_commitment"] for r in pending} == {"00001", "00002"}
    assert all(r["form_finalized"] == "false" for r in pending)
    assert len(book["ReviewAudit"][1]) == 1
    assert workflow._runs == before and len(adapter.calls) == 2
    assert client.post(URL, json=body).content == response.content


def test_subset_of_multi_form_document_does_not_export_unselected_forms_or_audit():
    value = clean_result()
    second = deepcopy(value.logical_forms[0]); second.form_id = "form-2"; value.logical_forms.append(second)
    client, _, _ = setup(Adapter(value))
    run = upload(client).json()
    item = selection(run, ["00002"])
    rows = read_workbook(client.post(URL, json={"forms": [item]}).content)["Forms"][1]
    assert len(rows) == 1 and rows[0]["form_id"] == "form-1"
    assert rows[0]["export_selected_commitment"] == "00002"


@pytest.mark.parametrize("change,status", [("token", 404), ("revision", 409), ("form", 422), ("value", 422)])
def test_server_selection_and_revision_validation_fail_closed(change, status):
    client, workflow, _ = setup(Adapter(clean_result())); run = upload(client).json(); item = selection(run)
    if change == "token": item["review_token"] = "wrong"
    if change == "revision": item["expected_revision"] = 99
    if change == "form": item["form_id"] = "not-in-source"
    if change == "value": item["commitments"] = ["not-in-source"]
    before = deepcopy(workflow._runs)
    assert client.post(URL, json={"forms": [item]}).status_code == status
    assert before == workflow._runs


def test_empty_selection_security_gate_and_duplicate_multipart_never_invoke_provider():
    client, _, adapter = setup(Adapter(clean_result()))
    assert client.post(URL, json={}).status_code == 422
    assert client.post(URL, json={}, headers={"Origin": "https://untrusted.invalid"}).status_code == 403
    response = client.post(BASE, files=[("file", ("a.pdf", PDF, "application/pdf")),
                                      ("file", ("b.pdf", PDF, "application/pdf"))],
                           data={"allow_external_processing": "true"})
    assert response.status_code == 422 and not adapter.calls


@pytest.mark.parametrize("error", [ProviderRateLimit("private"), ProviderUnavailable("private"), ProviderTimeout("private")])
def test_failure_report_is_separate_and_completed_document_survives(error):
    client, workflow, adapter = setup(Adapter(clean_result()))
    successful = upload(client).json(); adapter.error = error
    failed = upload(client).json(); before = deepcopy(workflow._runs)
    response = client.post(URL, json={"forms": [selection(successful)], "failures": [
        {"run_id": failed["run_id"], "review_token": failed["review_token"]}]})
    book = read_workbook(response.content)
    assert len(book["Forms"][1]) == 2
    assert book["ProcessingFailures"][1][0]["failure_code"] == error.code
    assert workflow._runs == before and len(adapter.calls) == 2


def test_legacy_review_codec_export_and_literal_formula_text():
    value = complete()
    value = codec.decode(codec.encode(value), BusinessReviewResult)
    field = value.record.fields[-1]
    selected = [(value.record.binding.document_id, value.record.form_id, v) for v in field.reviewed_values]
    book = read_workbook(export_workbook([value], selected_rows=selected))
    assert len(book["Forms"][1]) == len(set(field.reviewed_values))
    # The reader verifies every XLSX cell is literal text, not an Excel formula.
    assert book["ReviewAudit"][1]


def test_formula_like_unreviewed_identifier_is_literal_and_not_promoted():
    value = clean_result(); value.logical_forms[0].fields.commitment_numbers.values[0].raw = "=1+1"
    client, _, _ = setup(Adapter(value)); run = upload(client).json()
    row = read_workbook(client.post(URL, json={"forms": [selection(run, ["=1+1"])]}).content)["Forms"][1][0]
    assert row["export_selected_commitment"] == "=1+1" and row["commitment_numbers"] == ""


def test_durable_export_uses_only_existing_repository_get_and_never_calls_provider():
    from backend.api.bulk_export_schemas import BulkExportCommand
    from backend.persistence.business_repository import BusinessRepository
    from backend.services.bulk_export import bulk_export
    from backend.services.durable_workflow import DurableWorkflow

    client, memory, adapter = setup(Adapter(clean_result()))
    run = upload(client).json()
    repository = Mock(spec=BusinessRepository)
    repository.get.side_effect = lambda run_id: memory._runs[run_id]
    durable = DurableWorkflow(memory.config, adapter, repository)
    book = bulk_export(durable, BulkExportCommand.model_validate({"forms": [selection(run)]}))
    assert len(read_workbook(book)["Forms"][1]) == 2
    assert [call[0] for call in repository.mock_calls] == ["get"]
    assert len(adapter.calls) == 1


def test_bounds_and_conflicting_duplicates_are_rejected_without_partial_workbook():
    client, _, _ = setup(Adapter(clean_result())); run = upload(client).json()
    first = selection(run); second = {**first, "expected_revision": 99}
    assert client.post(URL, json={"forms": [first, second]}).status_code == 409
    assert client.post(URL, json={"forms": [{**first, "commitments": ["00001"] * 2000}, first]}).status_code == 413
    assert client.post(URL, json={"forms": [{**first, "commitments": [123]}]}).status_code == 422
