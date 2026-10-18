"""Synthetic-only tests for provider-independent measurement and input freeze."""

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from backend.domain import ExtractionResult
from backend.services.extraction.errors import ProviderNotAuthorized, ProviderRateLimit, ProviderTimeout, ProviderUnavailable
from backend.services.extraction.gemini_adapter import GeminiExtractionAdapter
from backend.tools import extraction_baseline as baseline
from evaluation.scoring import score_extraction
from tests.test_extraction_api import FakeExtractionAdapter
from tests.test_gemini_adapter import _document, _provider_payload, _request
from tests.test_gemini_adapter import _FakeClient, _FakeResponse, _StatusError
from tests.test_gemini_adapter import _authorized_config
from tests.test_scoring import extraction, field, ground_truth, result_field


def authorized_config():
    return replace(_authorized_config(), gemini_model="synthetic-model")


def prepared_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(baseline, "PRIVATE_ROOT", tmp_path)
    directory = tmp_path / "fixtures"
    directory.mkdir()
    pdf = directory / "synthetic.pdf"
    pdf.write_bytes(b"%PDF-1.7 synthetic")
    truth = ground_truth({"israeli_id": field("present", ["001234567"])})
    truth["source_document"]["sha256"] = baseline.sha256(pdf)
    truth_path = directory / "truth.json"
    truth_path.write_text(json.dumps(truth), encoding="utf-8")
    entry = {
        "fixture_id": truth["fixture_id"], "document_path": str(pdf),
        "document_sha256": baseline.sha256(pdf), "ground_truth_path": str(truth_path),
        "ground_truth_sha256": baseline.sha256(truth_path),
        "source": {"path": "synthetic.pdf", "page_1based": 1},
    }
    return {"fixture_set": "dev", "approved_demo_data": True, "approved_provider": "gemini", "entries": [entry]}


def test_input_freeze_rejects_changed_truth_and_pdf(tmp_path, monkeypatch):
    manifest = prepared_manifest(tmp_path, monkeypatch)
    assert len(baseline.prepare_entries(manifest)) == 1
    path = Path(manifest["entries"][0]["ground_truth_path"])
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="Ground truth checksum"):
        baseline.prepare_entries(manifest)
    pdf = Path(manifest["entries"][0]["document_path"])
    pdf.write_bytes(b"different document")
    with pytest.raises(ValueError, match="PDF checksum"):
        baseline.prepare_entries(manifest)


def test_measurement_does_not_rewrite_provider_form_identity_or_values():
    result = ExtractionResult.model_validate(extraction({
        "israeli_id": result_field("present", ["001234567"])
    }))
    adapter = FakeExtractionAdapter(result=result)
    record, payload = baseline.measured_extraction(adapter, _request(), _document())
    assert record["status"] == "succeeded"
    assert payload == result.model_dump(mode="json", exclude_none=True)
    truth = ground_truth({"israeli_id": field("present", ["001234567"])})
    truth["logical_forms"][0]["form_id"] = "different-ground-truth-id"
    score = score_extraction(truth, payload)
    assert score["logical_forms"]["matched_count"] == 0
    assert score["overall"]["missing_values"] == 1


def test_attempt_observer_preserves_retry_behavior_and_safe_error_details():
    adapter = FakeExtractionAdapter(error=ProviderTimeout("Safe timeout"))
    adapter._call_provider = Mock(side_effect=TimeoutError("SECRET"))
    original = adapter._call_provider
    def extract(request, document):
        for _ in range(2):
            try:
                adapter._call_provider()
            except TimeoutError:
                pass
        raise ProviderTimeout("Safe timeout")
    adapter.extract = extract
    record, result = baseline.measured_extraction(adapter, _request(), _document())
    assert result is None
    assert record["retry_observed"] is True
    assert len(record["adapter_attempts"]) == 2
    assert "SECRET" not in json.dumps(record)
    assert adapter._call_provider is original


