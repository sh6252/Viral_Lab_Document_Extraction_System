"""Cloud upload correlation and allowlisted diagnostics; never log exception text."""
from contextvars import ContextVar
from dataclasses import dataclass, field
import json
import logging
from time import monotonic
from uuid import uuid4

from backend.services.extraction.errors import ExtractionAdapterError
from backend.services.extraction.provider_errors import SAFE_PROVIDER_STATUSES

LOGGER = logging.getLogger("uvicorn.error")


@dataclass
class UploadTrace:
    request_id: str = field(default_factory=lambda: uuid4().hex)
    run_id: str | None = None
    started: float = field(default_factory=monotonic)
    stage: str = "request"
    failed_stage: str | None = None
    code: str | None = None
    failure_kind: str | None = None
    provider_http_status: int | None = None
    provider_status: str | None = None
    timeout_phase: str | None = None
    http_status: int = 0

    def summary(self) -> dict:
        return {"request_id": self.request_id, "run_id": self.run_id,
            "stage": self.failed_stage or self.stage, "code": self.code,
            "status_class": f"{self.http_status // 100}xx" if self.http_status else "pending",
            "failure_kind": self.failure_kind, "provider_http_status": self.provider_http_status,
            "provider_status": self.provider_status, "timeout_phase": self.timeout_phase,
            "elapsed_ms": round((monotonic() - self.started) * 1000)}

    def log(self, event: str) -> None:
        LOGGER.info(json.dumps({"event": event, "active_stage": self.stage, **self.summary()}, separators=(",", ":")))


CURRENT: ContextVar[UploadTrace | None] = ContextVar("cloud_upload_trace", default=None)


def failure_status(code: str) -> int:
    return {"PROVIDER_RATE_LIMIT": 429, "PROVIDER_TIMEOUT": 504,
        "PROVIDER_NOT_AUTHORIZED": 403, "INVALID_PROVIDER_RESPONSE": 502,
        "EXTRACTION_VALIDATION_ERROR": 502, "INVALID_PROVIDER_REQUEST": 502,
        "DOCUMENT_TOO_LARGE": 413, "UNSUPPORTED_DOCUMENT_TYPE": 415}.get(code, 503)


def stage(name: str) -> None:
    if trace := CURRENT.get():
        trace.stage = name
        trace.log("cloud_upload_stage")


def failure(code: str, status: int, error: Exception | None = None) -> None:
    if trace := CURRENT.get():
        trace.code, trace.http_status, trace.failed_stage = code, status, trace.stage
        diagnostics = error.safe_diagnostics() if isinstance(error, ExtractionAdapterError) else {}
        http_status = diagnostics.get("http_status_code")
        trace.provider_http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        provider_status = diagnostics.get("provider_status")
        trace.provider_status = provider_status if isinstance(provider_status, str) and provider_status in SAFE_PROVIDER_STATUSES else None
        trace.timeout_phase = None
        if code == "PROVIDER_TIMEOUT":
            exception_class = diagnostics.get("exception_class")
            phases = {"ConnectTimeout": "connect", "ReadTimeout": "read", "WriteTimeout": "write", "PoolTimeout": "pool"}
            trace.timeout_phase = "provider_deadline" if trace.provider_http_status == 504 else (
                phases.get(exception_class, "unknown") if isinstance(exception_class, str) else "unknown")
        trace.failure_kind = ("database" if trace.stage.startswith("database") else
            "provider_http" if trace.provider_http_status else "client_timeout" if code == "PROVIDER_TIMEOUT" else
            "policy" if code in {"PROVIDER_NOT_AUTHORIZED", "MISSING_PROVIDER_CREDENTIALS"} else
            "provider" if code.startswith("PROVIDER_") else
            "provider_response" if code in {"INVALID_PROVIDER_REQUEST", "INVALID_PROVIDER_RESPONSE", "EXTRACTION_VALIDATION_ERROR"} else "backend")
        trace.log("cloud_upload_failed")


def observe_provider(name: str, error: Exception | None = None) -> None:
    if error is None:
        stage(name)
    else:
        code = error.code if isinstance(error, ExtractionAdapterError) else "CLOUD_BACKEND_UNEXPECTED"
        if name == "provider_client" and not isinstance(error, ExtractionAdapterError):
            code = "CLOUD_PROVIDER_CLIENT_INIT_FAILED"
        failure(code, failure_status(code), error)
