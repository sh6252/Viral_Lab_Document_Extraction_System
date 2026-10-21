"""Real PostgreSQL tests; never substitute SQLite for restart/transaction evidence."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from html import escape

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete, inspect, select, update
from sqlalchemy.exc import SQLAlchemyError

from backend.core.config import AppConfig
from backend.domain import ExtractionResult
from backend.main import create_app
from backend.persistence import business_codec as codec
from backend.persistence.business_models import documents, events, records
from backend.persistence.business_repository import BusinessRepository
from backend.services.demo_workflow import DemoError
from backend.services.durable_workflow import DurableWorkflow
from backend.services.extraction.errors import ProviderTimeout
from tests.test_business_review import canonical
from tests.test_demo_api import Adapter, BASE, command, post, upload
from tests.test_excel_export import read_workbook

pytestmark = pytest.mark.postgresql


def two_forms():
    payload = canonical().model_dump(mode="json")
    second = deepcopy(payload["logical_forms"][0])
    second["form_id"] = "form-2"
    second["fields"]["israeli_id"] = {"value_status": "missing", "values": [], "review_status": "needs_review"}
    payload["logical_forms"].append(second)
    return ExtractionResult.model_validate(payload)


def two_form_pdf():
    """Valid private-fixture-free PDF matching the mocked provider's two forms."""
    import pymupdf

    with pymupdf.open() as document:
        for form in two_forms().logical_forms:
            page = document.new_page()
            rows = []
            for name, field in form.fields:
                values = "<br>".join(escape(value.raw) for value in field.values)
                rows.append(f"<tr><td>{escape(name)}</td><td>{values}</td></tr>")
            html = "<h1>Synthetic form " + escape(form.form_id) + "</h1><table>" + "".join(rows) + "</table>"
            spare, scale = page.insert_htmlbox(pymupdf.Rect(36, 36, 559, 806), html,
                                               css="body {font-size: 12pt;} td {padding: 8pt;}", scale_low=1)
            assert spare >= 0 and scale == 1
        return document.tobytes()


def client_for(engine, adapter=None):
    adapter = Adapter(result=two_forms()) if adapter is None else adapter
    repository = BusinessRepository(engine)
    service = DurableWorkflow(AppConfig(), adapter, repository)
    app = create_app(service.config, adapter, demo_workflow=service)
    client = TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50001),
                        headers={"X-Local-Demo": "1"})
    return client, service, adapter


def all_actions(run):
    body = command(run)
    body["actions"] = []
    for field in run["selected_form"]["fields"]:
        name = field["field_name"]
        missing = field["provider_value_status"] == "missing"
        kind = "acknowledge_blank" if missing else "approve_as_extracted"
        values = [] if missing else [v["raw"] for v in field["provider_values"]]
        if name == "first_name":
            kind, values = "correct_and_approve", ["שם סינתטי מתוקן"]
        body["actions"].append({"field_name": name, "decision_id": "decision-" + name,
            "action": kind, "reviewed_values": values, "reason": "Synthetic explicit source review"})
    return body