def test_summary_uses_field_counts_and_keeps_document_isolation():
    truth = ground_truth({"israeli_id": field("present", ["001234567"])})
    perfect = score_extraction(truth, extraction({"israeli_id": result_field("present", ["001234567"])}))
    missing = score_extraction(truth, extraction({}))
    summary = baseline.summarize_scores([perfect, missing])
    assert summary["scored_documents"] == 2
    assert summary["overall"]["evaluated_fields"] == 14
    assert summary["overall"]["exact_matches"] == 13
    assert summary["overall"]["missing_values"] == 1
    assert summary["logical_forms"]["matched_count"] == 2


def test_baseline_denies_before_factory_when_server_policy_is_disabled(tmp_path, monkeypatch):
    manifest = prepared_manifest(tmp_path, monkeypatch)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    factory = Mock(side_effect=AssertionError("No provider construction allowed"))
    monkeypatch.setattr(baseline, "build_extraction_adapter", factory)
    monkeypatch.setattr(baseline.AppConfig, "from_env", lambda: baseline.AppConfig())
    with pytest.raises(ProviderNotAuthorized):
        baseline.run_baseline(manifest_path, tmp_path / "results.json", provider="gemini", authorize=True)
    factory.assert_not_called()


def test_artifact_path_cannot_escape_private_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(baseline, "PRIVATE_ROOT", tmp_path / "private")
    with pytest.raises(ValueError):
        baseline.checked_artifact_path(str(tmp_path / "public.json"))


def test_full_run_checkpoints_exact_output_and_refuses_overwrite(tmp_path, monkeypatch):
    manifest = prepared_manifest(tmp_path, monkeypatch)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = ExtractionResult.model_validate(extraction({
        "israeli_id": result_field("present", ["001234567"])
    }))
    factory = Mock(return_value=FakeExtractionAdapter(result=result))
    monkeypatch.setattr(baseline, "build_extraction_adapter", factory)
    monkeypatch.setattr(baseline.AppConfig, "from_env", authorized_config)
    output = tmp_path / "run.json"
    report = baseline.run_baseline(manifest_path, output, provider="gemini", authorize=True)
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved == report
    assert saved["documents"][0]["extraction_result"] == result.model_dump(mode="json", exclude_none=True)
    assert saved["summary"]["selected_scorable_fields"] == 7
    assert saved["summary"]["end_to_end_exact_match_rate"] == 1.0
    with pytest.raises(ValueError, match="preserve the previous run"):
        baseline.run_baseline(manifest_path, output, provider="gemini", authorize=True)
    factory.assert_called_once()


@pytest.mark.parametrize("warning", ["needs_review", "needs_clarification"])
def test_present_provider_warnings_enter_review_required_fields_without_changing_scores(tmp_path, monkeypatch, warning):
    manifest = prepared_manifest(tmp_path, monkeypatch)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    payload = _provider_payload()
    payload["logical_forms"][0]["fields"]["central_numbers"]["review_status"] = warning
    original = deepcopy(payload)
    client = _FakeClient(_FakeResponse(payload))
    monkeypatch.setattr(baseline.AppConfig, "from_env", authorized_config)
    report = baseline.run_baseline(
        manifest_path, tmp_path / "run.json", provider="gemini", authorize=True,
        adapter_factory=lambda config: GeminiExtractionAdapter(config=config, client_factory=lambda *_: client),
    )
    record = report["documents"][0]
    result = record["extraction_result"]
    central = result["logical_forms"][0]["fields"]["central_numbers"]
    assert central["value_status"] == "present" and central["review_status"] == warning
    assert central["values"] == original["logical_forms"][0]["fields"]["central_numbers"]["values"]
    assert {"form_id": "form-001", "field": "central_numbers"} in record["review_required_fields"]
    assert json.loads((tmp_path / "run.json").read_text(encoding="utf-8")) == report
    assert len(client.calls) == 1 and payload == original
    # Compare against the former canonical review default, keeping values identical.
    previous_view = deepcopy(result)
    previous_view["logical_forms"][0]["fields"]["central_numbers"]["review_status"] = "unreviewed"
    entry, truth, _ = baseline.prepare_entries(manifest)[0]
    _, previous_score = baseline.score_fixture(entry, truth, previous_view)
    assert record["scoring"] == previous_score


