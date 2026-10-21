"""Disposable PostgreSQL records search, filters and reopen/correct/restart tests."""

import pytest

from backend.domain import ExtractionResult
from tests.integration.test_business_repository import all_actions, client_for, two_form_pdf, two_forms
from tests.test_demo_api import Adapter, BASE, command, post, upload
from tests.test_excel_export import read_workbook

pytestmark = pytest.mark.postgresql
LIST = "/api/v1/business-records"


def search_fixture():
    value = two_forms().model_dump(mode="json")
    for form, first, last, sender, central, receipt in zip(value["logical_forms"],
            ("נועה", "ליה"), ("כהן", "לוי"), ("מרכז א", "מעבדה ב"), ("0007", "0008"),
            ("2026-02-01", "2026-02-03"), strict=True):
        for name, raw in {"first_name": first, "last_name": last, "sender": sender,
                          "sample_receipt_date": receipt}.items():
            form["fields"][name]["values"] = [{"raw": raw}]
        form["fields"]["central_numbers"]["values"] = [{"raw": central}, {"raw": "008/9)"}]
    return ExtractionResult.model_validate(value)


@pytest.mark.parametrize("term,expected", [("נועה", 1), ("כהן", 1), ("נועה כהן", 1), ("000123", 1),
    ("0007", 1), ("008/9)", 2), ("00001", 2), ("מעבדה ב", 1), ("%' OR 1=1 --", 0), ("%", 0), ("_", 0)])
def test_search_all_required_fields_and_literal_special_characters(pg_engine, term, expected):
    client, _, provider = client_for(pg_engine, Adapter(result=search_fixture()))
    assert upload(client).status_code == 201
    response = client.get(LIST, params={"q": term})
    assert response.status_code == 200
    assert response.json()["total"] == expected
    assert len(provider.calls) == 1


def test_status_sender_date_filters_and_safe_summary_shape(pg_engine):
    client, _, provider = client_for(pg_engine, Adapter(result=search_fixture()))
    run = upload(client).json()
    assert client.get(LIST, params={"status": "pending"}).json()["total"] == 2
    assert post(client, run, all_actions(run)).status_code == 200
    page = client.get(LIST, params={"status": "completed", "sender": "מרכז א", "receipt_date": "2026-02-01"}).json()
    assert page["total"] == 1
    item = page["items"][0]
    assert item["run_id"] == run["run_id"] and item["form_id"] == "form-1" and item["revision"] == 7
    assert item["summary"]["first_name"] == ["שם סינתטי מתוקן"]
    assert item["summary"]["israeli_id"] == ["000123"]
    assert item["created_at"] and item["updated_at"] >= item["created_at"]
    assert set(item) == {"run_id", "document_id", "form_id", "revision", "review_status", "summary", "created_at", "updated_at", "finalized"}
    assert item["finalized"] is False  # Existing field decisions never fabricate a form-level attestation.
    assert client.get(LIST, params={"status": "pending"}).json()["total"] == 1
    assert client.get(LIST, params={"sender": "מרכז"}).json()["total"] == 0  # Exact sender filter.
    assert client.get(LIST, params={"receipt_date": "2026-02-02"}).json()["total"] == 0
    assert len(provider.calls) == 1


def test_pagination_stable_form_scope_and_no_duplicates(pg_engine):
    client, _, _ = client_for(pg_engine)
    runs = [upload(client).json() for _ in range(3)]
    found = []
    for offset in (0, 2, 4):
        response = client.get(LIST, params={"limit": 2, "offset": offset}).json()
        assert response["total"] == 6 and len(response["items"]) == 2 and response["offset"] == offset
        found.extend((row["run_id"], row["form_id"]) for row in response["items"])
    assert len(set(found)) == 6
    assert set(found) == {(run["run_id"], form) for run in runs for form in run["form_ids"]}
    assert not client.get(LIST, params={"offset": 100}).json()["items"]


def test_search_reopen_correct_approved_date_and_restart_preserve_history_export(pg_engine):
    client, workflow, provider = client_for(pg_engine, Adapter(result=search_fixture()))
    source = two_form_pdf()
    run = upload(client, content=source).json()
    for form in run["form_ids"]:
        current = client.get(f"{BASE}/{run['run_id']}", params={"form_id": form}).json()
        assert post(client, current, all_actions(current), form=form).status_code == 200
    before = workflow.repository.get(run["run_id"])
    pg_engine.dispose()
    forbidden = Adapter(error=AssertionError("No new extraction during persisted operations"))
    fresh, persisted, _ = client_for(pg_engine, forbidden)
    listed = fresh.get(LIST, params={"q": "000123", "status": "completed"}).json()["items"][0]
    reopened = fresh.get(f"{BASE}/{listed['run_id']}", params={"form_id": listed["form_id"]}).json()
    assert reopened["selected_form"]["revision"] == 7
    assert fresh.get(f"{BASE}/{listed['run_id']}/source").content == source
    correction = command(reopened, name="sample_receipt_date", values=["2026-02-02"])
    correction["actions"][0]["decision_id"] = "date-correction-after-approval"
    response = post(fresh, reopened, correction)
    assert response.status_code == 200 and response.json()["selected_form"]["revision"] == 8
    after = persisted.repository.get(run["run_id"])
    assert after.results[0].audit[:7] == before.results[0].audit
    assert len(after.results[0].audit) == 8 and after.results[1] == before.results[1]
    assert fresh.get(LIST, params={"receipt_date": "2026-02-01"}).json()["total"] == 0
    assert fresh.get(LIST, params={"receipt_date": "2026-02-02", "status": "completed"}).json()["total"] == 1
    saved_xlsx = fresh.get(f"{BASE}/{run['run_id']}/export.xlsx").content
    assert len(read_workbook(saved_xlsx)["ReviewAudit"][1]) == 15
    pg_engine.dispose()
    last, restored, _ = client_for(pg_engine, forbidden)
    assert restored.repository.get(run["run_id"]) == after
    assert last.get(f"{BASE}/{run['run_id']}/export.xlsx").content == saved_xlsx
    assert len(provider.calls) == 1 and not forbidden.calls


def test_acknowledged_blank_and_rejected_values_do_not_reappear_in_search(pg_engine):
    client, _, _ = client_for(pg_engine)
    run = upload(client).json()
    action = command(run, name="israeli_id", action="acknowledge_blank", values=[])
    assert post(client, run, action).status_code == 200
    assert client.get(LIST, params={"q": "000123"}).json()["total"] == 0
    assert client.get(LIST).json()["items"][0]["summary"]["israeli_id"] == []
