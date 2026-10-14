"""Safe extraction error categories."""

from __future__ import annotations

from typing import Any

SafeDiagnostics = dict[str, str | int]


class ExtractionAdapterError(Exception):
    """Base class for adapter errors with safe public messages."""

    code = "EXTRACTION_ADAPTER_ERROR"
    retryable = False

    def __init__(
        self,
        safe_message: str,
        *,
        retryable: bool | None = None,
        diagnostics: SafeDiagnostics | None = None,
    ) -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message
        if retryable is not None:
            self.retryable = retryable
        self.diagnostics = diagnostics or {}

    def safe_diagnostics(self) -> dict[str, Any]:
        """Return diagnostics that are safe for internal tooling reports."""

        return dict(self.diagnostics)


class ProviderNotAuthorized(ExtractionAdapterError):
    code = "PROVIDER_NOT_AUTHORIZED"


class MissingProviderCredentials(ExtractionAdapterError):
    code = "MISSING_PROVIDER_CREDENTIALS"


class InvalidUpload(ExtractionAdapterError):
    code = "INVALID_UPLOAD"


class UnsupportedDocumentType(ExtractionAdapterError):
    code = "UNSUPPORTED_DOCUMENT_TYPE"


class DocumentTooLarge(ExtractionAdapterError):
    code = "DOCUMENT_TOO_LARGE"


class ProviderTimeout(ExtractionAdapterError):
    code = "PROVIDER_TIMEOUT"
    retryable = True


class ProviderRateLimit(ExtractionAdapterError):
    code = "PROVIDER_RATE_LIMIT"
    retryable = True


class InvalidProviderRequest(ExtractionAdapterError):
    code = "INVALID_PROVIDER_REQUEST"


class ProviderResourceUnavailable(ExtractionAdapterError):
    code = "PROVIDER_RESOURCE_UNAVAILABLE"


class ProviderConnectionError(ExtractionAdapterError):
    code = "PROVIDER_CONNECTION_ERROR"
    retryable = True


class ProviderUnavailable(ExtractionAdapterError):
    code = "PROVIDER_UNAVAILABLE"
    retryable = True


class InvalidProviderResponse(ExtractionAdapterError):
    code = "INVALID_PROVIDER_RESPONSE"


class ExtractionValidationError(ExtractionAdapterError):
    code = "EXTRACTION_VALIDATION_ERROR"