def test_failed_run_keeps_failure_separate_and_in_end_to_end_denominator(tmp_path, monkeypatch):
    manifest = prepared_manifest(tmp_path, monkeypatch)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(baseline, "build_extraction_adapter", lambda _: FakeExtractionAdapter(
        error=ProviderTimeout("Safe timeout")
    ))
    monkeypatch.setattr(baseline.AppConfig, "from_env", authorized_config)
    report = baseline.run_baseline(manifest_path, tmp_path / "run.json", provider="gemini", authorize=True)
    assert report["documents"][0]["extraction_result"] is None
    assert report["documents"][0]["scoring"] is None
    assert report["summary"]["scored_documents"] == 0
    assert report["summary"]["failed_documents"] == 1
    assert report["summary"]["overall"]["exact_match_rate"] is None
    assert report["summary"]["end_to_end_exact_match_rate"] == 0.0


def test_quota_stop_does_not_present_unattempted_documents_as_zero_accuracy(tmp_path, monkeypatch):
    manifest = prepared_manifest(tmp_path, monkeypatch)
    second = deepcopy(manifest["entries"][0])
    second["fixture_id"] = "synthetic-second"
    truth = json.loads(Path(second["ground_truth_path"]).read_text(encoding="utf-8"))
    truth["fixture_id"] = second["fixture_id"]
    second_path = tmp_path / "fixtures" / "second-truth.json"
    second_path.write_text(json.dumps(truth), encoding="utf-8")
    second["ground_truth_path"] = str(second_path)
    second["ground_truth_sha256"] = baseline.sha256(second_path)
    manifest["entries"].append(second)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    adapter = FakeExtractionAdapter(error=ProviderRateLimit("Safe quota error", retryable=False))
    monkeypatch.setattr(baseline, "build_extraction_adapter", lambda _: adapter)
    monkeypatch.setattr(baseline.AppConfig, "from_env", authorized_config)
    report = baseline.run_baseline(manifest_path, tmp_path / "run.json", provider="gemini", authorize=True)
    assert len(adapter.calls) == 1
    assert report["summary"]["attempted_documents"] == 1
    assert report["summary"]["selected_documents"] == 2
    assert report["summary"]["selected_scorable_fields"] == 14
    assert report["summary"]["attempted_scorable_fields"] == 7
    assert report["summary"]["end_to_end_exact_match_rate"] is None


@pytest.mark.parametrize("status,provider_status,error_code", [
    (429, "RESOURCE_EXHAUSTED", "PROVIDER_RATE_LIMIT"),
    (404, "NOT_FOUND", "PROVIDER_RESOURCE_UNAVAILABLE"),
])
def test_gemini_baseline_uses_existing_adapter_and_stops_on_provider_block(
    tmp_path, monkeypatch, status, provider_status, error_code
):
    manifest = prepared_manifest(tmp_path, monkeypatch)
    manifest["approved_provider"] = "gemini"
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    config = replace(authorized_config(), approved_ai_providers=("gemini",),
                     gemini_api_key="synthetic", gemini_model="synthetic-gemini-model")
    monkeypatch.setattr(baseline.AppConfig, "from_env", lambda: config)
    client = _FakeClient(_StatusError(status, status=provider_status))
    default_factory = Mock(side_effect=AssertionError("No default or fallback provider"))
    monkeypatch.setattr(baseline, "build_extraction_adapter", default_factory)
    report = baseline.run_baseline(
        manifest_path, tmp_path / "gemini-run.json", provider="gemini", authorize=True,
        adapter_factory=lambda c: GeminiExtractionAdapter(
            config=c, max_retries=0, client_factory=lambda *_: client,
        ),
    )
    default_factory.assert_not_called()
    assert report["provider"] == "gemini"
    assert report["model"] == "synthetic-gemini-model"
    assert report["documents"][0]["error"]["code"] == error_code
    assert report["documents"][0]["retry_observed"] is False
    assert len(client.calls) == 1


