# Private development benchmark

All 15 A1–A5/B1–B5/C1–C5 documents are **development data**, not an independent
holdout. Altered personal details do not grant external-processing permission.
The input directory and all generated artifacts are Git-ignored. Never force-add
these files or treat the hash checks as proof of anonymization or authenticity.

## Offline generation and validation

From the repository root, with the existing development environment active:

```powershell
.\.venv\Scripts\python.exe -B -m backend.tools.benchmark_import generate --output evaluation/artifacts/benchmark-dev-v1
.\.venv\Scripts\python.exe -B -m backend.tools.benchmark_import validate --manifest evaluation/artifacts/benchmark-dev-v1/manifest.json
```

Inputs default to `evaluation/benchmark/ground_truth.xlsx` and
`evaluation/benchmark/documents/`. Optional `--workbook` and `--documents` arguments
select local inputs. Output must be a NEW directory below `evaluation/artifacts/`.
An existing or partially written directory is never reused or overwritten. All
inputs are validated before writing; an I/O failure may leave an incomplete
directory without a manifest. Use a new output path after such a failure.

The bundle contains byte-identical `documents/*.pdf`, schema-valid `truth/*.json`,
a byte-identical `ground_truth.xlsx`, `provenance.json`, and `manifest.json`.
The manifest hashes every PDF, truth JSON, archived workbook and provenance file.
Provenance retains original cell values/types/coordinates/formats, sheet, row,
date system, statuses and notes. Validation re-imports the archived workbook and
compares annotations with generated truth, then reuses the baseline's unchanged
`prepare_entries()` checks. No AI client is constructed by either command.

Generation requires the existing development dependencies, including PyMuPDF for
local PDF page-count inspection. The narrow standard-library OOXML reader accepts
one visible sheet with the exact 17 benchmark columns. It rejects formulas,
external links, merged cells, ambiguous dates and unsupported cell types instead
of calculating or guessing. It is not a general spreadsheet engine.

## Annotation mapping

- `verified`: `annotation_status=verified`, `value_status=present`, nonblank value.
- `missing`: `verified/absent`, empty values, scored for false positives.
- `uncertain`: `unverified/ambiguous`, candidate values preserved, excluded from strict accuracy.
- `unreadable`: `verified/unreadable`, no invented value, excluded from strict accuracy.

Missing/unreadable cells containing a candidate value are rejected for explicit
annotation resolution. Unknown statuses and verified empty cells are rejected.
Workbook statuses and row notes are preserved, not rewritten in the source.

Identifiers must be text cells. Numeric identifiers are rejected because lost
zeros cannot be reconstructed safely; there is no Israeli-ID checksum gate.
Only `central_numbers` and `commitment_numbers` split on semicolons. Ordered raw
segments and duplicates remain intact; delimiter padding is trimmed only in the
optional normalized value. Empty segments are rejected. All original cell text
remains available in provenance. The existing scorer compares multi-values as
sets, so ordering and duplicate multiplicity are not part of its exact match.

Date cells use their workbook's 1900/1904 epoch and normalize to `YYYY-MM-DD`.
Numeric serials remain strings in `raw`, with the ISO date in `normalized`.
Exact ISO text dates are also accepted. Locale-dependent text, fractional days,
Excel's fictitious 1900-02-29, and invalid dates are rejected, not rounded.

## Reporting and external boundary

The existing `score_fixture()` associates one returned form with one frozen
single-page document in a scoring copy. `scoring.py` and its schema are unchanged.
The baseline summary now adds `annotations`, `annotations_by_field` and
`by_difficulty`, with `hard`, `medium`, `easy` derived from validated manifest data.
Uncertain/unreadable counts describe ALL selected truth, even after provider
failures; their predicted values never become strict correct/incorrect scores.
Existing per-field accuracy and multi-value metrics remain available.

Keep these denominators separate:

- `overall.exact_match_rate`: exact matches / scorable fields in scored responses.
- `provider_success_rate`: successful newly attempted documents / newly attempted
  documents; reused results are counted separately and are not fresh API calls.
- `end_to_end_exact_match_rate`: exact matches / all selected scorable fields,
  including failed or structurally unscorable documents. This remains null until
  every selected document has been attempted. Unattempted counts are explicit.

The inspected workbook has 100 verified-present, two verified-absent, two
uncertain and one unreadable field: 102 scorable and three excluded of 105 total.
Difficulty denominators are hard=35, medium=33, easy=34. These are annotation
counts, not extraction-accuracy results.

Generated manifests set `approved_demo_data=false` and `approved_provider=null`.
The existing baseline runner therefore refuses live execution even if process
environment settings allow Gemini. Neither importer command can grant approval.
No live benchmark command is included here: document/provider authorization is a
separate explicit decision, and the server-side provider gates still apply.

Focused offline tests (synthetic inputs, no benchmark records in tests):

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider tests/test_benchmark_import.py tests/test_extraction_baseline.py tests/test_scoring.py
```
