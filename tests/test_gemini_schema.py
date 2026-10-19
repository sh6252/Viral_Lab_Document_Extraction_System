"""Synthetic generation-schema checks; no provider clients or network access."""

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from typing import get_args

from google.genai import models, types
from jsonschema import Draft202012Validator
import pytest

from backend.domain.models import ExtractionValueStatus, RequiredFieldName
from backend.services.extraction.canonical import ProviderPayload
from backend.services.extraction.gemini_schema import (
    GEMINI_RESPONSE_SCHEMA_VERSION,
    build_gemini_response_schema,
    gemini_response_schema_sha256,
)


@pytest.fixture
def validator() -> Draft202012Validator:
    schema = build_gemini_response_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def payload(field: dict, name: str = "israeli_id") -> dict:
    return {"logical_forms": [{"fields": {name: field}}]}


def test_schema_version_and_canonical_sha256_are_reproducible() -> None:
    schema = build_gemini_response_schema()
    encoded = json.dumps(
        schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    assert GEMINI_RESPONSE_SCHEMA_VERSION == "gemini-provider-output-v1"
    assert gemini_response_schema_sha256() == hashlib.sha256(encoded).hexdigest()
    # Contract/dependency drift must be reviewed and versioned, not hidden.
    assert gemini_response_schema_sha256() == "cd7ae6b8238a806924bb134a5ad6c2fe6306fbf59883432532ab4b9faf533664"


def test_schema_retains_required_contract_keys_and_optional_field_properties() -> None:
    original = ProviderPayload.model_json_schema()
    schema = build_gemini_response_schema()
    assert schema["required"] == ["logical_forms"]
    assert schema["properties"]["logical_forms"]["minItems"] == 1
    assert "maxItems" not in schema["properties"]["logical_forms"]
    assert set(schema["properties"]) == {"logical_forms"}
    for name, definition in schema["$defs"].items():
        assert definition.get("required") == original["$defs"][name].get("required")
        assert definition.get("additionalProperties") == original["$defs"][name].get("additionalProperties")
    fields = schema["$defs"]["ExtractionFields"]
    assert not fields.get("required")
    assert set(fields["properties"]) == set(get_args(RequiredFieldName))
    assert schema["$defs"]["FieldResult"]["required"] == ["value_status", "values"]
    assert schema["$defs"]["FieldValue"]["required"] == ["raw"]
    assert schema["$defs"]["FieldValue"]["properties"]["raw"]["type"] == "string"
    assert ProviderPayload.model_json_schema() == original


def test_builder_returns_independent_dictionaries_without_generation_defaults() -> None:
    first = build_gemini_response_schema()
    original = deepcopy(first)
    first["$defs"]["FieldResult"]["required"].clear()
    assert build_gemini_response_schema() == original

    def check(node: dict) -> None:
        assert not ({"default", "title", "minLength"} & node.keys())
        for key in ("properties", "$defs"):
            for child in node.get(key, {}).values():
                check(child)
        if isinstance(node.get("items"), dict):
            check(node["items"])
        for child in node.get("anyOf", []):
            check(child)
    check(original)


@pytest.mark.parametrize("name", get_args(RequiredFieldName))
@pytest.mark.parametrize("status", get_args(ExtractionValueStatus))
def test_required_values_apply_to_every_returned_field_and_status(validator, name, status) -> None:
    candidate = {"value_status": status, "values": [{"raw": "0007"}] if status == "present" else []}
    validator.validate(payload(candidate, name))
    del candidate["values"]
    assert not validator.is_valid(payload(candidate, name))
    candidate["values"] = None
    assert not validator.is_valid(payload(candidate, name))


@pytest.mark.parametrize("bad_value", [{}, {"raw": 123}, {"raw": None}, {"raw": "001", "extra": True}])
def test_value_elements_require_raw_string_and_reject_extra_properties(validator, bad_value) -> None:
    assert not validator.is_valid(payload({"value_status": "present", "values": [bad_value]}))


def test_optional_nulls_metadata_and_arbitrary_signals_remain_supported(validator) -> None:
    candidate = payload({
        "value_status": "ambiguous", "values": [], "notes": None,
        "review_status": None,
        "signals": {"title": "synthetic", "default": None, "arbitrary": [1, True, {"nested": "עברית"}]},
        "evidence": [{"page_index": 0, "source_kind": "vision_model", "notes": "synthetic",
                      "text_snippet": None, "bounding_box": {"x": 1, "y": 2, "width": 3, "height": 4}}],
    })
    candidate["logical_forms"][0]["fields"]["sender"] = None
    validator.validate(candidate)
    ProviderPayload.model_validate(candidate)
    validator.validate({"logical_forms": [{"fields": {}}]})


@pytest.mark.parametrize("status", ["unreviewed", "needs_review", "needs_clarification"])
def test_generation_allows_provider_warning_metadata(validator, status) -> None:
    validator.validate(payload({"value_status": "missing", "values": [], "review_status": status}))


@pytest.mark.parametrize("status", [
    "approved_as_extracted", "approved_corrected", "corrected", "rejected", "acknowledged_blank", "finalized",
])
def test_generation_never_requests_human_review_decisions(validator, status) -> None:
    assert not validator.is_valid(payload({"value_status": "missing", "values": [], "review_status": status}))


@pytest.mark.parametrize("candidate", [
    {}, {"logical_forms": None}, {"logical_forms": []},
    {"logical_forms": [{"fields": {"unexpected": {"value_status": "missing", "values": []}}}]},
    {"logical_forms": [{"fields": {}, "extra": True}]},
    {"logical_forms": [{"fields": {}}], "extra": True},
])
def test_generation_envelope_and_closed_objects(validator, candidate) -> None:
    assert not validator.is_valid(candidate)


def test_sdk_serialization_preserves_the_json_schema() -> None:
    schema = build_gemini_response_schema()
    config = types.GenerateContentConfig(
        response_mime_type="application/json", response_json_schema=schema, temperature=0,
    )
    # Pure SDK conversion: no SDK client, credential, HTTP transport or API call.
    wire = models._GenerateContentConfig_to_mldev(
        SimpleNamespace(vertexai=False), config.model_dump(exclude_none=True),
    )
    assert wire["responseJsonSchema"] == schema
    assert "responseSchema" not in wire
    assert wire["responseMimeType"] == "application/json"
    assert wire["temperature"] == 0
