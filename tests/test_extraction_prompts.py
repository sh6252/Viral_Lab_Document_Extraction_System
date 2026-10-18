"""Offline prompt contract checks; fake responses do not measure model accuracy."""

from __future__ import annotations

import json
import re

import pytest

from backend.services.extraction.gemini_adapter import GeminiExtractionAdapter
from backend.services.extraction.prompts import (
    EXTRACTION_PROMPT_VERSION,
    GEMINI_EXTRACTION_PROMPT_VERSION,
    build_extraction_prompt,
    build_gemini_extraction_prompt,
)
from backend.services.extraction.schema_validation import validate_extraction_result_schema
from tests.test_gemini_adapter import (
    _FakeClient,
    _FakeResponse,
    _authorized_config,
    _document,
    _provider_payload,
    _request,
)


def rendered_prompt() -> str:
    return build_gemini_extraction_prompt(_request().fields, "he-IL")


def test_prompt_version_is_explicit_and_shared_with_gemini() -> None:
    assert EXTRACTION_PROMPT_VERSION == "he-commitment-v2-identifiers"
    assert GEMINI_EXTRACTION_PROMPT_VERSION == EXTRACTION_PROMPT_VERSION
    assert f"Prompt version: {EXTRACTION_PROMPT_VERSION}\n" in rendered_prompt()
    assert rendered_prompt() == build_extraction_prompt(_request().fields, "he-IL")


def test_locale_and_requested_field_selection_are_preserved() -> None:
    prompt = build_gemini_extraction_prompt(["central_numbers"], "en-US")
    assert "Locale: en-US\n" in prompt
    assert "Extract only these requested fields: central_numbers.\n" in prompt


def test_json_example_preserves_the_existing_seven_field_contract() -> None:
    example = rendered_prompt().split("Return only valid JSON with this shape:\n", 1)[1]
    example = example.split("\n\nExtract only these requested fields:", 1)[0]
    statuses = "present|missing|unreadable|ambiguous|not_processed"
    fields = {
        name: {"value_status": statuses, "values": [{"raw": "string"}]}
        for name in _request().fields
    }
    fields["first_name"] = {
        "value_status": statuses,
        "values": [{"raw": "string", "normalized": "string"}],
        "notes": "string",
    }
    fields["sample_receipt_date"]["values"] = [{"raw": "YYYY-MM-DD"}]
    assert json.loads(example) == {"logical_forms": [{"form_id": "string", "fields": fields}]}


def test_identifier_definitions_distinguish_lab_and_financial_context() -> None:
    prompt = rendered_prompt()
    central = prompt.split("- central_numbers ", 1)[1].split("\n", 1)[0]
    commitment = prompt.split("- commitment_numbers ", 1)[1].split("\n", 1)[0]
    assert "laboratory sample/accession identifiers" in central
    assert '"דגימה"' in central and "surrounding context" in central
    assert "financial commitment/authorization identifiers" in commitment
    for label in ("מספר התחייבות", "התחייבות", "דרישה"):
        assert f'"{label}"' in commitment
    assert "only in a financial authorization context" in commitment
    assert "a laboratory test request alone does not establish it" in commitment
    assert "nearby labels, stickers, stamps, and surrounding text" in prompt


def test_visual_heuristics_alone_cannot_assign_identifier_roles() -> None:
    prompt = rendered_prompt()
    rule = next(line for line in prompt.splitlines() if line.startswith("- Barcode presence"))
    for cue in ("Barcode presence", "numeric length", "prominence", "page position"):
        assert cue in rule
    assert "alone are insufficient" in rule
    assert "Do not substitute a commitment number for a central number" in prompt
    assert "Do not copy an identifier into another field without supporting context" in prompt


def test_unsupported_role_requires_ambiguous_empty_values_and_notes() -> None:
    rule = next(line for line in rendered_prompt().splitlines() if "role cannot be determined" in line)
    assert "do not assign it to an unsupported field" in rule
    assert 'value_status "ambiguous", an empty values array' in rule
    assert 'an explanatory note in "notes"' in rule
    assert "Keep supported values in fields whose roles are established" in rule


def test_prompt_retains_raw_multi_values_and_other_field_instructions() -> None:
    prompt = rendered_prompt()
    for instruction in (
        "Preserve raw strings exactly, including Hebrew text, punctuation, and leading zeros.",
        "Do not convert identifiers to numbers.",
        "Do not guess missing, unreadable, or ambiguous values.",
        'Use "missing" only when the field is expected but no value is found.',
        'Use "unreadable" when the value exists but cannot be read.',
        'Use "ambiguous" when multiple interpretations are possible.',
        "Preserve all supported multiple values in each field, including leading zeros",
        "Do not approve fields or records. Do not include review approvals.",
        "Do not include explanations outside the JSON object.",
    ):
        assert instruction in prompt
    # No document-specific numeric examples or fixture names belong in the prompt.
    assert re.search(r"\d{3,}", prompt) is None
    assert re.search(r"\b[A-C][1-5]\b", prompt) is None


def test_adapter_sends_the_new_prompt_without_changing_transport_or_business_values() -> None:
    payload = _provider_payload()
    fields = payload["logical_forms"][0]["fields"]
    fields["central_numbers"]["values"] = [{"raw": "000071-A"}, {"raw": "000072/B"}]
    fields["commitment_numbers"]["values"] = [{"raw": "AUTH-0008"}, {"raw": "AUTH-0009"}]
    fake = _FakeClient(_FakeResponse(payload))
    timeouts: list[int] = []

    def factory(api_key: str, timeout: int) -> _FakeClient:
        timeouts.append(timeout)
        return fake

    adapter = GeminiExtractionAdapter(config=_authorized_config(), client_factory=factory)
    result = adapter.extract(_request(), _document())
    validate_extraction_result_schema(result)
    assert timeouts == [60]
    assert adapter._max_retries == 1
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["model"] == "gemini-test-model"
    assert call["config"].temperature == 0
    assert call["config"].response_mime_type == "application/json"
    assert call["contents"][0].inline_data.data == _document().content
    assert call["contents"][1] == rendered_prompt()
    for name, expected in fields.items():
        actual = getattr(result.logical_forms[0].fields, name)
        assert actual.value_status == expected["value_status"]
        assert [value.model_dump(exclude_none=True) for value in actual.values] == expected["values"]
        assert actual.notes == expected.get("notes")


@pytest.mark.parametrize("field_name", ["central_numbers", "commitment_numbers"])
def test_ambiguous_role_response_uses_existing_schema_and_requires_review(field_name: str) -> None:
    payload = _provider_payload()
    fields = payload["logical_forms"][0]["fields"]
    fields[field_name] = {
        "value_status": "ambiguous",
        "values": [],
        "notes": "Synthetic label does not establish the identifier role.",
    }
    fake = _FakeClient(_FakeResponse(payload))
    adapter = GeminiExtractionAdapter(
        config=_authorized_config(), client_factory=lambda *_: fake,
    )
    result = adapter.extract(_request(), _document())
    validate_extraction_result_schema(result)
    actual = getattr(result.logical_forms[0].fields, field_name)
    assert actual.value_status == "ambiguous" and actual.values == []
    assert actual.notes == fields[field_name]["notes"]
    assert actual.review_status == "needs_review"
    other = "commitment_numbers" if field_name == "central_numbers" else "central_numbers"
    assert [value.raw for value in getattr(result.logical_forms[0].fields, other).values] == [
        value["raw"] for value in fields[other]["values"]
    ]
