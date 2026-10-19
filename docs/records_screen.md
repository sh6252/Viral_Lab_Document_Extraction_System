# Saved records, search and reopening

Use the existing durable launcher and React frontend described in
[business_persistence.md](business_persistence.md). No new migration or dependency
is needed beyond migration `0002_business_persistence`. The original in-memory
and isolated experiment launchers remain separate; their records section explains
that PostgreSQL storage is required rather than presenting an empty durable list.

## Browser workflow

1. Start the durable backend on loopback port 8000 and Vite on 5173. Open
   `http://127.0.0.1:5173/` and use **רשומות שמורות**.
2. Search by first/last/full name, Israeli ID, any central number, any commitment
   number or sender. All identifiers remain strings, including leading zeros.
3. Optionally filter by pending/completed review, exact sender name and exact
   sample receipt date. Click **חיפוש / רענון**; **הקודם / הבא** paginate 20 rows.
4. Click **פתיחה לבדיקה** on the desired logical form. Both current persisted
   review state and original PDF/image are read from the private backend.
   No extraction POST, provider call or new external consent is needed.
5. Inspect the original alongside current reviewed values. Previously approved
   values remain visible, with no new action selected. To fix an inaccurate
   approved date, select **Correct and approve**, enter the corrected string,
   provide the operator declaration and reason, and save explicitly.
6. Confirm the new revision and saved status; download XLSX as before. Other
   fields/forms retain their own state and old audit entries remain immutable.
7. Restart the backend/browser, find the record again and reopen it. The preview,
   reviewed values, revision and export come from PostgreSQL, without re-extraction.

Opening a different record asks before discarding unsaved explicit choices.
Pending ambiguous review saves still require retrying the SAME command before
navigation. A failed reopen/source fetch leaves the current review inputs intact.
Failure to refresh the list is reported separately from a successful review save.

## API and summary semantics

`GET /api/v1/business-records` is available only with a `DurableWorkflow`, under
the existing loopback peer/Host, exact Origin and `X-Local-Demo: 1` boundary.
Responses are no-store. Default app startup does not expose these demo routes.

Query parameters:

- `q`: case-insensitive literal substring, maximum 200 characters. Searches all
  six requested name/identifier/sender fields, including first-name + last-name.
  SQL `%`, `_` and the escape character are searched literally, not as wildcards.
- `status`: `pending` or `completed`; absent means either. These map to existing
  `unresolved`/`complete` business statuses, not the OCR review-session status.
- `sender`: exact sender match after trimming surrounding whitespace, maximum
  200 characters. This is not a new controlled organization registry.
- `receipt_date`: valid ISO query date (`YYYY-MM-DD`). Matches exact stored ISO,
  day/month/year or dotted day.month.year representations, including normalized
  provider dates only when the field is still unreviewed. An approved correction
  takes precedence over the old raw/normalized provider date. Ambiguous, partial
  or unsupported date representations are not guessed or SQL-cast into dates.
- `limit`: 1–100, default 20. `offset`: 0–100000, default 0.

The response contains `items`, `total`, `limit`, `offset`. Each item includes
`run_id`/`document_id`, `form_id`, revision, pending/completed status, seven summary
value arrays, document creation timestamp and latest review receipt timestamp.
Each logical form has a separate row, with stable document/form ordering.
No original bytes, canonical snapshot, review token, evidence or audit bodies
are returned in list responses.

Summaries/search use approved reviewed values first, original candidate raw values
only for unreviewed fields, and empty arrays for acknowledged blanks or explicitly
rejected/unresolved fields. Pending candidate values are visibly labelled as such.
This does not approve missing fields or silently restore rejected candidates.

SQLAlchemy binds every search/filter/page value. Filtering and pagination run in
PostgreSQL. A repeatable-read transaction gives count/page/projection/audit checks
a consistent committed snapshot. Returned projections are hash-checked, replayed
through domain validation, checked against document scope and compared with stored
audit events. Full source integrity checks also run when reopening the document.

The existing protected run/form, source, review and XLSX APIs are reused. Preview
fetches carry the same custom header; the browser displays a temporary blob URL
rather than exposing a public original-document URL. Blob URLs are revoked when
replaced or unmounted. React escapes summary text; no HTML from records is inserted.

## Offline tests and limits

Validation on 2026-10-19: **861 Python tests passed, zero skipped**, including
**63 real PostgreSQL integration tests** (48 existing plus 15 records-screen
cases). All **12 frontend regression tests**, strict TypeScript checking and the
Vite production build passed. No Gemini or other external service was called.

Tests use synthetic mocked extractions only. Read `REVIEW_TEST_DSN` privately from
the Git-excluded root `.env.test.local`, validate the explicit disposable local
database, then set `REVIEW_TEST_ALLOW_DISPOSABLE=yes` in the test process only.
Do not run destructive tests against the main `viral_lab` database.

Coverage includes all requested search fields, literal wildcard/injection text,
query bounds, status/sender/date filters, pagination, per-form isolation, source
access protections, restart/reopen, further approved-date correction, retained audit
prefix, and XLSX with the additional audit decision and no new provider call.
Frontend component-handler tests cover RTL controls, pagination/query reset,
protected preview fetch, reopen/save/export, cancelled navigation and source/list
failures. They do not substitute for a real browser rendering check.

This small-data implementation queries the existing JSON projections rather than
adding search indexes or new materialized columns. Large datasets will require
indexing/performance work. There is no fuzzy search, date-range filter, production
identity/RBAC or audit-history viewer in this slice. Existing audit read APIs and
XLSX audit sheets remain available. Live provider availability and extraction
accuracy are not measured by these tests.
