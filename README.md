# Viral_Lab_Document_Extraction_System

Central Virology Laboratory document extraction system.

## Python Development Setup

The project currently contains the evaluation foundation, FastAPI backend foundation, domain contracts, a policy-gated Gemini extraction adapter, a shared provider factory, and a minimal document extraction API orchestration path. Database, frontend, OCR, permanent storage, background jobs, authentication, and Excel export are not implemented yet. Provider integration is verified with offline synthetic tests; live provider availability and model accuracy require separate evaluation.

### Requirements

- Python `>=3.12,<3.15`
- Windows PowerShell

Python 3.12 or 3.13 are recommended for the broadest dependency compatibility. Python 3.14 is allowed by the project metadata and should be verified with the test suite after dependency installation.

### Create a Virtual Environment

From the repository root:

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Do not commit `.venv/`, `.env`, uploaded documents, generated exports, or real medical records.

### Run Tests

```powershell
python -m unittest discover -v
python -m pytest -q
python -m pip check
```

### Run the Backend

The backend is intended for local development only at this stage. Do not expose it on a network without authentication, authorization, upload limits, audit logging, and production storage controls.

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn backend.main:app --reload --host 127.0.0.1
```

Health check:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
```

Expected response:

```json
{
  "status": "healthy",
  "service": "Virology Lab IDP",
  "version": "0.1.0",
  "external_ai_enabled": false
}
```

### Extraction Endpoint

`POST /api/v1/extractions` accepts one multipart field named `file`.

Supported document signatures:

- PDF: `application/pdf`
- JPEG: `image/jpeg`
- PNG: `image/png`

The current default `DEFAULT_EXTRACTION_ADAPTER=local_ocr` has no production adapter implementation yet, so the endpoint returns a safe provider-unavailable error unless an adapter is injected by tests or a real adapter is explicitly configured. Automated tests use a fake adapter and do not call external AI.

Synthetic PowerShell request against a locally running app:

```powershell
$bytes = [System.Text.Encoding]::ASCII.GetBytes("%PDF-1.7`n% synthetic`n")
[System.IO.File]::WriteAllBytes("$PWD\synthetic.pdf", $bytes)
Invoke-RestMethod `
  -Uri http://127.0.0.1:8000/api/v1/extractions `
  -Method Post `
  -Form @{ file = Get-Item "$PWD\synthetic.pdf" }
Remove-Item "$PWD\synthetic.pdf"
```

The request keeps bytes in memory for this MVP and does not persist uploads. The endpoint validates actual file signatures, rejects empty, oversized, unsupported, and spoofed uploads, and returns canonical `ExtractionResult` JSON when an adapter succeeds.

### Environment Configuration

Copy `.env.example` to `.env` for local development if you need environment overrides.

External AI is disabled by default:

```powershell
AI_OUTBOUND_ENABLED=false
ALLOW_EXTERNAL_PHI=false
APPROVED_AI_PROVIDERS=
DEFAULT_EXTRACTION_ADAPTER=local_ocr
MAX_UPLOAD_BYTES=20971520
EXTRACTION_PIPELINE_VERSION=api-v1
GEMINI_API_KEY=
GEMINI_MODEL=gemini-2.5-flash
```

Gemini extraction remains disabled unless `AI_OUTBOUND_ENABLED=true`, `ALLOW_EXTERNAL_PHI=true`, `APPROVED_AI_PROVIDERS` includes `gemini`, the request-level provider policy permits `external_phi`, and `GEMINI_API_KEY` is set. Unit tests use a fake Gemini client and do not make real external AI calls.

Select the live provider with `DEFAULT_EXTRACTION_ADAPTER=gemini` through `backend/services/extraction/registry.py`; HTTP request and response contracts stay the same. A configured unknown provider fails application construction explicitly. The safe default remains `local_ocr` (not an implemented OCR engine), with external access disabled. `.env` files are not loaded automatically: export the variables in the process environment or configure your launcher to load them.

See [provider integration](docs/provider_integration.md) for extension points, offline validation, and remaining limitations.

Current extraction limitations:

