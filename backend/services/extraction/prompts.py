"""Versioned prompts for provider adapters."""

from __future__ import annotations

from backend.domain.models import RequiredFieldName

EXTRACTION_PROMPT_VERSION = "he-commitment-v1"
GEMINI_EXTRACTION_PROMPT_VERSION = EXTRACTION_PROMPT_VERSION


def build_extraction_prompt(fields: list[RequiredFieldName], locale: str) -> str:
    field_list = ", ".join(fields)
    return f"""
You extract structured fields from Hebrew medical commitment forms for the Central Virology Laboratory.
Locale: {locale}
Prompt version: {EXTRACTION_PROMPT_VERSION}

Return only valid JSON with this shape:
{{
  "logical_forms": [
    {{
      "form_id": "string",
      "fields": {{
        "first_name": {{"value_status": "present|missing|unreadable|ambiguous|not_processed", "values": [{{"raw": "string", "normalized": "string"}}], "notes": "string"}},
        "last_name": {{"value_status": "present|missing|unreadable|ambiguous|not_processed", "values": [{{"raw": "string"}}]}},
        "israeli_id": {{"value_status": "present|missing|unreadable|ambiguous|not_processed", "values": [{{"raw": "string"}}]}},
        "central_numbers": {{"value_status": "present|missing|unreadable|ambiguous|not_processed", "values": [{{"raw": "string"}}]}},
        "sample_receipt_date": {{"value_status": "present|missing|unreadable|ambiguous|not_processed", "values": [{{"raw": "YYYY-MM-DD"}}]}},
        "sender": {{"value_status": "present|missing|unreadable|ambiguous|not_processed", "values": [{{"raw": "string"}}]}},
        "commitment_numbers": {{"value_status": "present|missing|unreadable|ambiguous|not_processed", "values": [{{"raw": "string"}}]}}
      }}
    }}
  ]
}}

Extract only these requested fields: {field_list}.
Preserve raw strings exactly, including Hebrew text, punctuation, and leading zeros.
Do not convert identifiers to numbers.
Do not guess missing, unreadable, or ambiguous values.
Use "missing" only when the field is expected but no value is found.
Use "unreadable" when the value exists but cannot be read.
Use "ambiguous" when multiple interpretations are possible.
Do not approve fields or records. Do not include review approvals.
Do not include explanations outside the JSON object.
""".strip()


def build_gemini_extraction_prompt(fields: list[RequiredFieldName], locale: str) -> str:
    return build_extraction_prompt(fields, locale)
