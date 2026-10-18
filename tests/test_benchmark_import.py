"""Synthetic XLSX/PDF fixtures only; benchmark contents never enter source control."""

import json
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from unittest.mock import Mock

import pymupdf
import pytest
import xlsxwriter

from backend.tools import benchmark_import as importer
from backend.tools import extraction_baseline as baseline
from evaluation.benchmark_excel import BenchmarkInputError, ExcelCell, iso_date
from evaluation.scoring import REQUIRED_FIELDS


def synthetic_rows():
    rows = []
    for file_id in importer.EXPECTED_IDS:
        row = {"file_id": file_id, "difficulty": importer.DIFFICULTIES[file_id[0]],
               "first_name": "שם בדיקה", "last_name": "סינתטי", "israeli_id": "000000001",
               "central_numbers": "0007; 008/9);0007", "sample_receipt_date": datetime(2026, 10, 13),
               "sender": "Synthetic sender", "commitment_numbers": "HT-00001;HT-00002", "notes": "Synthetic only"}
        row.update({f"{name}_status": "verified" for name in REQUIRED_FIELDS})
        rows.append(row)
    return rows


def save_workbook(path, rows, *, headers=importer.HEADERS, date_1904=False):
    with xlsxwriter.Workbook(path, {"date_1904": date_1904}) as book:
        sheet = book.add_worksheet("Annotations")
        date_format = book.add_format({"num_format": "yyyy-mm-dd"})
        for column, name in enumerate(headers):
            sheet.write_string(0, column, name)
        for number, row in enumerate(rows, 1):
            for column, name in enumerate(headers):
                value = row.get(name)
                if value is None or value == "":
                    continue
                if isinstance(value, datetime):
                    sheet.write_datetime(number, column, value, date_format)
                elif isinstance(value, int):
                    sheet.write_number(number, column, value)
                elif isinstance(value, str) and value.startswith("=FORMULA_TEST"):
                    sheet.write_formula(number, column, "=1+1")
                else:
                    sheet.write_string(number, column, value)


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    root = tmp_path / "artifacts"
    monkeypatch.setattr(baseline, "PRIVATE_ROOT", root)
    monkeypatch.setattr(importer, "_relative", lambda path: str(path.resolve()))
    source = tmp_path / "source"
    source.mkdir()
    rows = synthetic_rows()
    workbook = tmp_path / "truth.xlsx"
    save_workbook(workbook, rows)
    for name in importer.EXPECTED_IDS:
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            page.insert_text((40, 40), "Synthetic document " + name)
            pdf.save(source / f"{name}.pdf")
    return workbook, source, root / "bundle", rows


def generate(inputs):
    workbook, source, output, _ = inputs
    summary = importer.create_bundle(workbook, source, output)
    return summary, baseline.load_json(output / "manifest.json")


def test_all_records_hashes_original_annotations_and_runner_approval(inputs, monkeypatch):
    workbook, source, output, rows = inputs
    before = {path: path.read_bytes() for path in (workbook, *source.iterdir())}
    summary, manifest = generate(inputs)
    assert summary["scorable_fields"] == 105 and summary["excluded_fields"] == 0
    assert summary["documents"] == 15 and summary["fixture_set"] == "dev"
    assert manifest["approved_demo_data"] is False and manifest["approved_provider"] is None
    assert len(baseline.prepare_entries(manifest)) == 15
    for name in importer.EXPECTED_IDS:
        assert (output / "documents" / f"{name}.pdf").read_bytes() == (source / f"{name}.pdf").read_bytes()
    truth = baseline.load_json(output / "truth/A1.json")
    fields = truth["logical_forms"][0]["fields"]
    assert fields["israeli_id"]["values"] == [{"raw": "000000001"}]  # intentionally invalid checksum
    assert fields["central_numbers"]["values"] == [
        {"raw": "0007"}, {"raw": " 008/9)", "normalized": "008/9)"}, {"raw": "0007"},
    ]
    assert fields["sample_receipt_date"]["values"][0]["normalized"] == "2026-10-13"
    provenance = baseline.load_json(output / "provenance.json")
    assert provenance["rows"][0]["cells"]["central_numbers"]["raw"] == rows[0]["central_numbers"]
    assert provenance["rows"][0]["cells"]["notes"]["raw"] == "Synthetic only"
    factory = Mock(side_effect=AssertionError("No external client may be constructed"))
    monkeypatch.setattr(baseline, "build_extraction_adapter", factory)
    with pytest.raises(ValueError, match="authorization"):
        baseline.run_baseline(output / "manifest.json", output / "run.json", provider="gemini", authorize=False)
    with pytest.raises(ValueError, match="approval"):
        baseline.run_baseline(output / "manifest.json", output / "run.json", provider="gemini", authorize=True)
    factory.assert_not_called()
    assert all(path.read_bytes() == original for path, original in before.items())
    with pytest.raises(BenchmarkInputError):
        importer.create_bundle(workbook, source, output)


