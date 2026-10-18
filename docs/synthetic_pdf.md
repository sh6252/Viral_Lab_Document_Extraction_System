# Readable synthetic PDF

The original development smoke PDF and its ground truth remain frozen. The old
PDF used UTF-16 strings with Helvetica and did not render Hebrew correctly.
The generator now uses PyMuPDF's HTML layout, bidirectional shaping and embedded
bundled Noto Hebrew font. Hebrew labels and values are RTL; identifiers and dates
are separate LTR cells. Every multi-value identifier is printed on its own line.
Only hardcoded fictional smoke values are used, with an explicit synthetic label.

PDF generation requires the development extra (`PyMuPDF` in `.[dev]`). No font download,
OS-specific font path, server configuration or provider call is required. This is
development tooling, not a preprocessing change to the extraction pipeline.

From the repository root, choose a NEW output filename in an existing directory:

```powershell
.\.venv\Scripts\python.exe -B -m backend.tools.extraction_smoke --synthetic-pdf-output .\evaluation\artifacts\readable-smoke\synthetic_hebrew_commitment.readable.pdf
```

The PDF-only option refuses to overwrite a file and does not write ground truth or
run extraction. The historical `--create-synthetic-fixture` option writes a PDF and
truth pair; do not use it to replace the frozen originals.

Validate against the original truth's field values and page count. Its source hash
identifies the ORIGINAL bytes and must not be treated as the new PDF's hash or
edited to match. Generation changes PDF bytes, not business values. Local text and
glyph-position regression tests do not establish Gemini extraction accuracy; view
the rendered PDF before a separately authorized live test.
