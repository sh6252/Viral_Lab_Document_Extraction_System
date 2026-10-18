"""Versioned prompts for provider adapters."""

from __future__ import annotations

from backend.domain.models import RequiredFieldName

EXTRACTION_PROMPT_VERSION = "he-commitment-v2-identifiers"
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

Distinguish the requested identifier fields by their meaning:
- central_numbers are laboratory sample/accession identifiers. Labels such as "מספר מרכזי", "מספר מעבדה", or "דגימה" support this role when the surrounding context identifies a laboratory sample/accession.
- commitment_numbers are financial commitment/authorization identifiers for payment or coverage. Labels such as "מספר התחייבות" or "התחייבות" support this role. "דרישה" supports this role only in a financial authorization context; a laboratory test request alone does not establish it.
- Use nearby labels, stickers, stamps, and surrounding text to determine what each identifier refers to. A sticker or stamp alone does not establish its role.
- Barcode presence, numeric length, prominence, or page position alone are insufficient to determine an identifier's role.
- Do not substitute a commitment number for a central number, or a central number for a commitment number. Do not copy an identifier into another field without supporting context.
- Preserve all supported multiple values in each field, including leading zeros, punctuation, and raw formatting. Do not select only the most prominent identifier.
- If an identifier's role cannot be determined, do not assign it to an unsupported field. Return the unresolved field with value_status "ambiguous", an empty values array, and an explanatory note in "notes". Keep supported values in fields whose roles are established.

Do not approve fields or records. Do not include review approvals.
Do not include explanations outside the JSON object.
""".strip()


def build_gemini_extraction_prompt(fields: list[RequiredFieldName], locale: str) -> str:
    return build_extraction_prompt(fields, locale)