@pytest.mark.parametrize("mutation", [
    "duplicate", "unknown_id", "difficulty", "unknown_status", "verified_blank", "missing_value",
    "unreadable_value", "numeric_id", "numeric_central", "empty_segment", "formula", "bad_date", "header", "row_count",
])
def test_invalid_workbooks_rejected_before_any_output(inputs, mutation):
    workbook, source, output, rows = inputs
    headers = importer.HEADERS
    if mutation == "duplicate": rows[1]["file_id"] = "A1"
    elif mutation == "unknown_id": rows[0]["file_id"] = "../private"
    elif mutation == "difficulty": rows[0]["difficulty"] = "easy"
    elif mutation == "unknown_status": rows[0]["sender_status"] = "guess"
    elif mutation == "verified_blank": rows[0]["sender"] = ""
    elif mutation == "missing_value": rows[0]["sender_status"] = "missing"
    elif mutation == "unreadable_value": rows[0]["sender_status"] = "unreadable"
    elif mutation == "numeric_id": rows[0]["israeli_id"] = 1
    elif mutation == "numeric_central": rows[0]["central_numbers"] = 7
    elif mutation == "empty_segment": rows[0]["central_numbers"] = "001;;002"
    elif mutation == "formula": rows[0]["sender"] = "=FORMULA_TEST"
    elif mutation == "bad_date": rows[0]["sample_receipt_date"] = "13/10/2026"
    elif mutation == "header": headers = (*headers[:-1], "unknown")
    elif mutation == "row_count": rows.pop()
    save_workbook(workbook, rows, headers=headers)
    with pytest.raises((BenchmarkInputError, ValueError)):
        importer.create_bundle(workbook, source, output)
    assert not output.exists()


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate_bytes", "multipage", "bad_pdf"])
def test_document_matching_and_structure(inputs, mutation):
    workbook, source, output, _ = inputs
    if mutation == "missing": (source / "A1.pdf").unlink()
    elif mutation == "extra": (source / "A6.pdf").write_bytes(b"extra")
    elif mutation == "duplicate_bytes": (source / "A2.pdf").write_bytes((source / "A1.pdf").read_bytes())
    elif mutation == "multipage":
        with pymupdf.open() as pdf:
            pdf.new_page(); pdf.new_page()
            pdf.save(source / "A1.pdf")
    elif mutation == "bad_pdf": (source / "A1.pdf").write_bytes(b"not a PDF")
    with pytest.raises((BenchmarkInputError, pymupdf.FileDataError)):
        importer.create_bundle(workbook, source, output)
    assert not output.exists()


def test_status_exclusions_false_positives_and_difficulty_yield(inputs):
    workbook, _, output, rows = inputs
    rows[0].update(israeli_id_status="uncertain", central_numbers_status="uncertain",
                   commitment_numbers_status="unreadable", commitment_numbers="", sender_status="missing", sender="")
    save_workbook(workbook, rows)
    summary, manifest = generate(inputs)
    assert summary["scorable_fields"] == 102 and summary["excluded_fields"] == 3
    assert summary["original_annotations"] == {"verified": 101, "uncertain": 2, "unreadable": 1, "missing": 1}
    prepared = baseline.prepare_entries(manifest)
    records = []
    for index, (entry, truth, _) in enumerate(prepared):
        fields = deepcopy(truth["logical_forms"][0]["fields"])
        for name, field in fields.items():
            field.pop("annotation_status")
            if field["value_status"] == "absent": field["value_status"] = "missing"
            if field["value_status"] == "ambiguous": field["value_status"] = "present"
        if index == 0:
            fields["sender"] = {"value_status": "present", "values": [{"raw": "Unexpected synthetic sender"}]}
            fields["commitment_numbers"] = {"value_status": "present", "values": [{"raw": "NOT-SCORABLE"}]}
        payload = {"logical_forms": [{"form_id": "provider-form", "fields": fields}]}
        association, score = baseline.score_fixture(entry, truth, payload)
        assert payload["logical_forms"][0]["form_id"] == "provider-form"
        records.append({"fixture_id": entry["fixture_id"], "status": "succeeded", "scoring": score,
                        "measurement_status": association["status"]})
    first = records[0]["scoring"]
    assert first["overall"]["evaluated_fields"] == 4
    assert first["overall"]["false_positive_values"] == 1
    assert first["overall"]["skipped_unverified_fields"] == 2
    assert first["overall"]["skipped_non_scorable_fields"] == 1
    assert first["fields"]["commitment_numbers"]["false_positive_values"] == 0
    # One provider failure remains in yield, never in conditional extraction accuracy.
    records[-1].update(status="failed", scoring=None)
    report = baseline.summarize_evaluation(prepared, records)
    assert report["provider_success_rate"] == pytest.approx(14 / 15)
    assert report["overall"]["evaluated_fields"] == 95
    assert report["overall"]["exact_match_rate"] == pytest.approx(94 / 95)
    assert report["end_to_end_exact_match_rate"] == pytest.approx(94 / 102)
    assert report["annotations"]["uncertain"] == 2
    assert report["annotations"]["unreadable"] == 1
    assert report["by_difficulty"]["hard"]["selected_scorable_fields"] == 32
    assert report["by_difficulty"]["easy"]["provider_success_rate"] == pytest.approx(4 / 5)
    assert report["fields"]["sender"]["false_positive_values"] == 1
    assert baseline.summarize_evaluation(prepared, records[:-1])["end_to_end_exact_match_rate"] is None
    records[-2]["reused"] = True
    assert baseline.summarize_evaluation(prepared, records)["provider_success_rate"] == pytest.approx(13 / 14)


