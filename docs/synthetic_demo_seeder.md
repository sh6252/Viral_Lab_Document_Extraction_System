# Synthetic business demo: guarded seeder and offline alternative

The current implementation **cannot safely seed the durable database** with
accurate non-live source provenance without changes to shared production logic.
`backend.tools.seed_synthetic_demo` therefore fails closed before opening any
database connection. `--confirm-write` does not override this blocker.

## Why database seeding is blocked

- `DemoWorkflow.create` (inherited by `DurableWorkflow`) hardcodes the source
  binding to `data_origin="live", snapshot_kind="live"` and records the extraction
  prompt version even for an injected offline adapter.
- `BusinessRepository._load` and `list_records` require that live/live pair.
- `SourceBinding` supports preserved/historical replay, but no `synthetic` or
  `mock` origin. The React upload view displays `LIVE` for a fresh extraction.
- Provider metadata `mode="mock"` alone does not resolve those source/UI labels.

The tool does not monkeypatch these checks, invent a new origin, update database
rows, fabricate a Gemini response, or create human approval/audit events.
There is deliberately no dormant database write implementation to enable by
removing a CLI check.

## Commands (PowerShell, repository root)

Database configuration is read only from the process `REVIEW_DATABASE_DSN`, or
from an explicitly selected `--env-file`. The file is never sourced, interpolated
or modified; no other settings are loaded. There is no automatic `.env` loading.

```powershell
.\.venv\Scripts\python.exe -B -m backend.tools.seed_synthetic_demo --env-file .env --preflight
```

Expected: exit **2**, `SYNTHETIC_DURABLE_PROVENANCE_UNSUPPORTED`, no connection or
record. The only acceptable target is PostgreSQL/psycopg at **127.0.0.1:5433**, DB
**viral_lab**, with an explicit username and no URL query parameters. Test DBs,
remote hosts, alternative host names, ports, drivers and missing settings fail
closed. Diagnostics never print a DSN, password or username.

The explicit write request below is documented for clarity, **not recommended
as a way to seed today**. It still exits 2 before connecting:

```powershell
.\.venv\Scripts\python.exe -B -m backend.tools.seed_synthetic_demo --env-file .env --confirm-write
```

## Supported alternative: isolated file-only replay

This does **not** test the PostgreSQL Records screen or persistence across a
backend restart. It tests the existing canonical review/export domain and
operator file workflow without pretending it is a durable live record.

Create a new directory under ignored private storage (no database settings used):

```powershell
.\.venv\Scripts\python.exe -B -m backend.tools.seed_synthetic_demo --offline-output .\storage\synthetic-business-demo-001
```

This creates a watermarked Hebrew one-page `synthetic.pdf`, `snapshot.json`,
`source.json`, an **empty** `actions.json`, and a SHA-256 `manifest.json`.
The PDF visibly says **נתונים סינתטיים להדגמה בלבד**, with an English offline/mock
warning. It contains exactly Sarah Cohen / `012345678`, central numbers `803215`
and `803216`, receipt date `18.08.2026`, sender Leumit, and distinct commitments
`456001`, `456002`, `456003`. Names and sender are in Hebrew in the actual PDF.

The existing `OfflineSmokeExtractionAdapter` generates the snapshot under a
`local_only` request policy. Provider name is `offline_smoke_mock`, mode `mock`,
version `offline-perfect`. No AI prompt is executed (`prompt_version=null`). The
file binding is accurately `historical_replay` / `preserved`: a locally prepared
snapshot for subsequent replay. There is one form, revision zero, seven
unreviewed fields and zero human decisions. Source bytes and canonical UTF-8
snapshot have independent SHA-256 hashes.

Every output directory must be new. Existing directories, including partial
outputs from an interrupted operation, are refused; no cleanup or overwrite is
performed. Keep generated files private and out of Git.

An optional **unreviewed** export demonstrates truthful pending state:

```powershell
.\.venv\Scripts\python.exe -B -m backend.tools.review_export --snapshot .\storage\synthetic-business-demo-001\snapshot.json --source .\storage\synthetic-business-demo-001\source.json --actions .\storage\synthetic-business-demo-001\actions.json --output .\storage\synthetic-business-demo-001\unreviewed.xlsx
```

It does not approve provider values. To export approved business values, an
operator must inspect the PDF and supply seven explicit actions in a **separate**
JSON action file conforming to `ActionInput` in `backend.tools.review_export`:
bound source/extraction/snapshot hashes, form ID, field/original values, unique
decision ID, expected revision, operator declaration, aware timestamp, reason,
action and reviewed strings. The seeder intentionally does not populate these.
Use a new output name for every export. Seven explicit approvals give one
complete logical form and seven audit events; current commitment-level export
produces three Excel business rows, with the two central numbers on each row.

## Existing frontend

No record is inserted, so there is nothing new to find in **רשומות שמורות**.
Uploading this PDF through the normal live upload route is **not** an offline
alternative and may invoke the configured provider. Do not do that for this task.
The existing frontend cannot import this replay bundle. Testing Records → Reopen
→ Review against PostgreSQL requires separately authorized shared provenance/UI
support, which this task explicitly prohibits changing.

## Offline validation

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider tests/test_seed_synthetic_demo.py tests/test_business_review.py tests/test_review_export.py tests/test_excel_export.py tests/test_extraction_smoke.py tests/test_commitment_export_compatibility.py tests/test_demo_api.py
```

The new tests prohibit network connections, Gemini, database engine creation and
durable writes. Temporary fixture creation is confined to pytest-owned paths.
No migration or PostgreSQL integration test is needed or executed for this
fail-closed, file-only implementation. No production readiness claim is made.

Executed offline on 2026-10-20: **205 passed**, including **45** new safety/fixture
tests; only dependency deprecation warnings. Python AST syntax checks and
whitespace checks passed. Ruff and mypy are not installed, so those checks were
not run and no dependencies were installed. The actual pytest run used a fresh
unique `--basetemp` under ignored `storage/` to avoid historical scratch paths.
The real CLI seeding operation was not executed; CLI calls in unit tests used
synthetic DSNs and database/network guards.