def test_two_form_fourteen_decisions_restart_reopen_identical_xlsx_without_provider(pg_engine):
    client, service, adapter = client_for(pg_engine)
    source_pdf = two_form_pdf()
    commands, projections = {}, {}
    with client:
        response = upload(client, content=source_pdf)
        assert response.status_code == 201
        run = response.json()
        assert len(adapter.calls) == 1 and len(run["form_ids"]) == 2
        for form_id in run["form_ids"]:
            selected = client.get(f"{BASE}/{run['run_id']}", params={"form_id": form_id}).json()
            commands[form_id] = all_actions(selected)
            saved = post(client, selected, commands[form_id], form=form_id)
            assert saved.status_code == 200
            projections[form_id] = saved.json()
            assert saved.json()["selected_form"]["revision"] == 7
            assert saved.json()["selected_form"]["review_status"] == "complete"
        before = service.repository.get(run["run_id"])
        audit = client.get(f"{BASE}/{run['run_id']}/audit").json()
        assert sum(len(f["audit"]) for f in audit["forms"]) == 14
        workbook = client.get(f"{BASE}/{run['run_id']}/export.xlsx").content
        sheets = read_workbook(workbook)
        assert len(sheets["Forms"][1]) == 4 and len(sheets["ReviewAudit"][1]) == 14
        assert [row["commitment_numbers"] for row in sheets["Forms"][1]] == ["00001", "00002", "00001", "00002"]
    pg_engine.dispose()  # Close connections and recreate the entire ASGI/service/repository stack.
    forbidden = Adapter(error=AssertionError("provider must not run during reopen"))
    fresh, workflow, _ = client_for(pg_engine, forbidden)
    workflow.clock = lambda: 10**12  # Durable records do not use the old process TTL.
    with fresh:
        assert workflow.repository.get(run["run_id"]) == before
        assert fresh.get(f"{BASE}/{run['run_id']}/source").content == source_pdf
        assert fresh.get(f"{BASE}/{run['run_id']}/audit").json() == audit
        for form_id, expected in projections.items():
            assert fresh.get(f"{BASE}/{run['run_id']}", params={"form_id": form_id}).json() == expected
            assert post(fresh, expected, commands[form_id], form=form_id).json() == expected
        assert fresh.get(f"{BASE}/{run['run_id']}/export.xlsx").content == workbook
        assert not forbidden.calls
    first, second = before.results
    identity = next(f for f in first.record.fields if f.field_name == "israeli_id")
    assert identity.original.values[0].raw == "000123" and identity.original.values[0].normalized == "123"
    assert next(f for f in second.record.fields if f.field_name == "israeli_id").review_status == "acknowledged_blank"
    assert next(f for f in first.record.fields if f.field_name == "central_numbers").reviewed_values == ("0007", "008/9)", "0007")
    assert first.audit[0].action.operator_declaration == commands["form-1"]["operator_declaration"]


def test_denied_consent_and_provider_failure_persist_without_business_records(pg_engine):
    client, service, adapter = client_for(pg_engine)
    run = upload(client, consent=False).json()
    assert not adapter.calls and service.repository.get(run["run_id"]).failure.failure_code == "PROVIDER_NOT_AUTHORIZED"
    failing, workflow, provider = client_for(pg_engine, Adapter(error=ProviderTimeout("PRIVATE")))
    failed = upload(failing).json()
    pg_engine.dispose()
    saved = BusinessRepository(pg_engine).get(failed["run_id"])
    assert saved.status == "failed" and not saved.results and saved.failure.failure_code == "PROVIDER_TIMEOUT"
    assert len(provider.calls) == 1
    assert b"PRIVATE" not in failing.get(f"{BASE}/{failed['run_id']}/export.xlsx").content


def test_stale_conflicting_forged_cross_form_and_invalid_batch_are_atomic(pg_engine):
    client, service, _ = client_for(pg_engine)
    run = upload(client).json()
    body = command(run, action="approve_as_extracted")
    assert post(client, run, body).status_code == 200
    baseline = service.repository.get(run["run_id"])
    stale = deepcopy(body)
    stale["actions"][0]["decision_id"] = "new-id"
    assert post(client, run, stale).status_code == 409
    conflict = deepcopy(body)
    conflict["actions"][0]["reason"] = "different"
    assert post(client, run, conflict).status_code == 409
    forged = deepcopy(body)
    forged["review_token"] = "wrong"
    assert post(client, run, forged).status_code == 404
    assert post(client, run, body, form="unknown").status_code == 404
    other = upload(client).json()
    assert post(client, other, body).status_code == 404
    current = client.get(f"{BASE}/{run['run_id']}").json()
    invalid = all_actions(current)
    invalid["actions"][-1]["reviewed_values"] = []
    assert post(client, current, invalid).status_code == 422
    assert service.repository.get(run["run_id"]) == baseline


