"""Validation helpers for canonical extraction results."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from backend.domain import ExtractionResult
from backend.services.extraction.errors import ExtractionValidationError


_SCHEMA_PATH = (
    Path(__file__).resolve().parents[3]
    / "evaluation"
    / "schemas"
    / "extraction_result.schema.json"
)


def validate_extraction_result_schema(result: ExtractionResult) -> None:
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    payload = result.model_dump(mode="json", exclude_none=True)
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda item: item.path)
    if errors:
        raise ExtractionValidationError("Canonical extraction result failed schema validation.")
