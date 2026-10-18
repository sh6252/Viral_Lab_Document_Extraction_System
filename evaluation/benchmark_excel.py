"""Strict, offline reader for the benchmark's single-sheet, literal-cell XLSX.

This is deliberately not a general Excel engine: formulas, external links,
unsupported cell types, and ambiguous dates fail instead of being evaluated.
Original cell encodings and values are retained for import provenance.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
OFFICE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


class BenchmarkInputError(ValueError):
    """Safe reason code without document values or patient information."""


@dataclass(frozen=True)
class ExcelCell:
    coordinate: str
    kind: str
    raw: str
    number_format: str


@dataclass(frozen=True)
class ExcelSheet:
    name: str
    date_1904: bool
    rows: dict[int, dict[str, ExcelCell]]


def _excel_text(value: str) -> str:
    # OOXML escapes controls; _x005F_ protects a literal escape sequence. Decode
    # once, preserving e.g. literal "_x000D_" rather than turning it into CR.
    return re.sub(
        r"_x005[Ff]_(x[0-9a-fA-F]{4}_)|_x([0-9a-fA-F]{4})_",
        lambda match: "_" + match[1] if match[1] else chr(int(match[2], 16)), value,
    )


def read_workbook(path: Path) -> ExcelSheet:
    with ZipFile(path) as archive:
        if sum(item.file_size for item in archive.infolist()) > 64 * 1024 * 1024:
            raise BenchmarkInputError("WORKBOOK_TOO_LARGE")
        if len(archive.namelist()) != len(set(archive.namelist())):
            raise BenchmarkInputError("DUPLICATE_ZIP_MEMBER")
        if any("externalLinks/" in name or name.endswith("vbaProject.bin") for name in archive.namelist()):
            raise BenchmarkInputError("ACTIVE_WORKBOOK_CONTENT")
        for name in archive.namelist():
            if name.endswith(".rels"):
                root = ET.fromstring(archive.read(name))
                if any(r.attrib.get("TargetMode") == "External" for r in root):
                    raise BenchmarkInputError("EXTERNAL_WORKBOOK_LINK")
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        sheets = workbook.findall("s:sheets/s:sheet", NS)
        if len(sheets) != 1 or sheets[0].attrib.get("state", "visible") != "visible":
            raise BenchmarkInputError("ONE_VISIBLE_SHEET_REQUIRED")
        properties = workbook.find("s:workbookPr", NS)
        date_1904 = properties is not None and properties.attrib.get("date1904") in {"1", "true"}
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        sheet_id = sheets[0].attrib[f"{{{OFFICE_REL}}}id"]
        target = next(r.attrib["Target"] for r in relationships if r.attrib["Id"] == sheet_id)
        sheet_path = posixpath.normpath(target.lstrip("/") if target.startswith("/") else "xl/" + target)
        if not sheet_path.startswith("xl/worksheets/"):
            raise BenchmarkInputError("INVALID_SHEET_PATH")
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = [_excel_text("".join(t.text or "" for t in si.findall(".//s:t", NS)))
                       for si in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("s:si", NS)]
        styles = ET.fromstring(archive.read("xl/styles.xml"))
        formats = {int(f.attrib["numFmtId"]): f.attrib["formatCode"]
                   for f in styles.findall("s:numFmts/s:numFmt", NS)}
        formats.update({0: "General", 14: "mm-dd-yy", 49: "@"})
        cell_styles = [formats.get(int(f.attrib.get("numFmtId", "0")), "unsupported")
                       for f in styles.findall("s:cellXfs/s:xf", NS)]
        root = ET.fromstring(archive.read(sheet_path))
        if root.findall("s:mergeCells/s:mergeCell", NS):
            raise BenchmarkInputError("MERGED_CELLS_UNSUPPORTED")
        rows: dict[int, dict[str, ExcelCell]] = {}
        for row in root.findall("s:sheetData/s:row", NS):
            number = int(row.attrib["r"])
            if number in rows:
                raise BenchmarkInputError("DUPLICATE_ROW")
            cells = {}
            for cell in row.findall("s:c", NS):
                coordinate = cell.attrib["r"]
                match = re.fullmatch(r"([A-Z]+)([1-9][0-9]*)", coordinate)
                if not match or int(match[2]) != number or match[1] in cells:
                    raise BenchmarkInputError("INVALID_CELL_COORDINATE")
                if cell.find("s:f", NS) is not None:
                    raise BenchmarkInputError("FORMULA_UNSUPPORTED")
                kind = cell.attrib.get("t", "n")
                value = cell.find("s:v", NS)
                raw = (value.text or "") if value is not None else ""
                if kind == "s":
                    raw = strings[int(raw)]
                elif kind == "inlineStr":
                    raw = _excel_text("".join(t.text or "" for t in cell.findall("s:is//s:t", NS)))
                elif kind not in {"n", "d"}:
                    raise BenchmarkInputError("UNSUPPORTED_CELL_TYPE")
                cells[match[1]] = ExcelCell(coordinate, kind, raw, cell_styles[int(cell.attrib.get("s", "0"))])
            if any(c.raw != "" for c in cells.values()):
                rows[number] = cells
        return ExcelSheet(sheets[0].attrib["name"], date_1904, rows)


def iso_date(cell: ExcelCell, *, date_1904: bool) -> str:
    """Convert exact dates only; never round fractions or guess locale formats."""
    try:
        if cell.kind == "n":
            # This reader supports the benchmark's explicit date formats only.
            fmt = cell.number_format.lower().replace('"', "").replace("\\", "")
            if fmt not in {"yyyy-mm-dd", "mm-dd-yy", "yyyy/mm/dd", "dd/mm/yyyy"}:
                raise BenchmarkInputError("DATE_CELL_FORMAT_REQUIRED")
            serial = Decimal(cell.raw)
            if not serial.is_finite() or serial != serial.to_integral_value() or serial < 0:
                raise BenchmarkInputError("DATE_MUST_HAVE_NO_TIME_COMPONENT")
            if not date_1904 and serial == 60:
                raise BenchmarkInputError("EXCEL_FICTITIOUS_LEAP_DAY")
            epoch = date(1904, 1, 1) if date_1904 else date(1899, 12, 31)
            days = int(serial) - (1 if not date_1904 and serial > 60 else 0)
            return (epoch + timedelta(days=days)).isoformat()
        if cell.kind == "d":
            value = datetime.fromisoformat(cell.raw)
            if value.time().isoformat() != "00:00:00" or value.tzinfo is not None:
                raise BenchmarkInputError("DATE_MUST_HAVE_NO_TIME_COMPONENT")
            return value.date().isoformat()
        value = date.fromisoformat(cell.raw)
        if value.isoformat() != cell.raw:
            raise BenchmarkInputError("ISO_DATE_REQUIRED")
        return value.isoformat()
    except (ValueError, InvalidOperation, OverflowError) as error:
        if isinstance(error, BenchmarkInputError):
            raise
        raise BenchmarkInputError("INVALID_DATE") from None
