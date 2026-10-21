"""Legacy stored payload through real read/export HTTP, mocked storage; no SQL."""

from hashlib import sha256
from unittest.mock import Mock

from fastapi.testclient import TestClient

from backend.core.config import AppConfig
from backend.main import create_app
from backend.persistence import business_codec as codec
from backend.persistence.business_repository import BusinessRepository
from backend.services.business_review import BusinessReviewResult, FIELDS, apply_business_review
from backend.services.demo_workflow import DemoRun
from backend.services.durable_workflow import DurableWorkflow
from tests.test_business_review import action, binding, canonical
from tests.test_excel_export import read_workbook


def test_legacy_persisted_form_reopens_and_expands_only_export_without_provider_or_storage_writes():
    content = b"%PDF-1.7 synthetic legacy original"
    value = canonical()
    source = binding(value, document_id="legacy-document", source_sha256=sha256(content).hexdigest(),
                     data_origin="live", snapshot_kind="live", prompt_version="he-commitment-v2-identifiers")
    original = apply_business_review(value, source, "form-1", [
        action(value, source, name=name, kind="approve_as_extracted", revision=i, decision_id=f"old-{i}")
        for i, name in enumerate(FIELDS)])
    stored = codec.encode(original)

    def load(_):
        result = codec.decode(stored, BusinessReviewResult)
        return DemoRun(source.document_id, "synthetic-token", content, "application/pdf", source.source_sha256,
            0, source.prompt_version, source.pipeline_version, "succeeded", value.extraction_id,
            result.canonical_snapshot_json, (result,))

    repository = Mock(spec=BusinessRepository)
    repository.get.side_effect = load
    provider = Mock()
    provider.provider_name = "synthetic"
    config = AppConfig()
    for _ in range(2):  # Recreate application/service; no new extraction or new persisted form.
        workflow = DurableWorkflow(config, provider, repository)
        app = create_app(config, provider, demo_workflow=workflow)
        with TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50001),
                        headers={"X-Local-Demo": "1"}) as client:
            route = "/api/v1/demo-runs/legacy-document"
            response = client.get(route)
            assert response.status_code == 200
            assert response.json()["prompt_version"] == "he-commitment-v2-identifiers"
            assert response.json()["selected_form"]["revision"] == 7
            assert client.get(route + "/source").content == content
            audit = client.get(route + "/audit").json()["forms"][0]["audit"]
            assert len(audit) == 7
            book = read_workbook(client.get(route + "/export.xlsx").content)
            assert [row["commitment_numbers"] for row in book["Forms"][1]] == ["00001", "00002"]
            assert len(book["ReviewAudit"][1]) == 7
            assert codec.encode(repository.get(source.document_id).results[0]) == stored
    provider.extract.assert_not_called()
    repository.create.assert_not_called()
    repository.complete.assert_not_called()
    repository.review_atomic.assert_not_called()