- No authentication or access control yet.
- No permanent document storage.
- No page rendering, OCR, database persistence, worker queue, or Excel export yet.
- Multi-form results are preserved when an adapter returns them, but this endpoint does not perform form segmentation.
- Real Gemini smoke testing requires explicit configuration and approval; it is never enabled by the presence of an API key alone.

Offline extraction integration tests:

```powershell
python -m pytest tests/test_extraction_api.py -q
```

### Controlled Extraction Smoke Test

The repository includes one synthetic development fixture:

- `evaluation/fixtures/dev/smoke/synthetic_hebrew_commitment.pdf`
- `evaluation/fixtures/dev/smoke/synthetic_hebrew_commitment.ground_truth.json`

The fixture contains fictional Hebrew commitment-form data only: a name, an Israeli ID with leading zeros, multiple central laboratory numbers, a sample receipt date marked as received, a synthetic sending organization, and multiple commitment numbers.

Run the default offline smoke test:

```powershell
python -m backend.tools.extraction_smoke
```

Run the offline needs-review scenario:

```powershell
python -m backend.tools.extraction_smoke --mock-scenario needs_review
```

The smoke report is JSON and includes the document name, provider, duration, schema-validation status, required fields, expected values, actual values, value status, review status, field-level differences, multi-value precision/recall/F1 from the existing evaluator, and safe errors or warnings.

Use redacted reporting when testing any non-synthetic document:

```powershell
python -m backend.tools.extraction_smoke --document .\path\to\demo.pdf --ground-truth .\path\to\truth.json --redact
```

Future authorized live Gemini smoke test command, not to be run without explicit approval:

```powershell
$env:AI_OUTBOUND_ENABLED="true"
$env:ALLOW_EXTERNAL_PHI="true"
$env:APPROVED_AI_PROVIDERS="gemini"
$env:DEFAULT_EXTRACTION_ADAPTER="gemini"
$env:GEMINI_API_KEY="<approved-demo-key>"
python -m backend.tools.extraction_smoke `
  --document evaluation\fixtures\dev\smoke\synthetic_hebrew_commitment.pdf `
  --ground-truth evaluation\fixtures\dev\smoke\synthetic_hebrew_commitment.ground_truth.json `
  --provider gemini `
  --authorize-external-processing `
  --redact