@pytest.mark.parametrize("date_1904", [False, True])
def test_excel_date_systems(inputs, date_1904):
    workbook, _, output, rows = inputs
    save_workbook(workbook, rows, date_1904=date_1904)
    generate(inputs)
    truth = baseline.load_json(output / "truth/A1.json")
    assert truth["logical_forms"][0]["fields"]["sample_receipt_date"]["values"][0]["normalized"] == "2026-10-13"


@pytest.mark.parametrize("raw", ["60", "45943.5", "NaN", "-1"])
def test_ambiguous_excel_serial_dates_fail(raw):
    with pytest.raises(BenchmarkInputError):
        iso_date(ExcelCell("K2", "n", raw, "yyyy-mm-dd"), date_1904=False)


def test_iso_date_text_supported_without_locale_guessing():
    assert iso_date(ExcelCell("K2", "s", "2026-10-13", "@"), date_1904=False) == date(2026, 10, 13).isoformat()


def test_excel_text_escapes_and_uncertain_blank_preserved(inputs):
    workbook, _, output, rows = inputs
    rows[0].update(sender="Synthetic\rline _x000D_", israeli_id="", israeli_id_status="uncertain")
    save_workbook(workbook, rows)
    summary, _ = generate(inputs)
    truth = baseline.load_json(output / "truth/A1.json")
    fields = truth["logical_forms"][0]["fields"]
    assert fields["sender"]["values"][0]["raw"] == "Synthetic\rline _x000D_"
    assert fields["israeli_id"]["values"] == []
    assert fields["israeli_id"]["annotation_status"] == "unverified"
    assert summary["excluded_fields"] == 1


def test_rehashed_truth_still_must_match_original_workbook(inputs):
    _, _, output, _ = inputs
    _, manifest = generate(inputs)
    path = output / "truth/A1.json"
    truth = baseline.load_json(path)
    truth["logical_forms"][0]["fields"]["first_name"]["values"][0]["raw"] = "Synthetic tampering"
    path.write_text(json.dumps(truth), encoding="utf-8")
    manifest["entries"][0]["ground_truth_sha256"] = baseline.sha256(path)
    (output / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(BenchmarkInputError, match="TRUTH_DOES_NOT_MATCH_WORKBOOK"):
        importer.validate_bundle(output / "manifest.json")


@pytest.mark.parametrize("target", ["documents/A1.pdf", "truth/A1.json", "provenance.json", "ground_truth.xlsx"])
def test_bundle_tampering_fails(inputs, target):
    _, _, output, _ = inputs
    generate(inputs)
    (output / target).write_bytes(b"changed")
    with pytest.raises(ValueError):
        importer.validate_bundle(output / "manifest.json")


def test_cli_safe_error_no_values_or_paths(inputs, capsys):
    workbook, source, output, rows = inputs
    rows[0]["sender"] = ""
    save_workbook(workbook, rows)
    assert importer.main(["generate", "--workbook", str(workbook), "--documents", str(source), "--output", str(output)]) == 1
    assert json.loads(capsys.readouterr().out) == {"status": "invalid", "code": "BENCHMARK_INPUT_INVALID"}


def test_path_escape_cannot_create_bundle(inputs):
    workbook, source, output, _ = inputs
    with pytest.raises(ValueError, match="inside evaluation/artifacts"):
        importer.create_bundle(workbook, source, output.parent.parent / "public")
