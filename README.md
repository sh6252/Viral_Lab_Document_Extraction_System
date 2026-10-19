# Viral_Lab_Document_Extraction_System

Central Virology Laboratory document extraction system.

## Python Development Setup

The project contains evaluation tooling, policy-gated Gemini extraction, canonical contracts, an isolated PostgreSQL OCR-review boundary, a local seven-field review/XLSX tool, and an explicitly enabled local React/FastAPI upload-review-download demo. The original demo remains bounded and non-durable. A separate opt-in [durable PostgreSQL launcher](docs/business_persistence.md) implements business-document, seven-field record and audit persistence with a [saved-records search and reopen screen](docs/records_screen.md). Durable jobs, production authentication and runtime OCR remain unimplemented. Offline tests do not establish live provider availability or extraction accuracy.

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
- No runtime page rendering/OCR, extraction persistence or worker queue. The separate local review/export tool described below is not wired to this endpoint.
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

## Local seven-field review and Excel export (Task 11B)

This operator-only tool consumes an EXISTING canonical snapshot and explicit human
actions. It never calls Gemini/OCR, starts a server, changes canonical extraction
or uses the OCR review tables. All seven fields are reviewable. Source/result files,
actions, workbook and audit bundle contain sensitive data: use a private local
directory such as the already ignored `exports/` or `storage/`, outside public
static directories. The tool supplies no authentication or filesystem ACL setup.
No directory under `evaluation/artifacts/` is chosen as implicit runtime storage.