def test_single_form_scoring_alignment_preserves_original_payload_and_values():
    truth = ground_truth({"israeli_id": field("present", ["001234567"])})
    payload = extraction({"israeli_id": result_field("present", ["001234567"])})
    payload["logical_forms"][0]["form_id"] = "provider-1"
    original = deepcopy(payload)
    association, score = baseline.score_fixture({"ground_truth_sha256": "frozen-hash"}, truth, payload)
    assert association["provider_form_id"] == "provider-1"
    assert association["scoring_logical_form_id"] == "form-1"
    assert score["overall"]["exact_matches"] == 7
    assert payload == original


@pytest.mark.parametrize("count", [0, 2])
def test_scoring_alignment_rejects_zero_or_multiple_forms(count):
    truth = ground_truth({})
    payload = extraction({})
    payload["logical_forms"] = [deepcopy(payload["logical_forms"][0]) for _ in range(count)]
    original = deepcopy(payload)
    association, score = baseline.score_fixture({"ground_truth_sha256": "frozen"}, truth, payload)
    assert association["status"] == "identity_structure_failure"
    assert score is None
    assert payload == original


def test_scoring_alignment_is_not_based_on_extracted_values():
    truth = ground_truth({"israeli_id": field("present", ["001234567"])})
    payload = extraction({"israeli_id": result_field("present", ["WRONG"])})
    payload["logical_forms"][0]["form_id"] = "other"
    association, score = baseline.score_fixture({"ground_truth_sha256": "frozen"}, truth, payload)
    assert association["scoring_logical_form_id"] == "form-1"
    assert score["fields"]["israeli_id"]["exact_matches"] == 0
    assert score["fields"]["israeli_id"]["false_positive_values"] == 1


def test_scoring_alignment_refuses_multi_page_fixture():
    truth = ground_truth({})
    truth["source_document"]["page_count"] = 2
    association, score = baseline.score_fixture({"ground_truth_sha256": "frozen"}, truth, extraction({}))
    assert association["status"] == "identity_structure_failure"
    assert score is None


class SequenceAdapter:
    provider_name = "fake"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def extract(self, request, document):
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def unavailable(status=503):
    return ProviderUnavailable("Safe transient failure", diagnostics={"http_status_code": status})


def test_measurement_retries_recover_with_exponential_backoff():
    result = ExtractionResult.model_validate(extraction({}))
    adapter = SequenceAdapter([unavailable(), unavailable(), result])
    delays = []
    record, payload = baseline.measured_with_retries(
        adapter, _request(), _document(), max_retries=2, sleep=delays.append
    )
    assert adapter.calls == 3
    assert delays == [1.0, 2.0]
    assert record["status"] == "succeeded"
    assert record["retry_observed"] is True
    assert len(record["measurement_attempts"]) == 3
    assert record["measurement_attempts"][0]["error"]["code"] == "PROVIDER_UNAVAILABLE"
    assert payload == result.model_dump(mode="json", exclude_none=True)


def test_measurement_stops_after_two_retries_when_transient_error_persists():
    adapter = SequenceAdapter([unavailable(), unavailable(), unavailable()])
    record, payload = baseline.measured_with_retries(
        adapter, _request(), _document(), max_retries=2, sleep=lambda _: None
    )
    assert adapter.calls == 3
    assert payload is None
    assert record["status"] == "failed"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 429])
