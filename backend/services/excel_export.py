"""Literal-text XLSX export of reviewed records, with separate processing failures."""

from __future__ import annotations

import io
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from collections.abc import Iterable

import xlsxwriter

from backend.services.business_review import (
    FIELDS, MULTI_FIELDS, BusinessReviewResult, validate_review_result,
)

FORM_COLUMNS = FIELDS + (
    "document_id", "form_id", "extraction_id", "source_sha256", "data_origin", "record_review_status",
    "unresolved_fields", "relationship_status", "provider", "model", "prompt_version", "pipeline_version",
    "review_revision",
) + tuple(f"{name}_{suffix}" for name in FIELDS for suffix in ("value_status", "review_status", "value_source"))
FAILURE_COLUMNS = ("document_id", "attempt_reference", "source_sha256", "provider", "model", "failure_code",
                   "retryable", "attempt_count", "latency_seconds", "data_origin")
AUDIT_COLUMNS = (
    "document_id", "source_sha256", "canonical_snapshot_sha256", "extraction_id", "form_id", "field_name",
    "original_raw_values", "original_normalized_values", "original_value_status", "original_review_status",
    "decision_id", "action", "reviewed_values", "operator_declaration", "declared_timestamp", "reason",
    "old_review_state", "new_review_state", "previous_revision", "revision",
)


class ExcelExportError(ValueError):
    """Safe export reason, never the rejected cell content."""


@dataclass(frozen=True, slots=True)
class ProcessingFailure:
    document_id: str
    attempt_reference: str
    failure_code: str
    data_origin: str
    source_sha256: str | None = None
    provider: str | None = None
    model: str | None = None
    retryable: bool | None = None
    attempt_count: int | None = None
    latency_seconds: float | None = None

    def __post_init__(self) -> None:
        if (not all(isinstance(v, str) and v.strip() for v in (self.document_id, self.attempt_reference))
                or not isinstance(self.failure_code, str)
                or re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", self.failure_code) is None
                or self.data_origin not in {"live", "historical_replay"}
                or (self.source_sha256 is not None and (not isinstance(self.source_sha256, str)
                    or re.fullmatch(r"[0-9a-f]{64}", self.source_sha256) is None))
                or any(v is not None and not isinstance(v, str) for v in (self.provider, self.model))
                or (self.retryable is not None and type(self.retryable) is not bool)
                or (self.attempt_count is not None and (type(self.attempt_count) is not int or self.attempt_count < 1))
                or (self.latency_seconds is not None and (type(self.latency_seconds) not in {float, int}
                    or not math.isfinite(self.latency_seconds) or self.latency_seconds < 0))):
            raise ExcelExportError("INVALID_PROCESSING_FAILURE")


def _array(values: object) -> str:
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _cell(value: object) -> str:
    text = "" if value is None else str(value)
    try:
        # Conservative UTF-16 Excel bound; no writer truncation, XML-invalid controls
        # or unpaired surrogates. Tabs/newlines/CR and formula-like text are literal.
        if len(text.encode("utf-16-le")) // 2 > 32767:
            raise ExcelExportError("EXCEL_CELL_TOO_LONG")
        if any((ord(c) < 32 and c not in "\t\n\r") or ord(c) in {0xFFFE, 0xFFFF} for c in text):
            raise ExcelExportError("UNSUPPORTED_EXCEL_TEXT")
    except UnicodeError:
        raise ExcelExportError("UNSUPPORTED_EXCEL_TEXT") from None
    return text


def export_workbook(results: Iterable[BusinessReviewResult], failures: Iterable[ProcessingFailure] = ()) -> bytes:
    """No file I/O. Exactly all forms of each supplied bound extraction, once each."""
    results, failures = tuple(results), tuple(failures)
    if not results and not failures:
        raise ExcelExportError("NO_RECORDS_OR_FAILURES")
    groups: dict[tuple[str, str], list[BusinessReviewResult]] = {}
    for result in results:
        validate_review_result(result)
        binding = result.record.binding
        groups.setdefault((binding.document_id, binding.extraction_id), []).append(result)
    for group in groups.values():
        first = group[0]
        if any(r.record.binding != first.record.binding or r.canonical_snapshot_json != first.canonical_snapshot_json
               for r in group):
            raise ExcelExportError("EXTRACTION_BINDING_CONFLICT")
        expected = [f["form_id"] for f in json.loads(first.canonical_snapshot_json)["logical_forms"]]
        actual = [r.record.form_id for r in group]
        if len(actual) != len(set(actual)) or set(expected) != set(actual):
            raise ExcelExportError("ALL_LOGICAL_FORMS_REQUIRED_ONCE")
    if not all(isinstance(f, ProcessingFailure) for f in failures):
        raise ExcelExportError("INVALID_PROCESSING_FAILURE")
    failure_ids = [(f.document_id, f.attempt_reference) for f in failures]
    if len(failure_ids) != len(set(failure_ids)):
        raise ExcelExportError("DUPLICATE_PROCESSING_FAILURE")
    form_rows, audit_rows = [], []
    for result in results:
        record, binding = result.record, result.record.binding
        fields = record.fields
        business = [(_array(f.reviewed_values) if f.field_name in MULTI_FIELDS else
                     (f.reviewed_values[0] if f.reviewed_values else "")) for f in fields]
        form_rows.append(business + [binding.document_id, record.form_id, binding.extraction_id,
            binding.source_sha256, binding.data_origin, record.review_status, _array(record.unresolved_fields),
            binding.relationship_status, record.provider, record.model, binding.prompt_version,
            binding.pipeline_version, record.revision] +
            [v for f in fields for v in (f.value_status, f.review_status, f.value_source)])
        for entry in result.audit:
            action = entry.action
            audit_rows.append([binding.document_id, action.source_sha256, action.canonical_snapshot_sha256,
                action.extraction_id, action.form_id, action.field_name,
                _array([v.raw for v in action.original.values]), _array([v.normalized for v in action.original.values]),
                action.original.value_status, action.original.review_status, action.decision_id, action.action,
                _array(action.reviewed_values), action.operator_declaration, action.declared_at.isoformat(),
                action.reason, entry.old_review_state, entry.new_review_state, entry.previous_revision, entry.revision])
    tables = [("Forms", FORM_COLUMNS, form_rows),
              ("ProcessingFailures", FAILURE_COLUMNS, [[getattr(f, c) for c in FAILURE_COLUMNS] for f in failures]),
              ("ReviewAudit", AUDIT_COLUMNS, audit_rows)]
    # Validate everything before constructing the workbook, including serialized arrays.
    checked = []
    for name, columns, rows in tables:
        if len(rows) > 1048575:
            raise ExcelExportError("EXCEL_ROW_LIMIT")
        checked.append((name, [list(columns)] + [[_cell(v) for v in row] for row in rows]))
    output = io.BytesIO()
    with xlsxwriter.Workbook(output, {"in_memory": True, "strings_to_formulas": False,
                                    "strings_to_urls": False, "strings_to_numbers": False}) as workbook:
        workbook.set_properties({"created": datetime(2000, 1, 1, tzinfo=timezone.utc)})
        literal = workbook.add_format({"num_format": "@"})
        for name, rows in checked:
            sheet = workbook.add_worksheet(name)
            sheet.freeze_panes(1, 0)
            for row_index, row in enumerate(rows):
                for col_index, value in enumerate(row):
                    # write_string, never generic write(): even '=...' stays text.
                    if sheet.write_string(row_index, col_index, value, literal) != 0:
                        raise ExcelExportError("EXCEL_WRITE_FAILED")
    return output.getvalue()
