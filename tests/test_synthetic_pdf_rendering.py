"""Read real PDF glyphs and coordinates, not just the mocked extraction result."""

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pymupdf

from backend.tools.extraction_smoke import (
    SYNTHETIC_GROUND_TRUTH_PATH,
    SYNTHETIC_PDF_PATH,
    build_synthetic_pdf_bytes,
    main,
)


class SyntheticPDFRenderingTests(unittest.TestCase):
    def test_embedded_hebrew_font_rtl_positions_and_exact_ground_truth_values(self):
        truth = json.loads(SYNTHETIC_GROUND_TRUTH_PATH.read_text(encoding="utf-8"))
        expected_fields = truth["logical_forms"][0]["fields"]
        self.assertEqual(len(expected_fields), 7)
        with pymupdf.open(stream=build_synthetic_pdf_bytes(), filetype="pdf") as doc:
            self.assertEqual(len(doc), 1)
            page = doc[0]
            text = page.get_text()
            self.assertIn("Synthetic data only - no real patient record.", text)
            self.assertNotIn("\ufffd", text)
            # Actual PDF text must preserve identifiers, list members and Hebrew.
            for name, field in expected_fields.items():
                for value in field["values"]:
                    self.assertIn(value["raw"], text, name)

            fonts = [doc.extract_font(f[0]) for f in page.get_fonts()]
            hebrew_fonts = [pymupdf.Font(fontbuffer=data) for _, _, _, data in fonts if data]
            self.assertTrue(any(
                all(font.has_glyph(ord(char)) for char in "נועהכהןמרכזפואידמו")
                for font in hebrew_fonts
            ), "Hebrew font must be embedded with the required glyphs")

            lines = [line for block in page.get_text("rawdict")["blocks"]
                     if "lines" in block for line in block["lines"]]
            # Logical Hebrew characters must be placed from right to left.
            for value in ("נועה", "כהן"):
                matches = []
                for line in lines:
                    chars = [c for span in line["spans"] for c in span["chars"]]
                    if "".join(c["c"] for c in chars) == value:
                        matches.append(chars)
                self.assertEqual(len(matches), 1, value)
                positions = [c["origin"][0] for c in matches[0]]
                self.assertTrue(all(a > b for a, b in zip(positions, positions[1:])))

            # Digits and commitment prefixes must remain LTR inside the RTL form.
            for name in ("israeli_id", "central_numbers", "sample_receipt_date", "commitment_numbers"):
                for value in expected_fields[name]["values"]:
                    matches = []
                    for line in lines:
                        chars = [c for span in line["spans"] for c in span["chars"]]
                        text = "".join(c["c"] for c in chars)
                        start = text.find(value["raw"])
                        if start >= 0:
                            # MuPDF can group a value and the adjacent label into
                            # one line; check only this exact value's coordinates.
                            matches.append(chars[start:start + len(value["raw"])])
                    self.assertEqual(len(matches), 1, name)
                    positions = [c["origin"][0] for c in matches[0]]
                    self.assertTrue(all(a < b for a, b in zip(positions, positions[1:])))

            # No field can be clipped outside the page; rendering must produce ink.
            for block in page.get_text("dict")["blocks"]:
                self.assertTrue(page.rect.contains(pymupdf.Rect(block["bbox"])))
            pixmap = page.get_pixmap()
            self.assertLess(min(pixmap.samples), 255)

    def test_pdf_only_cli_does_not_extract_modify_truth_or_overwrite(self):
        originals = {path: path.read_bytes() for path in (
            SYNTHETIC_PDF_PATH, SYNTHETIC_GROUND_TRUTH_PATH,
        )}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "readable.pdf"
            with patch("backend.tools.extraction_smoke.run_smoke_test") as extract:
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["--synthetic-pdf-output", str(output)]), 0)
                extract.assert_not_called()
                before = output.read_bytes()
                with self.assertRaises(FileExistsError):
                    main(["--synthetic-pdf-output", str(output)])
                self.assertEqual(output.read_bytes(), before)
            self.assertEqual(list(Path(directory).iterdir()), [output])
        for path, content in originals.items():
            self.assertEqual(path.read_bytes(), content)