The single new spreadsheet dependency is **XlsxWriter**, measured installed version
**3.2.9**, declared `>=3.2.9,<4`. Its [official package metadata](https://github.com/jmcnamara/XlsxWriter/blob/main/setup.py)
explicitly lists Python 3.14. It is pure Python and uses only standard-library
dependencies. No other package was installed/upgraded for this slice. Tests reopen
the actual ZIP/XML workbook using Python's standard library; no second spreadsheet
library, pandas or OCR dependency is needed. XlsxWriter itself is write-only.

### Operator inputs and command

Create these private inputs explicitly:

1. `snapshot.json`: a standalone canonical `ExtractionResult` object, not a whole
   evaluation run wrapper or ground truth. Preserve actual provider form IDs.
2. `source.json`: source path plus the binding object below. Relative source paths
   are resolved against this metadata file's directory. Source bytes are hashed
   locally and must match `source_sha256`; they are not transmitted or copied.
3. `actions.json`: an array of explicit field review actions, or `[]` to export all
   forms unresolved. The operator must inspect the source; the command does not
   infer actions from the truth, provider confidence or OCR.
4. Optional `failures.json`: an array of safe processing-failure metadata objects.
   Omit the file or use `[]` when none exists. Failures never manufacture form rows.

```powershell
.\.venv\Scripts\python.exe -B -m backend.tools.review_export `
  --snapshot .\storage\private-run\snapshot.json `
  --source .\storage\private-run\source.json `
  --actions .\storage\private-run\actions.json `
  --output .\exports\reviewed.xlsx
# Optional: --failures .\storage\private-run\failures.json
# Optional: --bundle .\exports\reviewed.review.json
```

Parents must exist; outputs must be NEW. The default mandatory private result/audit
bundle is beside the workbook with suffix `.review.json`. Existing input/output
files are never overwritten. Validation precedes writing; an ordinary write error
removes only this invocation's newly created outputs. A process/power failure during
the two file writes can leave a partial pair: there is no durable transaction.

Successful stdout contains only counts, outcome and workbook SHA-256, never field
values, source paths, operator/reason text or document IDs. Errors give the generic
code `REVIEW_EXPORT_FAILED` and nonzero exit; detailed Pydantic/OS input echoes are
not printed. Inspect the private files locally rather than adding sensitive logs.

### Binding format

Example shape (replace placeholders with locally computed values):

```json
{
  "source_path": "source.pdf",
  "binding": {
    "document_id": "local-document-1",
    "source_sha256": "<64 lowercase SHA-256 hex characters of source bytes>",
    "extraction_id": "<actual extraction_id>",
    "canonical_snapshot_sha256": "<deterministic canonical snapshot hash>",
    "data_origin": "historical_replay",
    "snapshot_kind": "preserved",
    "prompt_version": null,
    "pipeline_version": null,
    "relationship_status": "unresolved"
  }
}
```

For a preserved historical snapshot BOTH `snapshot_kind="preserved"` and
`data_origin="historical_replay"` are required. A live input uses BOTH `"live"`.
Missing/inconsistent labels fail; no origin is inferred from a filename or current
environment. These are operator declarations, not proof that an arbitrary input
actually came from a live provider. Known prompt/pipeline versions should be supplied
from the original attempt; unavailable metadata stays null, never a guessed default.
Provider/model come from the canonical snapshot. Identifier relationships are not
inferred or approved by this slice and remain `unresolved`.

Compute the canonical binding hash using
`backend.services.business_review.snapshot_sha256(extraction_result)` and the
source hash using `hashlib.sha256(source_bytes).hexdigest()`. Snapshot encoding is
`canonical-json-v1`: all `model_dump(mode="json", warnings="error")` fields/nulls,
sorted object keys, compact separators, exact Unicode and list order, no NaN, UTF-8.
It is NOT the hash of arbitrary whitespace in `snapshot.json`. The detached exact
canonical text is retained in the private bundle. A hash binds declared content;
it does not authenticate a reviewer or prove source-to-result association by itself.

### Review action format and semantics

Each action binds the exact source hash, extraction ID, canonical snapshot hash,
form ID, field, original provider values/status, decision ID, revision, operator
declaration, aware declared timestamp and reason. Example correction shape:

```json
[
  {
    "source_sha256": "<same source hash>",
    "extraction_id": "<same extraction_id>",
    "canonical_snapshot_sha256": "<same canonical hash>",
    "form_id": "<actual provider form_id>",
    "field_name": "first_name",
    "decision_id": "local-decision-1",
    "expected_revision": 0,
    "operator_declaration": "Local operator declaration (not authenticated)",
    "declared_at": "2026-10-17T10:00:00+03:00",
    "reason": "Inspected original patient-name field",
    "original": {
      "value_status": "unreadable",
      "review_status": "needs_review",
      "values": []
    },
    "action": "correct_and_approve",
    "reviewed_values": ["שם סינתטי"]
  }
]
```

The example's original state must match the actual snapshot. Use
`original_field(snapshot, form_id, field_name)` to obtain its detached immutable
representation. Original values are objects `{ "raw": "...", "normalized": null }`;
omitted fields use status `omitted`, null review status and `[]`. All strings retain
their exact content. Unknown JSON fields/duplicate keys, non-string values,
boolean revisions and naive/numeric timestamps are rejected.

- `approve_as_extracted`: reviewed list equals the WHOLE original present raw list,
  in order, including duplicates. No normalized substitution or partial approval.
- `correct_and_approve`: explicit nonblank list of exact operator-entered strings;
  scalar fields require one value, central/commitment fields allow many. An empty
  correction fails; use `acknowledge_blank`.
- `acknowledge_blank`: reviewed list `[]`; terminal human disposition, distinct
  from model missing/unreadable or a claim of source absence.
- `reject`: list `[]`, state `rejected_unresolved`; no trusted business value.
- `leave_unresolved`: state `unresolved`; optional tentative text is retained ONLY
  in audit, never promoted to business cells.

Per-form revisions start at zero and advance once per accepted action. Supply a
full ordered action log for the CLI, with sequential `expected_revision` for each
form; all forms are exported even if some have no actions. Decision IDs are unique
within each form. Duplicate IDs within one command fail. The pure
`apply_business_review(..., previous=result)` supports identical accepted replay
before revision checks and later explicit resolution; conflicting IDs, stale new
actions, wrong original values or source/form/snapshot mismatch fail atomically.
Old rejected/unresolved entries are retained. It is an in-memory/local-file
capability, not durable idempotency across independent CLI invocations.

`complete` means ALL seven fields have human-approved provider/corrected values or
an acknowledged blank. It is not model correctness or resolved identifier pairing.
No supplied provider approval/status auto-approves a business field. A field with
no action stays `unreviewed`; rejected/unresolved fields keep the record unresolved.
Both complete and unresolved records are exportable. Inspect all seven fields,
including present ones, rather than assuming a provider value is correct.

### Exact workbook contract

Exactly three sheets: **Forms**, **ProcessingFailures**, **ReviewAudit**.

Forms has one row per logical form, with first columns exactly `first_name`,
`last_name`, `israeli_id`, `central_numbers`, `sample_receipt_date`, `sender`,
`commitment_numbers`. Then: `document_id`, `form_id`, `extraction_id`, `source_sha256`,
`data_origin`, `record_review_status`, `unresolved_fields`, `relationship_status`,
`provider`, `model`, `prompt_version`, `pipeline_version`, `review_revision`; followed
by `<field>_value_status`, `<field>_review_status`, `<field>_value_source` for each
of the seven fields in that order. Unresolved fields are a JSON list.

Only explicitly approved values enter business cells. Unreviewed/unresolved/rejected
scalar cells are blank, and central/commitment cells are `[]` until approved. This
is deliberately stricter than the initial Task 11A option to display unverified
provider candidates in business cells. Original candidates remain recoverable in
the bound result bundle and in audit for every explicit action. Approved multi-value
fields are compact JSON arrays of exact strings in ONE text cell; no deduplication,
reordering, numeric conversion or Cartesian-product rows. Missing, unreadable,
ambiguous, omitted, rejected and acknowledged-blank states remain distinguishable.

ProcessingFailures columns: `document_id`, `attempt_reference`, `source_sha256`,
`provider`, `model`, `failure_code`, `retryable`, `attempt_count`, `latency_seconds`,
`data_origin`. Only declared failures appear there; no fake Forms row. Optional
failure input uses those exact keys, with null for unavailable metadata. The error
code is a safe uppercase category, not raw provider text. Attempts/latency are not
invented when absent. A pure failure-only workbook is supported by the exporter;
the operator command requires a valid successful snapshot as its main input.

ReviewAudit has one row per accepted action, not per replay: document/source/
canonical-snapshot/extraction/form/field identity, original raw/normalized lists and
value/review states, decision ID/action/reviewed list, declared operator/time/reason,
old/new review state and previous/new revision. Exact original canonical text,
evidence, actions, records, failure metadata and workbook hash remain in the
mandatory private JSON bundle. No OCR information is implicitly imported.

Every cell is explicit text (`write_string`, text format); formula/URL/number
autodetection is disabled. `=`, `+`, `-`, `@`, whitespace, tabs and newlines remain
literal text without display prefixes, formulas, hyperlinks or external links.
OOXML escaping preserves literal escape-looking strings. Invalid XML control
characters, unpaired surrogates and cells exceeding 32,767 UTF-16 units fail safely
BEFORE output, including long JSON arrays/audit cells. No silent truncation or
normalization occurs. Files and summaries are deterministic for identical inputs.

The CLI itself has no browser UI or provider/OCR invocation. Task 11C below adds a
separate local web demo; neither path provides durable business-review storage,
production operator authentication, automatic acceptance or identifier pairing.
The CLI operator must bind the correct source/snapshot and inspect it independently.

## Local upload, review and download demo (Task 11C)

This is an opt-in, single-process **LOCAL/PRIVATE** hackathon demo. Ordinary
`backend.main:app` does not register the demo routes. Existing extraction and
authenticated OCR-review endpoints/contracts remain unchanged. The demo has no
database, queue, filesystem artifact store or production login/ACL integration.
Runs and source bytes exist only in process memory, never `evaluation/artifacts/`.

### Exact manual demo sequence

1. In the repository root, activate the existing Python environment. Configure the
   already-approved Gemini settings locally in `.env`: provider selection `gemini`,
   the intended `GEMINI_MODEL`, key and existing outbound/provider-policy gates.
   Do not paste keys into chat or commit `.env`. No setting is changed by this task.
2. Start the explicit backend on loopback, with access logging/proxy trust disabled:

   ```powershell
   .\.venv\Scripts\python.exe -B -m uvicorn backend.tools.local_demo:app `
     --host 127.0.0.1 --port 8000 --env-file .env --no-access-log --no-proxy-headers
   ```

   This command explicitly loads `.env`; ordinary `AppConfig` still does not load
   files. Outbound policy stays fail-closed. A key alone enables nothing. The demo
   launcher configures the EXISTING Gemini adapter with zero adapter retries and
   SDK attempts=1, using its injected client factory; no automatic request retries
   or provider fallback. Model/prompt/configuration are not overridden.
3. In another terminal, from the repository root:

   ```powershell
   npm --prefix frontend ci --no-audit --no-fund
   npm --prefix frontend run dev
   ```

   Open **http://127.0.0.1:5173/**. Vite binds only `127.0.0.1:5173` and proxies
   `/api` to `127.0.0.1:8000`; no wildcard CORS is installed. These exact ports/
   loopback origins are the supported setup. Do not expose either server publicly.
4. Choose a PDF, PNG or JPEG. Use synthetic data or an explicitly approved demo
   single-page copy. The full challenge source PDFs include pages that were NOT
   approved for transmission; their presence locally is not permission to send them.
5. Check the explicit external-processing permission, then click **Upload / Extract**.
   The server performs signature/size checks and reuses `DocumentInput`,
   `ExtractionRequest`, `ExtractionOrchestrator` and the existing adapter/policy.
   Loading is shown while the synchronous provider operation runs in a threadpool;
   this is not a durable job. The UI never retries extraction automatically.
6. Inspect the locally selected source beside all seven fields. The PDF/image
   preview is a browser object URL for that selected `File`, revoked when replaced
   or unmounted; no server filesystem path or source-serving endpoint is exposed.
   For each field inspect raw values, separately supplied normalized values and
   original value/review status. Choose an explicit disposition and enter a reason:
   approve the whole provider raw list, correct and approve, acknowledge blank,
   reject or leave unresolved. Enter an operator declaration (not authenticated).
   Click **Save explicit review actions**. No disposition is preselected.
7. If multiple forms returned, use the form selector. Each form has its own revision,
   review state and unsaved drafts. Every form remains in the run and export. List
   fields use one input row per exact value with add/remove controls, no delimiter
   splitting, deduplication or inferred identifier pairing.
8. Click **Download Excel** after successful extraction, even when some/all fields
   or forms remain unresolved. Unsaved choices are excluded; the UI asks explicitly
   before downloading saved state with pending choices. The existing Task 11B
   exporter writes all forms once plus the audit/failure sheets. It never approves
   a field because of download and makes no additional provider/OCR call.

Gemini can return 429/quota, 503 or 504. A failed run has a safe processing error,
zero logical business forms and no invented fields. **Download failure report**
produces only its separate ProcessingFailures row and sheet headers elsewhere.
No failed run is silently substituted with a cached success. Uploading again is
an explicit new attempt and may consume quota/cost; this task made no live calls.

### Server binding and local limits

- Cryptographically random 256-bit opaque run IDs bind original bytes/hash,
  immutable canonical snapshot/hash, extraction ID, actual provider form IDs and
  provider/model/prompt/pipeline metadata. A separate random review token binds
  each mutation to that run. Treat run IDs/tokens as sensitive local capabilities,
  not authenticated user identities. No listing endpoint or client canonical import.
- Demo routes require a loopback peer and Host, `X-Local-Demo: 1` and an absent or
  exact allowlisted local Origin. Foreign/cross-site browser requests are denied
  before input handling. These are local-demo controls, not production authentication.
  Do not enable proxy-header trust or network exposure to bypass them.
- Default bounds: eight runs, 64 MiB total retained source bytes, four MiB snapshot
  per run, at most 32 returned forms, four MiB aggregate audit representation per
  run and 100 accepted actions per form. Uploads retain the existing 20 MiB default
  file limit. Framework multipart parsing may temporarily spool uploads; that is
  not a new durable source/artifact store or a full ingress resource-defense layer.
- Fixed lifetime is 30 minutes from run creation, not extended by reads/reviews.
  Expired entries are inaccessible and lazily pruned on the next operation; restart
  loses all runs. Capacity exhaustion fails explicitly instead of silently evicting
  active work. There is no resume, background cleanup worker or durable replay.
- Source/result/original field bindings come ONLY from server-owned run state.
  JSON commands contain per-field action/value/reason/decision ID plus declared
  operator/time, revision and review token. Browser-supplied canonical/provenance
  fields are forbidden. Existing pure business review owns all approval rules.
- In-process locking makes a form command atomic; exact decision replay precedes
  revision checking. The UI retains the same IDs/content/time after an ambiguous
  review network failure and offers **Retry SAME review request** without regenerating
  it. Stale/conflicting new commands fail. This is not restart-safe idempotency.
- Every handled response/error is `Cache-Control: no-store`; source/private values
  are absent from filenames and safe error bodies. Export uses the XLSX MIME type,
  fixed attachment filename `reviewed-demo.xlsx`, private in-memory generation and
  the existing literal-text spreadsheet protections. Nothing is written publicly.

### Routes and selected-form projection

Enabled only through `create_app(..., demo_workflow=...)` or the local entry point:

- `POST /api/v1/demo-runs`: multipart `file`, `allow_external_processing`, optional
  `locale`. HTTP 201 means a run was created; inspect `status` for `succeeded` versus
  `failed`. Invalid uploads return safe 400/413/415; no fake extracted record.
- `GET /api/v1/demo-runs/{run_id}?form_id=...`: run status/origin/metadata, all form
  IDs, review token and ONE selected form with seven allowlisted field projections.
  It returns no full canonical snapshot, source bytes, arbitrary evidence locator
  or filesystem path. The first form is initially displayed, not exclusively exported.
- `POST /api/v1/demo-runs/{run_id}/forms/{form_id}/review`: strict command with
  `review_token`, `expected_revision`, `operator_declaration`, aware ISO `declared_at`
  and up to seven `actions` containing `field_name`, stable `decision_id`, `action`,
  exact `reviewed_values` array and `reason`. Reuse original command IDs/content/time
  on a retry. Server supplies every source/snapshot/original-field binding.
- `GET /api/v1/demo-runs/{run_id}/export.xlsx`: all forms, including unresolved,
  or a separate failure-only workbook. No extraction or approval side effect.

### Historical replay and validation limits

Historical replay remains **CLI-only** through Task 11B's explicitly supplied source,
snapshot and actions, labeled `snapshot_kind="preserved"` and
`data_origin="historical_replay"`. A safe HTTP fixture catalog/import would require
another private ingestion/provenance boundary; it is deliberately omitted here.
The UI labels its runs **LIVE** and never accepts a browser replay/snapshot path.
CLI output stays clearly historical in the workbook. There is no hidden fallback.

Frontend uses React/React DOM **19.3.1**, TypeScript **5.9.3**, Vite **7.3.7** and
React type declarations **19.3.0**, pinned by `frontend/package-lock.json`. Verified
with Node **24.14.0** / npm **11.4.2**. No large UI framework, frontend test library
or new Python dependency was added. Development dependencies remain in frontend.

```powershell
npm --prefix frontend test
npm --prefix frontend run typecheck
npm --prefix frontend run build
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider tests/test_demo_api.py
```

Node's built-in runner tests request guards, exact action/list construction,
non-approval defaults and blank-state preservation. Backend HTTP tests exercise
the full synthetic upload/review/export path and reopen real workbooks. No real
Gemini/OCR call is part of tests. Browser UI automation could not run because no
browser surface was available; visual/manual interaction is not claimed verified.
The manual sequence above remains the operator acceptance check. Production auth,
durability, real-document accuracy and public deployment are NOT delivered here.

Task 11C validation: **25** synthetic demo API tests; **134** focused demo/business/
Excel/extraction checks passed. Full backend regression: **603 passed**, including
the real disposable PostgreSQL tests without skips, with **530** dependency
deprecation warnings. Frontend: **5** Node tests passed; strict TypeScript typecheck
and Vite production build passed. `pip check`, AST/import, whitespace/credential
marker and `git diff --check` checks passed. All **115** protected-file hashes
matched the pre-task state, including Task 11B logic, adapter/orchestrator/canonical,
prompt/scorer/truth, source PDFs, OCR/review/persistence/auth components and Python
dependencies. Only authorized `README.md`/`backend/main.py` edits were excluded.
The temporary test database/credentials and Vite test process were stopped/removed.
No package was added to Python, no real AI/OCR call was made and nothing was staged
or committed. Prior uncommitted Task 11B work and source PDFs were preserved.
