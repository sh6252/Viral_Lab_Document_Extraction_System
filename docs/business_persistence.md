# Durable seven-field workflow

This opt-in local application persists the existing upload/review/XLSX workflow in
PostgreSQL. It does not replace the isolated experiment launchers or alter the
default application, Gemini configuration, prompts, extraction policy or React
save-state behavior. It retains the local-only access boundary; it is not a
production identity or laboratory deployment solution.

## Storage and transaction boundaries

Migration `0002_business_persistence` adds three separate tables:

- `business_documents`: original private PDF/image bytes (BYTEA), MIME type,
  source SHA-256, opaque run/review capabilities, prompt/pipeline versions,
  immutable canonical extraction JSON and its SHA-256, or a separate safe failure.
  The canonical JSON includes the existing provider/model and field evidence metadata.
- `business_records`: one row per logical form, stable ordinal, seven-field review
  projection, revision, complete/unresolved status and projection SHA-256.
- `business_review_events`: append-only decisions with exact original/reviewed
  strings, operator declaration, declared timestamp/reason, revision transition,
  content SHA-256 and server receipt timestamp.

The existing two-field OCR review tables retain their original meaning. Neither
their `completed` status nor provider output approves a business record.

The private input is committed before extraction. A database insert failure
prevents the provider call. Extraction completion and all returned form records
commit together. Review locks the parent document row, reloads and validates the
complete aggregate, applies the existing business-review transition, and commits
new events and projections in the same transaction. Exact command replay adds
no events or revisions. Concurrent workers serialize through PostgreSQL, rather
than relying on a Python lock. Reads take a shared parent lock for a consistent
document/forms/audit snapshot.

Every read checks source/snapshot/projection/event hashes, source/form scope,
the exact form catalog, and domain replay of the full audit against the stored
projection. Raw/normalized identifiers and multiple values remain strings.
Malformed storage fails closed with a safe error; it is never silently repaired.
Database triggers prevent edits/deletion of finalized document inputs, updates or
deletes of audit events, and non-increasing record revisions.

Unlike `DemoWorkflow._runs`, this mode has no record TTL or eight-run eviction
limit. The upload endpoint still enforces the configured size/type limits; the
database additionally caps each original at 20 MiB. Storage capacity/retention
must be managed explicitly. Source bytes never enter a public static directory.

## Local Windows setup and launch

Prerequisite: a running local PostgreSQL database and an appropriate role. Nothing
here installs PostgreSQL, pulls images, starts Docker, or creates/resets a database.
Use a dedicated database; keep migration/admin privileges separate from application
privileges in real deployments. Do not grant the application TRUNCATE or trigger-
disabling privileges. Database backups contain sensitive originals and review data.

Run each command from the repository root. Enter the DSN privately; it is placed
only in the current PowerShell process environment and is not written to `.env`:

```powershell
$env:REVIEW_DATABASE_DSN = [System.Net.NetworkCredential]::new('', (Read-Host 'Local PostgreSQL DSN' -AsSecureString)).Password
.\.venv\Scripts\python.exe -B -m alembic -c alembic.ini upgrade head
.\.venv\Scripts\python.exe -B -m uvicorn backend.tools.durable_demo:create_durable_demo_app --factory --host 127.0.0.1 --port 8000 --env-file .env --no-access-log --no-proxy-headers
```

Startup checks that all three business tables are readable. A missing DSN, missing
migration or unavailable database stops startup with a safe error. There is no
in-memory fallback or automatic migration. Startup does not call a provider.

Use the existing frontend in another PowerShell terminal:

```powershell
npm --prefix frontend run dev
```

Open `http://127.0.0.1:5173/`. Existing external-processing configuration and explicit
browser consent are still required for an extraction. Starting this application
does not authorize any document for external processing. The launcher reuses the
existing local-demo adapter settings, including one SDK attempt, zero adapter
retries and its existing timeout; isolated experiments retain their own settings.
Do not run an extraction as part of offline setup verification.

The same frontend upload, review/save, form-switching and XLSX endpoints now use
PostgreSQL. The general `/api/v1/extractions` endpoint remains stateless; persistence
in this slice belongs to `/api/v1/demo-runs` under this explicit launcher.