def test_measurement_never_retries_permanent_http_status_even_if_marked_retryable(status):
    adapter = SequenceAdapter([unavailable(status)])
    sleep = Mock()
    record, _ = baseline.measured_with_retries(adapter, _request(), _document(), max_retries=2, sleep=sleep)
    assert adapter.calls == 1
    assert len(record["measurement_attempts"]) == 1
    sleep.assert_not_called()


def test_measurement_never_retries_quota_error_with_retryable_flag():
    adapter = SequenceAdapter([ProviderRateLimit("Safe quota error", retryable=True)])
    record, _ = baseline.measured_with_retries(adapter, _request(), _document(), max_retries=2)
    assert adapter.calls == 1
    assert len(record["measurement_attempts"]) == 1


def test_resume_reuses_success_without_constructing_provider_or_mutating_output(tmp_path, monkeypatch):
    manifest = prepared_manifest(tmp_path, monkeypatch)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = ExtractionResult.model_validate(extraction({"israeli_id": result_field("present", ["001234567"])}))
    result.extraction_id = "task7-synthetic-fixture"
    result.provider.name = "gemini"
    result.provider.version = "synthetic-model"
    result.logical_forms[0].form_id = "provider-id"
    monkeypatch.setattr(baseline.AppConfig, "from_env", authorized_config)
    factory = Mock(return_value=FakeExtractionAdapter(result=result))
    monkeypatch.setattr(baseline, "build_extraction_adapter", factory)
    previous_path = tmp_path / "previous.json"
    previous = baseline.run_baseline(manifest_path, previous_path, provider="gemini", authorize=True)
    previous_hash = baseline.sha256(previous_path)
    factory.reset_mock()
    resumed = baseline.run_baseline(
        manifest_path, tmp_path / "resumed.json", provider="gemini", authorize=True, resume_from=previous_path
    )
    factory.assert_not_called()
    assert resumed["summary"]["reused_documents"] == 1
    assert resumed["documents"][0]["extraction_result"] == previous["documents"][0]["extraction_result"]
    assert resumed["documents"][0]["provider_form_id"] == "provider-id"
    assert resumed["documents"][0]["scoring_logical_form_id"] == "form-1"
    assert baseline.sha256(previous_path) == previous_hash
    previous["model"] = "different-model"
    previous_path.write_text(json.dumps(previous), encoding="utf-8")
    with pytest.raises(ValueError, match="provenance"):
        baseline.run_baseline(manifest_path, tmp_path / "rejected.json", provider="gemini", authorize=True,
                              resume_from=previous_path)
    factory.assert_not_called()


def test_exhausted_transient_document_does_not_prevent_next_document(tmp_path, monkeypatch):
    manifest = prepared_manifest(tmp_path, monkeypatch)
    second = deepcopy(manifest["entries"][0])
    second["fixture_id"] = "synthetic-second"
    truth = json.loads(Path(second["ground_truth_path"]).read_text(encoding="utf-8"))
    truth["fixture_id"] = second["fixture_id"]
    second_path = tmp_path / "fixtures" / "second.json"
    second_path.write_text(json.dumps(truth), encoding="utf-8")
    second.update(ground_truth_path=str(second_path), ground_truth_sha256=baseline.sha256(second_path))
    manifest["entries"].append(second)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(baseline.AppConfig, "from_env", authorized_config)
    result = ExtractionResult.model_validate(extraction({"israeli_id": result_field("present", ["001234567"])}))
    adapter = SequenceAdapter([unavailable(), unavailable(), unavailable(), result])
    delays = []
    report = baseline.run_baseline(
        manifest_path, tmp_path / "run.json", provider="gemini", authorize=True,
        adapter_factory=lambda _: adapter, max_transient_retries=2, sleep=delays.append,
    )
    assert adapter.calls == 4
    assert delays == [1.0, 2.0]
    assert report["summary"]["attempted_documents"] == 2
    assert report["summary"]["failed_documents"] == 1
    assert report["documents"][1]["status"] == "succeeded"
