"""Offline list API bounds, access boundary and SQL parameterization."""

from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from backend.api.records_schemas import RecordsQuery
from backend.core.config import AppConfig
from backend.main import create_app
from backend.persistence.business_models import documents, records
from backend.persistence.business_repository import BusinessRepository
from backend.persistence.business_search import conditions, literal_pattern
from backend.services.durable_workflow import DurableWorkflow
from tests.test_demo_api import Adapter, setup


def list_client():
    repository = Mock(spec=BusinessRepository)
    repository.list_records.return_value = {"items": [], "total": 0, "limit": 20, "offset": 0}
    config, provider = AppConfig(), Adapter()
    app = create_app(config, provider, demo_workflow=DurableWorkflow(config, provider, repository))
    return TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50001),
                      headers={"X-Local-Demo": "1"}), repository, provider


def test_list_query_is_typed_bounded_and_never_extracts():
    client, repository, provider = list_client()
    response = client.get("/api/v1/business-records", params={"q": "0007 עברית", "status": "completed",
        "sender": "מעבדה", "receipt_date": "2026-02-01", "limit": 10, "offset": 20})
    assert response.status_code == 200
    query = repository.list_records.call_args.args[0]
    assert query.q == "0007 עברית" and query.status == "completed" and query.offset == 20
    assert query.receipt_date.isoformat() == "2026-02-01"
    assert not provider.calls and response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"offset": 100001},
    {"status": "approved"}, {"receipt_date": "2026-02-30"}, {"q": "x" * 201}, {"sender": "x" * 201},
    {"unexpected": "not_allowed"}])
def test_invalid_list_queries_do_not_reach_repository_or_echo_values(params):
    client, repository, provider = list_client()
    response = client.get("/api/v1/business-records", params=params)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_DEMO_INPUT"
    assert "not_allowed" not in response.text
    repository.list_records.assert_not_called()
    assert not provider.calls


def test_list_endpoint_preserves_loopback_origin_and_default_app_boundary():
    client, repository, provider = list_client()
    assert client.get("/api/v1/business-records", headers={"Origin": "https://foreign.invalid"}).status_code == 403
    repository.list_records.assert_not_called()
    memory_client, _, _ = setup()
    response = memory_client.get("/api/v1/business-records")
    assert response.status_code == 404 and response.json()["error"]["code"] == "DURABLE_RECORDS_UNAVAILABLE"
    assert TestClient(create_app(AppConfig(), provider)).get("/api/v1/business-records").status_code == 404
    assert not provider.calls


def test_search_is_bound_and_sql_wildcards_are_literal():
    malicious = "x%' OR 1=1 --_!"
    query = RecordsQuery(q=malicious, sender=malicious)
    statement = select(records.c.form_id).select_from(records.join(documents)).where(*conditions(query))
    compiled = statement.compile(dialect=postgresql.dialect())
    assert malicious not in str(compiled)
    assert "%" + literal_pattern(malicious) + "%" in compiled.params.values()
    assert literal_pattern("100%_!") == "100!%!_!!"
