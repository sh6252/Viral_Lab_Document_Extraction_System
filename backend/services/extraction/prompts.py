"""Versioned prompts for provider adapters."""

from __future__ import annotations

from backend.domain.models import RequiredFieldName

EXTRACTION_PROMPT_VERSION = "he-commitment-v3-lab-commitments"
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

Every document received with a laboratory sample is a commitment document. This does not establish the role of any particular number or imply that a commitment number is readable or present.
Distinguish the requested identifier fields by their meaning:
- central_numbers are internal laboratory sample/accession identifiers. A sticker marked "מרכזי" or a handwritten "מספר מרכזי" supports this role. "מספר מעבדה" requires surrounding context identifying the internal laboratory sample, not an examination/test code. Preserve all relevant central numbers.
- commitment_numbers are commitment/authorization identifiers, not laboratory examination/test numbers. Labels such as "מספר התחייבות", "התחייבות", or "דרישה" can identify them when the surrounding document context establishes that role; a laboratory test request alone does not establish it.
- On Leumit ("לאומית") forms specifically, "דגימה" can label a commitment number when the Leumit form context establishes that role. Do not globally treat every "דגימה" number as a commitment. On other forms or when the Leumit context/role is uncertain, the label alone is insufficient; preserve uncertainty rather than assigning an unsupported role.
- Examination/test codes are not commitment numbers. Neither a central number nor a test code can substitute for a missing commitment number. A document being a commitment document does not make every number a commitment number.
- sample_receipt_date is the laboratory receipt date written below the central-number label/sticker or at the relevant "התקבל" stamp. Do not substitute an unrelated document issue date, authorization date, or examination date. If competing receipt dates cannot be resolved by this context, return an ambiguous empty field with notes rather than guessing.
- Use nearby labels, stickers, stamps, and surrounding text to determine what each identifier refers to. A sticker or stamp alone does not establish its role.
- Barcode presence, numeric length, prominence, or page position alone are insufficient to determine an identifier's role.
- Do not substitute a commitment number for a central number, or a central number for a commitment number. Do not copy an identifier into another field without supporting context.
- Preserve all supported multiple values in each field, including leading zeros, punctuation, and raw formatting. Do not select only the most prominent identifier.
- Keep every logical form and its complete commitment_numbers list together. Do not split a logical form into output rows per commitment during extraction, infer commitment/central pairings, or generate a Cartesian product. Row expansion belongs to the reviewed Excel export only.
- If an identifier's role cannot be determined, do not assign it to an unsupported field. Return the unresolved field with value_status "ambiguous", an empty values array, and an explanatory note in "notes". Keep supported values in fields whose roles are established.

Do not approve fields or records. Do not include review approvals.
Do not include explanations outside the JSON object.
""".strip()


def build_gemini_extraction_prompt(fields: list[RequiredFieldName], locale: str) -> str:
    return build_extraction_prompt(fields, locale)
