"""Reopen actual XLSX ZIP/XML using only the standard library, no second engine."""

from dataclasses import replace
import io
import json
import re
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import pytest

from backend.services.business_review import FIELDS, apply_business_review
from backend.services.excel_export import (
    AUDIT_COLUMNS, FAILURE_COLUMNS, FORM_COLUMNS, ExcelExportError, ProcessingFailure, export_workbook,
)
from tests.test_business_review import action, binding, canonical, complete, reviewed

NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def read_workbook(data):
    """Inspect serialized cell types AND reconstruct exact escaped shared strings."""
    with ZipFile(io.BytesIO(data)) as archive:
        assert archive.testzip() is None
        for name in archive.namelist():
            if name.endswith(".xml"):
                ET.fromstring(archive.read(name))
        root = ET.fromstring(archive.read("xl/workbook.xml"))
        names = [s.attrib["name"] for s in root.findall("x:sheets/x:sheet", NS)]
        shared = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        # _x005F_xHHHH_ protects a literal escape, including overlapping underscores.
        strings = [re.sub(r"_x005[Ff]_(x[0-9a-fA-F]{4}_)|_x([0-9a-fA-F]{4})_",
                          lambda m: "_" + m[1] if m[1] else chr(int(m[2], 16)),
                          "".join(si.itertext())) for si in shared.findall("x:si", NS)]
        result = {}
        for i, name in enumerate(names, start=1):
            sheet = ET.fromstring(archive.read(f"xl/worksheets/sheet{i}.xml"))
            assert not sheet.findall(".//x:f", NS), "formula created"
            assert not sheet.findall(".//x:hyperlink", NS), "hyperlink created"
            rows = []
            for row in sheet.findall("x:sheetData/x:row", NS):
                values = []
                for cell in row.findall("x:c", NS):
                    assert cell.attrib["t"] == "s", "non-text cell created"
                    values.append(strings[int(cell.find("x:v", NS).text)])
                rows.append(values)
            result[name] = (tuple(rows[0]), [dict(zip(rows[0], row, strict=True)) for row in rows[1:]])
        assert not any("externallink" in n.lower() for n in archive.namelist())
        for name in archive.namelist():
            if name.endswith(".rels"):
                assert b'TargetMode="External"' not in archive.read(name)
        return result


def failure(**changes):
    data = dict(document_id="failed-document", attempt_reference="failed-attempt", failure_code="PROVIDER_TIMEOUT",
                data_origin="live", source_sha256="b"*64, provider="mock", model="synthetic-v1",
                retryable=True, attempt_count=3, latency_seconds=123.5)
    data.update(changes)
    return ProcessingFailure(**data)


def test_exact_sheets_headers_text_identifiers_dates_and_hebrew():
    book = read_workbook(export_workbook([complete()]))
    assert tuple(book) == ("Forms", "ProcessingFailures", "ReviewAudit")
    assert book["Forms"][0][:7] == FIELDS and book["Forms"][0] == FORM_COLUMNS
    assert book["ReviewAudit"][0] == AUDIT_COLUMNS and book["ProcessingFailures"][0] == FAILURE_COLUMNS
    row = book["Forms"][1][0]
    assert row["israeli_id"] == "000123" and row["sample_receipt_date"] == "01/02/2026"
    assert row["first_name"] == "טקסט בדיקה"
    assert json.loads(row["central_numbers"]) == ["0007", "008/9)", "0007"]
    assert json.loads(row["commitment_numbers"]) == ["00001", "00002"]
    assert row["review_revision"] == "7" and row["record_review_status"] == "complete"
    assert row["relationship_status"] == "unresolved"


def test_no_autoapproval_unresolved_blank_scalar_and_empty_arrays():
    row = read_workbook(export_workbook([reviewed()]))["Forms"][1][0]
    assert row["record_review_status"] == "unresolved"
    assert all(row[name] == "" for name in FIELDS if name not in {"central_numbers", "commitment_numbers"})
    assert row["central_numbers"] == row["commitment_numbers"] == "[]"
    assert row["first_name_review_status"] == "unreviewed" and row["first_name_value_source"] == "none"
    assert json.loads(row["unresolved_fields"]) == list(FIELDS)


def test_acknowledged_blank_distinct_from_unresolved_empty():
    result = reviewed(actions=[action(name="central_numbers", kind="acknowledge_blank")])
    row = read_workbook(export_workbook([result]))["Forms"][1][0]
    assert row["central_numbers"] == "[]" and row["central_numbers_review_status"] == "acknowledged_blank"
    assert row["central_numbers_value_source"] == "human_approved_blank"
    assert row["central_numbers_value_status"] == "blank"


@pytest.mark.parametrize("text", ["=SUM(1,2)", "+000123", "-000123", "@SUM(A1)", " \t=1+1", "\n=1+1",
    "\r=1+1", "https://example.invalid/private", "mailto:operator@example.invalid", "external:private.xlsx",
    "'000123", "  עברית /)  ", "_x000D_", "_x005F_x000D_"], ids=[
        "equals", "plus", "minus", "at", "whitespace", "newline", "carriage-return", "url", "mail",
        "external-path", "apostrophe", "hebrew", "literal-escape", "nested-literal-escape"])