```

Live mode still uses the adapter's fail-closed provider policy. The explicit CLI flags do not override server-side restrictions, and an API key alone is never sufficient. Expected API cost is not pinned in this repository; check the current Gemini pricing for the selected model before any live run. A single PDF smoke test may consume input document tokens plus output tokens.

The default remains `--provider offline_mock`. Live selection uses `--provider gemini` with explicit authorization and the existing provider policy. `--live-gemini` is retained as a deprecated alias and cannot be combined with a different provider.

### Troubleshooting

If PowerShell blocks activation, run:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

If `pip install -e ".[dev]"` fails because of Python compatibility, install a supported Python version in the `>=3.12,<3.15` range and recreate `.venv`.

If dependency installation fails because of network access, retry from an environment with package-index access.

If `pytest` is not found, activate `.venv` and rerun `python -m pip install -e ".[dev]"`.

If `uvicorn` is not found, activate `.venv` and rerun `python -m pip install -e ".[dev]"`.

## Internal review persistence (Task 10H)

This PostgreSQL slice is constructed only through explicit injection; startup does
not create a DB connection. Task 10J adds fail-closed review routes as described
below, without a production authenticator, OCR/providers or Excel. The existing
pure workflow remains authoritative for decision rules; approved OCR values never
overwrite canonical extraction. See [persistence design](docs/review_persistence_design.md).

Validated on Python 3.14.2/Windows with SQLAlchemy 2.0.55, psycopg and psycopg-binary
3.3.6, and Alembic 1.20.0. Added transitive versions: greenlet 3.5.6, Mako 1.4.3,
MarkupSafe 3.0.4 and tzdata 2026.5. No unrelated packages were upgraded. Pytest's
existing facilities suffice; no new integration-test framework or SQLite fallback.

Construct an engine explicitly via `create_review_engine(dsn)`, then a repository
and `ReviewPersistenceService`. These objects do not connect at import time. Supply
trusted document/form-scoped context from authenticated runtime when it eventually
exists; constructing `TrustedReviewContext` itself does not authenticate a person.
The database role should have only required permissions; separate migration/admin
credentials from application credentials in deployment. Schema triggers also reject
event/item mutations, immutable preparation changes and ordinary session deletion.

Migration requires a separately supplied PostgreSQL DSN; nothing reads `.env`
automatically or changes existing application configuration:

```powershell
# Set locally; never paste credentials into chat or commit them.
$env:REVIEW_DATABASE_DSN = '<explicit PostgreSQL DSN>'
.\.venv\Scripts\python.exe -B -m alembic -c alembic.ini upgrade head
```

Migration `0001_review_persistence` creates only the three review tables, constraints,
index and audit guards (plus Alembic's version table). It contains static DDL,
independent of future model edits. Downgrade deliberately refuses audit deletion;
production deletion/recovery needs a separately approved lifecycle procedure.

### Isolated PostgreSQL tests

Use a disposable local PostgreSQL instance/database. For example, start a dedicated
container from `postgres:16-alpine` with a locally generated password, database
`review_test_lab`, and a port bound only to `127.0.0.1`. Do not reuse an application
database. Supply its DSN only through the environment:

```powershell
$env:REVIEW_TEST_DSN = '<local PostgreSQL DSN for review_test_lab>'
$env:REVIEW_TEST_ALLOW_DISPOSABLE = 'yes'
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider tests/integration
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
```

The harness requires loopback host, database matching `review_test_[a-z0-9_]+`,
PostgreSQL DSN with no extra query options, and explicit disposable acknowledgement.
Each test creates/migrates a unique `review_test_<random>` schema, then drops ONLY
that generated schema. It never drops/resets a database. The test role must own
this dedicated test database/schema and may create functions/triggers. Without a
test DSN, PostgreSQL tests skip explicitly; those skips are not DB validation.
An unsafe configured DSN fails rather than silently skipping or connecting.

Ordinary unit tests need no DB. For a complete persistence validation, use the
configured integration/full-suite commands above. Repository errors hide SQL
parameters; never enable SQL debug logging or log DSNs, snapshots or decision text.
Private source files are not stored as image/PDF bytes in these tables. Stable
runtime artifact storage, authentication, retention policy and explicit re-review
supersession remain separate work; the current one-decision gate is unchanged.

## Fail-closed review API boundary (Task 10J)

The application registers only these new operations:

- `GET /api/v1/review-sessions/{session_id}`
- `POST /api/v1/review-sessions/{session_id}/decisions`

Review access fails closed in the ordinary application: no injected authenticator
returns **401**, no authorization policy grants no resource visibility, and no
missing dependency creates anonymous or default access. No identity provider,
login/user store, ACL store or header-based production auth was implemented.
Existing extraction and health behavior is unchanged.

An explicitly configured runtime/test caller supplies `review_api_service`,
`review_authenticator` and `review_authorization_policy` to `create_app`. The
authenticator must independently verify credentials; a broad role or unverified
header is not a document/form grant. The policy checks the exact principal and
session/document/form for read, submit and source operations separately. Read-only
requests never construct a writable `TrustedReviewContext`. No DB engine is
implicitly opened, and no API operation invokes OCR, Gemini, merge or export.

GET returns a selected-form DTO with actionable values, issue indicators and
server-derived capabilities, excluding full canonical snapshots, codec data,
other forms, trusted principal identity and storage paths. POST uses strict DTOs;
human `reviewer_reference` and `declared_reviewed_at` remain untrusted metadata.
Clients keep decision ID/content/declared time stable across retries. The existing
durable gate still determines replay, revisions, approval and actor attribution.
Responses and review-specific sanitized errors use `Cache-Control: no-store`.

Artifact access is descriptor-only. Without an explicitly injected trusted resolver,
evidence is unavailable; there is no artifact streaming route, evaluation-path
resolver, public URL or signed URL implementation. Shared-page bindings are refused
by this slice. No new dependencies, UI or canonical field mutations were added.

See [review API design and implementation record](docs/review_api_design.md) for
DTOs, error mapping, privacy boundaries and validation. Synthetic injected adapters
exist only in tests and must not be deployed as an authenticator. Production
identity/ACL configuration, source storage, ASGI/proxy access-log redaction and
deployment security remain prerequisites for any public rollout.