## Reopening and read APIs

All routes retain the existing loopback peer/Host, exact Origin and
`X-Local-Demo: 1` requirements. There is no permissive CORS. Opaque run IDs are
sensitive local access capabilities. Review additionally checks the bound token.

- `GET /api/v1/demo-runs/{run_id}`: current run and first form.
- `GET /api/v1/demo-runs/{run_id}?form_id=...`: a particular persisted form/revision.
- `GET /api/v1/demo-runs/{run_id}/audit`: all persisted audit decisions; optional
  `form_id` limits the response to one form.
- `GET /api/v1/demo-runs/{run_id}/source`: private original, attachment/no-store.
- Existing review POST and `GET .../export.xlsx` operate on persisted state.

For a saved run ID, a local read after stopping/restarting the backend is:

```powershell
$businessRunId = Read-Host 'Saved run ID'
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/demo-runs/$businessRunId" -Headers @{'X-Local-Demo'='1'}
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/demo-runs/$businessRunId/audit" -Headers @{'X-Local-Demo'='1'}
```

These reads/export never invoke Gemini or renew consent. The React app now has a
Hebrew RTL [saved-records screen](records_screen.md), including search, filtering,
pagination and reopening an existing run/form with its persisted source preview.
Reopening does not resubmit earlier decisions. Supported corrections can be made
after approval; each requires a new explicit action/reason and appends history.

## Tests and verification limits

The original preparation run had 48 skipped PostgreSQL cases. That blocker was
subsequently resolved: all **48 original real PostgreSQL integration cases** were
rerun successfully, including the actual restart/reopen acceptance test. The
records-screen targeted run passed **75 tests**: those 48 cases, 15 new real
PostgreSQL search/reopen cases and 12 list API unit cases. The final full suite
passed **861 tests with zero skips**, including all 63 real PostgreSQL cases.
Frontend **12 tests**, TypeScript checking and production build passed. Tests used only the explicitly authorized local
`review_test_viral_lab` database, with random disposable schemas; the main
application database was not used for destructive tests.

Offline unit/regression commands (no database or provider required):

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider tests/test_business_storage.py tests/test_demo_api.py tests/test_business_review.py tests/test_excel_export.py
npm --prefix frontend test
npm --prefix frontend run typecheck
```

Real PostgreSQL tests require an explicitly disposable **local** `review_test_*`
database. The existing harness migrates/drops only its freshly generated schema:

```powershell
$env:REVIEW_TEST_DSN = [System.Net.NetworkCredential]::new('', (Read-Host 'Disposable local review_test_* PostgreSQL DSN' -AsSecureString)).Password
$env:REVIEW_TEST_ALLOW_DISPOSABLE = 'yes'
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider tests/integration/test_business_repository.py tests/integration/test_review_repository.py
```

The restart test uploads two complete synthetic forms through ASGI with a mocked
provider, applies fourteen decisions (including a correction and acknowledged
missing ID), stops/recreates the app/repository and connections, and compares
both forms, audit, revisions, originals and byte-identical XLSX. It checks that
reopening/export/replaying decisions makes no further provider call. Other real
PostgreSQL cases cover concurrent workers, rollback after event insert, stale or
conflicting commands, invalid batches, immutable-source/audit guards and corrupt
projections. Skipped PostgreSQL tests are not proof of database correctness.

If a process dies during extraction, or the provider returns but the completion
commit fails, the already stored document remains `processing` without a business
record. Reopening never retries extraction. Durable jobs/recovery and manually
authorized reconciliation are outside this slice; do not reset/retry such a run
automatically. A response not yet committed cannot be recovered from memory after
process loss.

Rollback of the application: stop the durable backend with Ctrl+C and start the
previous launcher if needed. Retain the database and this migration; the deliberate
non-destructive downgrade guard requires an explicit archival plan before removing
persisted originals or audit history. Clearing `REVIEW_DATABASE_DSN` from the shell
does not delete data:

```powershell
Remove-Item Env:REVIEW_DATABASE_DSN -ErrorAction SilentlyContinue
```