def test_untrusted_values_and_notes_are_literal_exact_text_without_links(text):
    result = reviewed(actions=[action(values=(text,), operator_declaration=text, reason=text)])
    book = read_workbook(export_workbook([result], [failure(document_id=text, provider=text)]))
    assert book["Forms"][1][0]["first_name"] == text
    audit = book["ReviewAudit"][1][0]
    assert json.loads(audit["reviewed_values"]) == [text]
    assert audit["operator_declaration"] == audit["reason"] == text
    assert book["ProcessingFailures"][1][0]["document_id"] == text


def test_audit_contains_every_action_and_original_vs_corrected_exact_values():
    actions = [action(values=("תיקון",)), action(name="israeli_id", values=("000-9/",), revision=1, decision_id="second")]
    result = reviewed(actions=actions)
    book = read_workbook(export_workbook([result]))
    audit = book["ReviewAudit"][1]
    assert len(audit) == 2
    assert json.loads(audit[1]["original_raw_values"]) == ["000123"]
    assert json.loads(audit[1]["original_normalized_values"]) == ["123"]
    assert json.loads(audit[1]["reviewed_values"]) == ["000-9/"]
    assert audit[1]["revision"] == "2" and audit[0]["old_review_state"] == "unreviewed"
    assert "ocr" not in " ".join(book["Forms"][0] + book["ReviewAudit"][0]).lower()


def test_all_forms_once_no_cartesian_rows_and_no_cross_form_values():
    value = canonical(two_forms=True)
    source = binding(value)
    first = reviewed(value, [action(value, name="central_numbers", kind="approve_as_extracted")], source)
    second = apply_business_review(value, source, "form-2")
    rows = read_workbook(export_workbook([first, second]))["Forms"][1]
    assert len(rows) == 2 and [row["form_id"] for row in rows] == ["form-1", "form-2"]
    assert json.loads(rows[0]["central_numbers"]) == ["0007", "008/9)", "0007"]
    assert rows[1]["central_numbers"] == "[]"
    for incomplete in ([first], [first, first], [first, second, second]):
        with pytest.raises(ExcelExportError, match="ALL_LOGICAL_FORMS_REQUIRED_ONCE"):
            export_workbook(incomplete)


def test_failures_only_never_create_business_rows():
    book = read_workbook(export_workbook([], [failure()]))
    assert not book["Forms"][1] and not book["ReviewAudit"][1]
    assert len(book["ProcessingFailures"][1]) == 1
    assert book["ProcessingFailures"][1][0]["attempt_count"] == "3"
    with pytest.raises(ExcelExportError, match="NO_RECORDS_OR_FAILURES"):
        export_workbook([])


@pytest.mark.parametrize("text", ["x"*32768, "😀"*16384, "\x00PRIVATE", "\x01=PRIVATE", "\ud800", "\uffff"],
                         ids=["length", "utf16-length", "null", "control", "surrogate", "xml-invalid"])
def test_oversized_and_unsafe_text_fails_without_echo_or_truncation(text):
    with pytest.raises(ExcelExportError) as caught:
        export_workbook([reviewed(actions=[action(values=(text,))])])
    assert "PRIVATE" not in str(caught.value)


def test_serialized_array_and_audit_limits_are_checked_too():
    with pytest.raises(ExcelExportError, match="EXCEL_CELL_TOO_LONG"):
        export_workbook([reviewed(actions=[action(name="central_numbers", values=("x"*20000, "y"*20000))])])
    with pytest.raises(ExcelExportError, match="EXCEL_CELL_TOO_LONG"):
        export_workbook([reviewed(actions=[action(reason="x"*32768)])])


def test_near_limit_literal_cell_and_workbook_determinism():
    text = "א"*32000
    result = reviewed(actions=[action(values=(text,))])
    first = export_workbook([result])
    assert first == export_workbook([result])
    assert read_workbook(first)["Forms"][1][0]["first_name"] == text


@pytest.mark.parametrize("changes", [{"failure_code": "PRIVATE bad text"}, {"source_sha256": "bad"},
    {"attempt_count": True}, {"retryable": "true"}, {"latency_seconds": float("nan")}, {"data_origin": "unknown"}])
def test_invalid_failure_metadata_rejected(changes):
    with pytest.raises(ExcelExportError, match="INVALID_PROCESSING_FAILURE"):
        failure(**changes)


def test_duplicate_failure_and_binding_conflicts_are_rejected():
    with pytest.raises(ExcelExportError, match="DUPLICATE_PROCESSING_FAILURE"):
        export_workbook([reviewed()], [failure(), failure()])
    other = reviewed(source=binding(source_sha256="b"*64))
    with pytest.raises(ExcelExportError, match="EXTRACTION_BINDING_CONFLICT"):
        export_workbook([reviewed(), other])
