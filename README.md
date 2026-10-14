# Viral_Lab_Document_Extraction_System

Central Virology Laboratory document extraction system.

## Python Development Setup

The project currently contains the evaluation foundation, FastAPI backend foundation, domain contracts, policy-gated Gemini extraction adapter, and a minimal document extraction API orchestration path. Database, frontend, OCR, permanent storage, background jobs, authentication, and Excel export are not implemented yet.

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

### Troubleshooting

If PowerShell blocks activation, run:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

If `pip install -e ".[dev]"` fails because of Python compatibility, install a supported Python version in the `>=3.12,<3.15` range and recreate `.venv`.

If dependency installation fails because of network access, retry from an environment with package-index access.

If `pytest` is not found, activate `.venv` and rerun `python -m pip install -e ".[dev]"`.

If `uvicorn` is not found, activate `.venv` and rerun `python -m pip install -e ".[dev]"`.
