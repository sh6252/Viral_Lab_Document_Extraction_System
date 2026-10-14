# Viral_Lab_Document_Extraction_System

Central Virology Laboratory document extraction system.

## Python Development Setup

The project currently contains the evaluation foundation for the document extraction system. Backend, database, frontend, OCR, AI adapters, and Excel export are not implemented yet.

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

The current backend foundation exposes a FastAPI application without database, OCR, AI, upload, or export integrations.

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn backend.main:app --reload
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

### Environment Configuration

Copy `.env.example` to `.env` for local development if you need environment overrides.

External AI is disabled by default:

```powershell
AI_OUTBOUND_ENABLED=false
ALLOW_EXTERNAL_PHI=false
APPROVED_AI_PROVIDERS=
DEFAULT_EXTRACTION_ADAPTER=local_ocr
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