def test_event_insert_failure_rolls_back_whole_review_without_value_echo(pg_engine, monkeypatch):
    client, service, _ = client_for(pg_engine)
    run = upload(client).json()
    original = service.repository.get(run["run_id"])
    append = service.repository._append_events
    def fail_after_insert(*args):
        append(*args)
        raise ValueError("PRIVATE sensitive database detail")
    monkeypatch.setattr(service.repository, "_append_events", fail_after_insert)
    response = post(client, run, all_actions(run))
    assert response.status_code == 503 and "PRIVATE" not in response.text
    assert service.repository.get(run["run_id"]) == original
    with pg_engine.connect() as conn:
        assert not conn.execute(select(events).where(events.c.document_id == run["run_id"])).all()


@pytest.mark.parametrize("identical", [True, False])
def test_concurrent_workers_serialize_and_replay_or_conflict(pg_engine, identical):
    client, service, _ = client_for(pg_engine)
    run = upload(client).json()
    first = command(run, action="approve_as_extracted")
    second = deepcopy(first)
    if not identical:
        second["actions"][0]["decision_id"] = "other-worker"
    def send(body):
        independent, _, _ = client_for(pg_engine)
        return post(independent, run, body).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(send, [first, second]))
    assert statuses == ([200, 200] if identical else [200, 409])
    result = service.repository.get(run["run_id"]).results[0]
    assert result.record.revision == 1 and len(result.audit) == 1


@pytest.mark.parametrize("operation", ["update_event", "delete_event", "update_source", "delete_record"])
def test_database_guards_preserve_immutable_source_and_append_only_events(pg_engine, operation):
    client, _, _ = client_for(pg_engine)
    run = upload(client).json()
    assert post(client, run, command(run, action="approve_as_extracted")).status_code == 200
    statements = {
        "update_event": update(events).where(events.c.document_id == run["run_id"]).values(event_json="tampered"),
        "delete_event": delete(events).where(events.c.document_id == run["run_id"]),
        "update_source": update(documents).where(documents.c.document_id == run["run_id"]).values(source_bytes=b"changed"),
        "delete_record": delete(records).where(records.c.document_id == run["run_id"]),
    }
    with pytest.raises(SQLAlchemyError):
        with pg_engine.begin() as conn:
            conn.execute(statements[operation])


def test_hashed_but_invalid_projection_fails_closed(pg_engine):
    client, service, _ = client_for(pg_engine)
    run = upload(client).json()
    assert post(client, run, command(run, action="approve_as_extracted")).status_code == 200
    with pg_engine.begin() as conn:
        conn.execute(update(records).where(records.c.document_id == run["run_id"], records.c.form_id == "form-1")
                     .values(revision=2, projection_json="{}", projection_sha256=codec.digest("{}")))
    response = client.get(f"{BASE}/{run['run_id']}")
    assert response.status_code == 503 and response.json()["error"]["code"] == "BUSINESS_STORAGE_UNAVAILABLE"


def test_migration_tables_and_processing_document_never_reextracts(pg_engine):
    assert {"business_documents", "business_records", "business_review_events"} <= set(inspect(pg_engine).get_table_names())
    client, service, adapter = client_for(pg_engine)
    original_complete = service.repository.complete
    service.repository.complete = lambda run: (_ for _ in ()).throw(DemoError("BUSINESS_STORAGE_UNAVAILABLE", 503))
    response = upload(client)
    assert response.status_code == 503 and len(adapter.calls) == 1
    with pg_engine.connect() as conn:
        run_id = conn.execute(select(documents.c.document_id).where(documents.c.status == "processing")).scalar_one()
    service.repository.complete = original_complete
    reloaded = client.get(f"{BASE}/{run_id}").json()
    assert reloaded["status"] == "processing" and not reloaded["form_ids"] and len(adapter.calls) == 1
