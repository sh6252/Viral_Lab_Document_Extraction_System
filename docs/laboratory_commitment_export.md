# Laboratory commitment-level Excel export — 2026-10-20

The laboratory's latest written clarification supersedes the older one-Excel-row-
per-logical-form wording in the original AGENTS/ADR baseline and historical design
reports. Persistence, review, API identities and the seven-field extraction schema
remain at logical-form granularity. Only derived Excel rows expand.

## Row contract

- Validate every supplied form, its immutable snapshot/binding and review audit,
  and require the complete form set of an extraction exactly once as before.
- Use only approved reviewed values in business cells. Each exact distinct reviewed
  commitment string becomes one `Forms` row, in first-occurrence order. Forms follow
  the existing input order; this does not sort identifiers or change their strings.
- Keep the existing plural column name `commitment_numbers`, but write a single
  literal string, such as `000001`, rather than the previous JSON array. The change
  is intentional: downstream consumers must stop JSON-decoding this column.
- Repeat names, Israeli ID, receipt date, sender and **all** reviewed central numbers
  on each row. `central_numbers` keeps the existing compact JSON array. Repeat all
  document/form/extraction/provider/version/review metadata and field statuses.
- No reviewed commitments means one row with an empty commitment cell. Preserve
  actual states: unreviewed/rejected/unresolved stays unresolved; an existing
  explicit acknowledged-blank decision stays acknowledged blank. A record already
  complete through explicit decisions is not falsely downgraded or newly approved.
- Keep `ProcessingFailures` separate. Append each `ReviewAudit` action once per form,
  outside row expansion; never multiply audit events by the commitment count.
- Keep the existing text-only writer, formula/URL/number protections, XML/UTF-16
  cell validation and Excel row limit. Large original/reviewed arrays can still
  exceed audit-cell limits; splitting business rows does not bypass that check.

For commitments `456001`, `456002`, `456003` and central numbers `803215`, `803216`,
the previous output had one commitment-array cell `["456001","456002","456003"]`.
The new output has exactly three rows with commitment cells `456001`, `456002`,
`456003`. Each repeats `["803215","803216"]` and the same patient/metadata values.
There are no six-row commitment/central combinations.

## Duplicate policy and limits of the data model

Collapse exact repeated reviewed strings **only within one form's Excel output**.
No trimming, numeric conversion, Unicode normalization or use of `normalized` values
is permitted for this comparison. `0001`, `1`, ` 0001` and `0001 ` remain distinct.
The full raw/normalized/reviewed lists, including all duplicates, remain in stored
snapshots and audit sheets. Do not merge numbers across forms/documents.

The current model stores shared business fields per form, not different attributes
for each occurrence of a commitment. Thus exact duplicate occurrences have no
separate conflicting row attributes to discard. If commitments actually require
different patient/date/central associations inside one form, this model cannot
represent that reliably: require laboratory clarification/human review rather than
inventing pairings. No global uniqueness constraint is introduced.

CLI summary `forms`, record statuses and review-action counts still count logical
records, not expanded Excel rows. API contracts, database keys/revisions, schema
versions and the business-storage codec are unchanged. Existing records can be
decoded and exported by the new layout without modifying their canonical snapshot
or original prompt provenance. Old XLSX files/experiment artifacts are not rewritten.

## Extraction instructions

Prompt `he-commitment-v3-lab-commitments` states that every document received with a
sample is a commitment document. A particular number still requires contextual
evidence: `מספר התחייבות`/`התחייבות`/`דרישה`, and `דגימה` specifically when the Leumit
(`לאומית`) context establishes it. Generic sample labels or examination/test codes
are not globally mapped to commitments. `מרכזי` stickers and handwritten
`מספר מרכזי` indicate internal laboratory identifiers. Prefer receipt dates below
these identifiers or at a relevant `התקבל` stamp over unrelated issue/test dates.
Uncertain roles/competing receipt dates use ambiguous empty values and notes for
human review. No heuristic normalizer, output repair or approval behavior changed.

Seven-field Pydantic, generation schema and final canonical validation remain
mandatory and unchanged. Old prompts/saved results are preserved. Mocked tests
validate instructions, transport, contract behavior and export semantics, not actual
Gemini recognition/accuracy. No live comparison or benchmark truth change is made.

## Verification boundary

The task prohibits migrations/destructive SQL. The ordinary PostgreSQL integration
harness runs migrations and drops disposable schemas, so it must not be executed
for this task. Integration assertions are updated for the new row counts but remain
unexecuted here. A read-only inventory of the authorized disposable test database
found no business tables; no tables or records were created. Offline compatibility
tests exercise the actual versioned stored codec and HTTP reopen/source/audit/export
paths with mocked storage, asserting zero storage writes/provider calls. They are
not a new claim of real-database integration coverage.

Final offline verification: 812 backend tests passed (zero failures/skips), with
the 63 PostgreSQL integration cases explicitly excluded rather than executed.
The focused export/prompt/legacy/API selection passed 118 tests. Frontend: 12
tests passed; strict TypeScript checking and dependency checks passed. Existing
integration assertions were updated for expanded row counts but not run under
this task's no-migration/no-destructive-SQL boundary. No live model accuracy
measurement, database migration or automatic Git commit was performed.

Task-local reproduction (the ignored runner blocks DB connections, Alembic and
external networking, permits only internal loopback needed by Windows asyncio,
and uses a new disposable pytest scratch directory each time):

```powershell
.\.venv\Scripts\python.exe -B storage/run_commitment_export_offline_tests.py
npm --prefix frontend test
npm --prefix frontend run typecheck
.\.venv\Scripts\python.exe -B -m pip check
```

The runner and integrity fingerprints are local validation artifacts in ignored
`storage/`; they are not an application dependency. An ordinary equivalent suite
selection is `python -B -m pytest -q -p no:cacheprovider --ignore=tests/integration`.
