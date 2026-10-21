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
    FIELDS, BusinessReviewResult, FormApprovalAuditEntry, validate_review_result,
)

FORM_COLUMNS = FIELDS + (
    "document_id", "form_id", "extraction_id", "source_sha256", "data_origin", "record_review_status",
    "unresolved_fields", "relationship_status", "provider", "model", "prompt_version", "pipeline_version",
    "review_revision",
) + tuple(f"{name}_{suffix}" for name in FIELDS for suffix in ("value_status", "review_status", "value_source")) + (
    "form_finalized", "form_approval_basis",
)
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


def export_workbook(results: Iterable[BusinessReviewResult], failures: Iterable[ProcessingFailure] = (), *,
                    selected_rows: Iterable[tuple[str, str, str | None]] | None = None) -> bytes:
    """Validate every form; export one row per exact distinct reviewed commitment.

    Only derived rows are deduplicated, in first-occurrence order. Raw values,
    normalized values and complete review history remain untouched. No commitment
    produces one blank row with actual review statuses, never a new approval.
    """
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
    selected = None if selected_rows is None else set(selected_rows)
    available = set()
    for result in results:
        field = result.record.fields[FIELDS.index("commitment_numbers")]
        values = tuple(v.raw for v in field.original.values) if field.review_status == "unreviewed" else field.reviewed_values
        available.update((result.record.binding.document_id, result.record.form_id, value) for value in (tuple(dict.fromkeys(values)) or (None,)))
    if selected is not None and (not selected <= available or (not selected and not failures)):
        raise ExcelExportError("INVALID_EXPORT_SELECTION")
    form_rows, audit_rows = [], []
    for result in results:
        record, binding = result.record, result.record.binding
        if selected is not None and not any(doc == binding.document_id and form == record.form_id for doc, form, _ in selected):
            continue
        fields = record.fields
        business = [(_array(f.reviewed_values) if f.field_name == "central_numbers" else
                     (f.reviewed_values[0] if f.reviewed_values else "")) for f in fields]
        metadata = [binding.document_id, record.form_id, binding.extraction_id,
            binding.source_sha256, binding.data_origin, record.review_status, _array(record.unresolved_fields),
            binding.relationship_status, record.provider, record.model, binding.prompt_version,
            binding.pipeline_version, record.revision] + [
                v for f in fields for v in (f.value_status, f.review_status, f.value_source)]
        metadata += [str(result.finalized).lower(), "human_form_attestation" if result.finalized else ""]
        commitment_index = FIELDS.index("commitment_numbers")
        # Exact equality only: never trim, convert to numbers or deduplicate across
        # forms. Different raw formatting may carry information and is retained.
        commitments = tuple(dict.fromkeys(fields[commitment_index].reviewed_values)) or ("",)
        if selected is not None:
            field = fields[commitment_index]
            values = tuple(v.raw for v in field.original.values) if field.review_status == "unreviewed" else field.reviewed_values
            commitments = tuple(v for v in (tuple(dict.fromkeys(values)) or (None,))
                if (binding.document_id, record.form_id, v) in selected)
        if len(form_rows) + len(commitments) > 1048575:
            raise ExcelExportError("EXCEL_ROW_LIMIT")
        for commitment in commitments:
            row = list(business)
            row[commitment_index] = commitment if fields[commitment_index].review_status in {"approved_provider", "approved_corrected", "accepted_by_form"} else ""
            form_rows.append(row + metadata + ([commitment] if selected is not None else []))
        for entry in result.audit:
            action = entry.action
            if isinstance(entry, FormApprovalAuditEntry):
                # One record-level action, never seven fabricated field decisions.
                audit_rows.append([binding.document_id, action.source_sha256, action.canonical_snapshot_sha256,
                    action.extraction_id, action.form_id, action.field_name,
                    "", "", "form_snapshot", "not_applicable", action.decision_id, action.action,
                    "", action.operator_declaration, action.declared_at.isoformat(),
                    action.policy_version, "not_finalized", "finalized", entry.previous_revision, entry.revision])
                continue
            audit_rows.append([binding.document_id, action.source_sha256, action.canonical_snapshot_sha256,
                action.extraction_id, action.form_id, action.field_name,
                _array([v.raw for v in action.original.values]), _array([v.normalized for v in action.original.values]),
                action.original.value_status, action.original.review_status, action.decision_id, action.action,
                _array(action.reviewed_values), action.operator_declaration, action.declared_at.isoformat(),
                action.reason, entry.old_review_state, entry.new_review_state, entry.previous_revision, entry.revision])
    tables = [("Forms", FORM_COLUMNS + (("export_selected_commitment",) if selected is not None else ()), form_rows),
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
