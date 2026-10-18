"""Import and validate the private 15-document development benchmark, offline only."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

import pymupdf
from jsonschema import Draft202012Validator, ValidationError

from backend.tools import extraction_baseline as baseline
from evaluation.benchmark_excel import BenchmarkInputError, ExcelCell, iso_date, read_workbook
from evaluation.scoring import MULTI_VALUE_FIELDS, REQUIRED_FIELDS

DIFFICULTIES = {"A": "hard", "B": "medium", "C": "easy"}
EXPECTED_IDS = tuple(f"{letter}{number}" for letter in DIFFICULTIES for number in range(1, 6))
HEADERS = ("file_id", "difficulty") + tuple(
    column for field in REQUIRED_FIELDS for column in (field, f"{field}_status")
) + ("notes",)
STATUS_MAPPING = {
    "verified": ("verified", "present"),
    "missing": ("verified", "absent"),
    "uncertain": ("unverified", "ambiguous"),
    "unreadable": ("verified", "unreadable"),
}


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _text(cell: ExcelCell | None, *, required: bool = False) -> str:
    if cell is None or cell.raw == "":
        if required:
            raise BenchmarkInputError("REQUIRED_TEXT_CELL_EMPTY")
        return ""
    if cell.kind not in {"s", "inlineStr"}:
        raise BenchmarkInputError("TEXT_CELL_REQUIRED_NO_NUMERIC_COERCION")
    return cell.raw


def _field(cell: ExcelCell | None, status: str, name: str, date_1904: bool) -> dict[str, Any]:
    if status not in STATUS_MAPPING:
        raise BenchmarkInputError("UNKNOWN_ANNOTATION_STATUS")
    raw = cell.raw if cell is not None else ""
    if status == "verified" and not raw.strip():
        raise BenchmarkInputError("VERIFIED_VALUE_REQUIRED")
    if status in {"missing", "unreadable"} and raw:
        raise BenchmarkInputError("NONVALUE_STATUS_HAS_VALUE")
    values: list[dict[str, str]] = []
    if raw:
        if name == "sample_receipt_date":
            assert cell is not None
            values = [{"raw": raw, "normalized": iso_date(cell, date_1904=date_1904)}]
        else:
            text = _text(cell, required=True)
            # Semicolon is the workbook's ONLY multi-value delimiter. Keep order,
            # duplicates and exact raw segments; trim delimiter padding only in normalized.
            segments = text.split(";") if name in MULTI_VALUE_FIELDS else [text]
            if any(not item.strip() for item in segments):
                raise BenchmarkInputError("EMPTY_IDENTIFIER_SEGMENT")
            values = [{"raw": item, **({"normalized": item.strip()} if item != item.strip() else {})}
                      for item in segments]
    annotation, value_status = STATUS_MAPPING[status]
    return {"annotation_status": annotation, "value_status": value_status, "values": values,
            "evidence": [{"page_index": 0, "source_kind": "manual_review"}],
            "notes": f"Original Excel annotation: {status}"}


def read_annotations(workbook: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sheet = read_workbook(workbook)
    header = sheet.rows.get(1, {})
    columns = [chr(ord("A") + i) for i in range(len(HEADERS))]
    if set(header) != set(columns) or tuple(_text(header[c]) for c in columns) != HEADERS:
        raise BenchmarkInputError("EXACT_BENCHMARK_COLUMNS_REQUIRED")
    if len(sheet.rows) != 16:
        raise BenchmarkInputError("EXACTLY_15_RECORDS_REQUIRED")
    records = []
    provenance = {"sheet": sheet.name, "date_system": "1904" if sheet.date_1904 else "1900", "rows": []}
    seen = set()
    for number, cells in sorted(sheet.rows.items()):
        if number == 1:
            continue
        if set(cells) - set(columns):
            raise BenchmarkInputError("UNEXPECTED_COLUMN")
        row = {HEADERS[i]: cells.get(column) for i, column in enumerate(columns)}
        file_id = _text(row["file_id"], required=True)
        difficulty = _text(row["difficulty"], required=True)
        if file_id not in EXPECTED_IDS or file_id in seen:
            raise BenchmarkInputError("UNKNOWN_OR_DUPLICATE_FILE_ID")
        seen.add(file_id)
        if difficulty != DIFFICULTIES[file_id[0]]:
            raise BenchmarkInputError("DIFFICULTY_DOES_NOT_MATCH_FILE_ID")
        statuses = {name: _text(row[f"{name}_status"], required=True) for name in REQUIRED_FIELDS}
        fields = {name: _field(row[name], statuses[name], name, sheet.date_1904) for name in REQUIRED_FIELDS}
        notes = _text(row["notes"])
        records.append({"file_id": file_id, "difficulty": difficulty, "row": number,
                        "fields": fields, "notes": notes})
        provenance["rows"].append({"file_id": file_id, "excel_row": number, "statuses": statuses,
                                   "cells": {name: asdict(cell) if cell else None for name, cell in row.items()}})
    if seen != set(EXPECTED_IDS):
        raise BenchmarkInputError("MISSING_FILE_ID")
    return records, provenance


def _relative(path: Path) -> str:
    return path.resolve().relative_to(baseline.ROOT.resolve()).as_posix()


def create_bundle(workbook: Path, documents: Path, output: Path) -> dict[str, Any]:
    output = baseline.checked_artifact_path(str(output))
    if output == baseline.PRIVATE_ROOT.resolve() or output.exists():
        raise BenchmarkInputError("NEW_PRIVATE_BUNDLE_DIRECTORY_REQUIRED")
    workbook_bytes = workbook.read_bytes()
    records, provenance = read_annotations(workbook)
    if workbook.read_bytes() != workbook_bytes:
        raise BenchmarkInputError("WORKBOOK_CHANGED_DURING_IMPORT")
    expected_files = {f"{name}.pdf" for name in EXPECTED_IDS}
    actual_files = {p.name for p in documents.iterdir()}
    if actual_files != expected_files:
        raise BenchmarkInputError("DOCUMENT_SET_MUST_MATCH_ALL_15_IDS")
    validator = Draft202012Validator(baseline.load_json(
        baseline.ROOT / "evaluation/schemas/ground_truth.schema.json"
    ))
    frozen = []
    hashes: set[str] = set()
    for row in records:
        name = row["file_id"]
        source = documents / f"{name}.pdf"
        if not source.is_file() or source.resolve().parent != documents.resolve():
            raise BenchmarkInputError("DOCUMENT_MUST_BE_PRIVATE_LOCAL_FILE")
        content = source.read_bytes()
        digest = _hash(content)
        if digest in hashes:
            raise BenchmarkInputError("DUPLICATE_DOCUMENT_BYTES")
        hashes.add(digest)
        with pymupdf.open(stream=content, filetype="pdf") as pdf:
            if pdf.needs_pass or len(pdf) != 1:
                raise BenchmarkInputError("ONE_UNENCRYPTED_PAGE_REQUIRED")
        truth = {"schema_version": "1.0", "fixture_id": name, "fixture_set": "dev",
                 "source_document": {"filename": source.name, "sha256": digest, "page_count": 1},
                 "logical_forms": [{"form_id": name, "page_indices": [0],
                                    "fields": row["fields"], "notes": row["notes"]}]}
        validator.validate(truth)
        frozen.append((row, content, truth))
    provenance.update(import_version="benchmark-excel-v1", workbook_sha256=_hash(workbook_bytes))
    provenance_bytes = _json_bytes(provenance)
    manifest: dict[str, Any] = {
        "benchmark_version": "benchmark-excel-v1", "fixture_set": "dev",
        "approved_demo_data": False, "approved_provider": None,
        "workbook_path": _relative(output / "ground_truth.xlsx"), "workbook_sha256": _hash(workbook_bytes),
        "provenance_path": _relative(output / "provenance.json"), "provenance_sha256": _hash(provenance_bytes),
        "entries": [],
    }
    # Validate ALL source inputs before writing. A partial failed directory never
    # receives its completion manifest and is never reused or overwritten.
    output.mkdir(parents=True)
    (output / "documents").mkdir()
    (output / "truth").mkdir()
    (output / "ground_truth.xlsx").write_bytes(workbook_bytes)
    (output / "provenance.json").write_bytes(provenance_bytes)
    for row, content, truth in frozen:
        name = row["file_id"]
        pdf_path = output / "documents" / f"{name}.pdf"
        truth_path = output / "truth" / f"{name}.json"
        truth_bytes = _json_bytes(truth)
        pdf_path.write_bytes(content)
        truth_path.write_bytes(truth_bytes)
        manifest["entries"].append({
            "fixture_id": name, "difficulty": row["difficulty"],
            "document_path": _relative(pdf_path), "document_sha256": _hash(content),
            "ground_truth_path": _relative(truth_path), "ground_truth_sha256": _hash(truth_bytes),
            "source": {"path": f"{name}.pdf", "page_1based": 1, "excel_row": row["row"],
                       "workbook_sha256": _hash(workbook_bytes), "difficulty": row["difficulty"]},
        })
    # Existing validation remains authoritative and is never relaxed.
    baseline.prepare_entries(manifest)
    (output / "manifest.json").write_bytes(_json_bytes(manifest))
    return validate_bundle(output / "manifest.json")


def validate_bundle(manifest_path: Path) -> dict[str, Any]:
    manifest_path = baseline.checked_artifact_path(str(manifest_path))
    manifest = baseline.load_json(manifest_path)
    if manifest.get("benchmark_version") != "benchmark-excel-v1" or manifest.get("fixture_set") != "dev":
        raise BenchmarkInputError("BENCHMARK_DEV_MANIFEST_REQUIRED")
    for key in ("workbook", "provenance"):
        path = baseline.checked_artifact_path(manifest[f"{key}_path"])
        if baseline.sha256(path) != manifest[f"{key}_sha256"]:
            raise BenchmarkInputError("BUNDLE_PROVENANCE_HASH_MISMATCH")
    prepared = baseline.prepare_entries(manifest)
    if len(prepared) != 15 or {e["fixture_id"] for e, _, _ in prepared} != set(EXPECTED_IDS):
        raise BenchmarkInputError("EXACT_BENCHMARK_IDS_REQUIRED")
    records, provenance = read_annotations(baseline.checked_artifact_path(manifest["workbook_path"]))
    by_id = {row["file_id"]: row for row in records}
    saved = baseline.load_json(baseline.checked_artifact_path(manifest["provenance_path"]))
    if (any(saved.get(key) != provenance[key] for key in ("rows", "sheet", "date_system"))
            or saved.get("import_version") != "benchmark-excel-v1"
            or saved.get("workbook_sha256") != manifest["workbook_sha256"]):
        raise BenchmarkInputError("ORIGINAL_ANNOTATIONS_CHANGED")
    for entry, truth, content in prepared:
        row = by_id[entry["fixture_id"]]
        if (entry.get("difficulty") != row["difficulty"] or truth["logical_forms"][0]["fields"] != row["fields"]
                or truth["logical_forms"][0].get("notes") != row["notes"]
                or truth["logical_forms"][0]["form_id"] != entry["fixture_id"]
                or truth["logical_forms"][0]["page_indices"] != [0]
                or truth["source_document"]["page_count"] != 1):
            raise BenchmarkInputError("TRUTH_DOES_NOT_MATCH_WORKBOOK")
        with pymupdf.open(stream=content, filetype="pdf") as pdf:
            if pdf.needs_pass or len(pdf) != 1:
                raise BenchmarkInputError("ONE_UNENCRYPTED_PAGE_REQUIRED")
    counts = Counter(status for row in provenance["rows"] for status in row["statuses"].values())
    summary = baseline.summarize_evaluation(prepared, [])
    return {"status": "valid", "documents": len(prepared), "fixture_set": "dev",
            "original_annotations": dict(counts), "scorable_fields": counts["verified"] + counts["missing"],
            "excluded_fields": counts["uncertain"] + counts["unreadable"],
            "external_approval": manifest.get("approved_demo_data") is True,
            "annotations": summary["annotations"],
            "difficulty": {name: {"selected_documents": item["selected_documents"],
                                   "selected_scorable_fields": item["selected_scorable_fields"],
                                   "annotations": item["annotations"]}
                           for name, item in summary["by_difficulty"].items()}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate", help="Create a new private offline dev bundle")
    generate.add_argument("--workbook", type=Path, default=Path("evaluation/benchmark/ground_truth.xlsx"))
    generate.add_argument("--documents", type=Path, default=Path("evaluation/benchmark/documents"))
    generate.add_argument("--output", type=Path, required=True)
    validate = commands.add_parser("validate", help="Validate hashes, schema and original annotations; no AI")
    validate.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = (create_bundle(args.workbook, args.documents, args.output)
                  if args.command == "generate" else validate_bundle(args.manifest))
    except (ValueError, OSError, KeyError, IndexError, StopIteration, BadZipFile, ParseError,
            ValidationError, pymupdf.FileDataError):
        print(json.dumps({"status": "invalid", "code": "BENCHMARK_INPUT_INVALID"}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
