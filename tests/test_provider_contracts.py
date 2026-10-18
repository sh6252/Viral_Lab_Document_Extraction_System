"""The same candidate and isolation rules apply to every implemented provider."""

from copy import deepcopy

import pytest

from backend.domain import ExtractionFields, FieldResult
from backend.services.extraction.canonical import sanitize_fields
from backend.services.extraction.errors import InvalidProviderResponse
from backend.services.extraction.gemini_adapter import GeminiExtractionAdapter
from backend.tools.extraction_smoke import SYNTHETIC_GROUND_TRUTH_PATH, load_json
from evaluation.scoring import score_extraction
from tests.test_gemini_adapter import (
    _FakeClient, _FakeResponse, _authorized_config, _document, _provider_payload, _request,
)


def adapter_for(provider, payload):
    return GeminiExtractionAdapter(
        config=_authorized_config(),
        client_factory=lambda *_: _FakeClient(_FakeResponse(payload)),
    )


@pytest.mark.parametrize("provider", ["gemini"])
@pytest.mark.parametrize("review_status", [
    "approved_as_extracted", "approved_corrected", "corrected", "rejected", "acknowledged_blank", "finalized",
])
def test_provider_cannot_supply_terminal_review_decisions(provider, review_status):
    payload = _provider_payload()
    payload["logical_forms"][0]["fields"]["israeli_id"]["review_status"] = review_status
    with pytest.raises(InvalidProviderResponse):
        adapter_for(provider, payload).extract(_request(), _document())


@pytest.mark.parametrize("warning", ["needs_review", "needs_clarification"])
@pytest.mark.parametrize("status", ["present", "missing", "unreadable", "ambiguous", "not_processed"])
def test_provider_warnings_preserve_all_candidate_metadata_without_mutation(warning, status):
    value = FieldResult(
        value_status=status,
        values=[{"raw": "0007", "normalized": "0007"}, {"raw": "0007"}, {"raw": " 008/9)"}]
        if status == "present" else [],
        review_status=warning,
        notes="Synthetic identifier role requires inspection",
        signals={"confidence": 0.99, "uncertain_role": True},
        evidence=[{"page_index": 0, "source_kind": "sticker", "text_snippet": "Synthetic only",
                   "bounding_box": {"x": 10, "y": 20, "width": 30, "height": 40}}],
    )
    fields = ExtractionFields(central_numbers=value)
    before = fields.model_dump(mode="json")
    result = sanitize_fields(fields, ["central_numbers"])
    assert result.central_numbers.review_status == warning
    assert result.central_numbers.model_dump(mode="json") == before["central_numbers"]
    assert fields.model_dump(mode="json") == before


@pytest.mark.parametrize("warning", ["omitted", None, "unreviewed"])
@pytest.mark.parametrize("status", ["present", "missing", "unreadable", "ambiguous", "not_processed"])
def test_without_provider_warning_existing_defaults_are_unchanged(warning, status):
    payload = _provider_payload()
    candidate = {"value_status": status, "values": [{"raw": "000001"}] if status == "present" else []}
    if warning != "omitted":
        candidate["review_status"] = warning
    payload["logical_forms"][0]["fields"]["central_numbers"] = candidate
    result = adapter_for("gemini", payload).extract(_request(), _document())
    field = result.logical_forms[0].fields.central_numbers
    assert field.review_status == ("unreviewed" if status == "present" else "needs_review")
    assert [v.raw for v in field.values] == (["000001"] if status == "present" else [])


@pytest.mark.parametrize("provider", ["gemini"])
@pytest.mark.parametrize("invalid_case", ["empty", "duplicate", "missing_ids", "both_shapes"])
def test_ambiguous_form_identity_is_rejected_without_collapsing_rows(provider, invalid_case):
    payload = _provider_payload()
    form = payload["logical_forms"][0]
    if invalid_case == "empty":
        payload["logical_forms"] = []
    elif invalid_case == "duplicate":
        payload["logical_forms"].append(deepcopy(form))
    elif invalid_case == "missing_ids":
        form.pop("form_id")
        payload["logical_forms"].append(deepcopy(form))
    else:
        payload["fields"] = deepcopy(form["fields"])
    with pytest.raises(InvalidProviderResponse):
        adapter_for(provider, payload).extract(_request(), _document())


@pytest.mark.parametrize("provider", ["gemini"])
def test_distinct_forms_keep_their_own_identifiers_and_values(provider):
    payload = _provider_payload()
    second = deepcopy(payload["logical_forms"][0])
    second["form_id"] = "form-002"
    second["fields"]["israeli_id"]["values"] = [{"raw": "009999999"}]
    second["fields"]["central_numbers"]["values"] = [{"raw": "000003"}]
    payload["logical_forms"].append(second)
    result = adapter_for(provider, payload).extract(_request(), _document())
    assert [f.form_id for f in result.logical_forms] == ["form-001", "form-002"]
    assert [f.fields.israeli_id.values[0].raw for f in result.logical_forms] == [
        "001234567", "009999999"
    ]
    assert [v.raw for v in result.logical_forms[0].fields.central_numbers.values] == [
        "000001", "000002"
    ]


@pytest.mark.parametrize("provider", ["gemini"])
@pytest.mark.parametrize("shape", ["single_form", "fields"])
def test_single_form_without_provider_identifier_uses_request_identifier(provider, shape):
    payload = _provider_payload()
    payload["logical_forms"][0].pop("form_id")
    if shape == "fields":
        payload = {"fields": payload["logical_forms"][0]["fields"]}
    result = adapter_for(provider, payload).extract(_request(), _document())
    assert result.logical_forms[0].form_id == _request().logical_form_id


@pytest.mark.parametrize("provider", ["gemini"])
@pytest.mark.parametrize("status,values", [("present", []), ("missing", [{"raw": "001"}])])
def test_inconsistent_field_status_and_values_are_rejected(provider, status, values):
    payload = _provider_payload()
    payload["logical_forms"][0]["fields"]["israeli_id"] = {
        "value_status": status, "values": values,
    }
    with pytest.raises(InvalidProviderResponse):
        adapter_for(provider, payload).extract(_request(), _document())


def test_mock_development_evaluation_matches_gemini_baseline_without_mutating_truth():
    truth = load_json(SYNTHETIC_GROUND_TRUTH_PATH)
    original = deepcopy(truth)
    # Recorded synthetic values exercise transport/canonical parity, not model accuracy.
    payload = {"logical_forms": [{
        "form_id": truth["logical_forms"][0]["form_id"],
        "fields": {
            name: {"value_status": "present", "values": deepcopy(field["values"])}
            for name, field in truth["logical_forms"][0]["fields"].items()
        },
    }]}
    result = adapter_for("gemini", deepcopy(payload)).extract(_request(), _document())
    report = score_extraction(truth, result.model_dump(mode="json", exclude_none=True))
    assert report["overall"]["exact_matches"] == 7
    assert report["fields"]["central_numbers"]["recall"] == 1.0
    assert report["fields"]["commitment_numbers"]["recall"] == 1.0
    assert report["silent_errors"]["silently_accepted_errors"] == 0
    assert truth == original
