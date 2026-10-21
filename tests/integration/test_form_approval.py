"""Opt-in disposable PostgreSQL only; never load a DSN or migrate a main database."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from sqlalchemy import select, text

from backend.persistence.business_models import events
from backend.services.extraction.errors import ProviderTimeout
from tests.integration.test_business_repository import client_for, two_form_pdf
from tests.test_demo_api import Adapter, BASE, command, post, upload
from tests.test_excel_export import read_workbook
from tests.test_form_approval import clean_result, finalize_command, http_finalize

pytestmark = pytest.mark.postgresql


def pair():
    result = clean_result()
    second = deepcopy(result.logical_forms[0])
    second.form_id = "form-2"
    second.fields.israeli_id.value_status = "missing"
    second.fields.israeli_id.values = []
    result.logical_forms.append(second)
    return result


def test_durable_two_forms_restart_reopen_identical_export_and_single_form_events(pg_engine):
    client, workflow, adapter = client_for(pg_engine, Adapter(pair()))
    expected = {}
    with client:
        run = upload(client, content=two_form_pdf()).json()
        source = workflow.repository.get(run["run_id"])
        first = http_finalize(client, run)
        assert first.status_code == 200
        expected["form-1"] = first.json()
        second = client.get(f"{BASE}/{run['run_id']}", params={"form_id": "form-2"}).json()
        assert http_finalize(client, second).status_code == 422
        blank = post(client, second, command(second, name="israeli_id", action="acknowledge_blank"), form="form-2")
        second_final = http_finalize(client, blank.json())
        assert second_final.status_code == 200
        expected["form-2"] = second_final.json()
        assert len(adapter.calls) == 1
        before = workflow.repository.get(run["run_id"])
        assert before.source == source.source and before.canonical_snapshot_json == source.canonical_snapshot_json
        workbook = client.get(f"{BASE}/{run['run_id']}/export.xlsx").content
        assert len(read_workbook(workbook)["Forms"][1]) == 4
        audit = client.get(f"{BASE}/{run['run_id']}/audit").json()
        with pg_engine.connect() as conn:
            saved = conn.execute(select(events).where(events.c.document_id == run["run_id"])).mappings().all()
            assert len(saved) == 3 and sum(e["field_name"] == "__form__" for e in saved) == 2
        listed = client.get("/api/v1/business-records?status=completed&q=00001").json()
        assert listed["total"] == 2 and all(row["finalized"] for row in listed["items"])
    pg_engine.dispose()
    fresh, reopened_workflow, forbidden = client_for(pg_engine, Adapter(error=AssertionError("NO_PROVIDER_ON_REOPEN")))
    with fresh:
        assert reopened_workflow.repository.get(run["run_id"]) == before
        for form_id, projection in expected.items():
            assert fresh.get(f"{BASE}/{run['run_id']}", params={"form_id": form_id}).json() == projection
        assert fresh.get(f"{BASE}/{run['run_id']}/audit").json() == audit
        assert fresh.get(f"{BASE}/{run['run_id']}/export.xlsx").content == workbook
        assert not forbidden.calls


def test_durable_concurrency_replay_and_correction_invalidates_finalization(pg_engine):
    client, workflow, _ = client_for(pg_engine, Adapter(clean_result()))
    with client:
        run = upload(client).json()
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(lambda i: http_finalize(client, run, finalize_command(run, decision_id=f"form-{i}")), range(2)))
        assert sorted(r.status_code for r in replies) == [200, 409]
        winning = next(i for i, r in enumerate(replies) if r.status_code == 200)
        assert http_finalize(client, run, finalize_command(run, decision_id=f"form-{winning}")).status_code == 200
        current = client.get(f"{BASE}/{run['run_id']}").json()
        corrected = post(client, current, command(current, name="sample_receipt_date", values=["18.08.2026"]))
        assert corrected.status_code == 200 and corrected.json()["selected_form"]["finalized"] is False
        final = http_finalize(client, corrected.json(), finalize_command(corrected.json(), decision_id="again"))
        assert final.status_code == 200
        assert len(workflow.repository.get(run["run_id"]).results[0].audit) == 3


def test_absent_migration_fails_explicitly_without_writing_review_events(pg_engine):
    client, workflow, _ = client_for(pg_engine, Adapter(clean_result()))
    with pg_engine.begin() as conn:
        conn.execute(text("ALTER TABLE business_review_events DROP CONSTRAINT ck_business_event_field"))
        conn.execute(text("ALTER TABLE business_review_events ADD CONSTRAINT ck_business_event_field "
                          "CHECK (field_name <> '__form__')"))
    with client:
        run = upload(client).json()
        before = workflow.repository.get(run["run_id"])
        response = http_finalize(client, run)
        assert response.status_code == 503 and response.json()["error"]["code"] == "FORM_APPROVAL_MIGRATION_REQUIRED"
        assert workflow.repository.get(run["run_id"]) == before
