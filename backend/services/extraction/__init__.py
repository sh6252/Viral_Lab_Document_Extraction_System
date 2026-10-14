"""Provider-independent extraction adapters."""

from backend.services.extraction.base import DocumentInput, ExtractionAdapter
from backend.services.extraction.errors import (
    DocumentTooLarge,
    ExtractionAdapterError,
    ExtractionValidationError,
    InvalidProviderResponse,
    MissingProviderCredentials,
    ProviderNotAuthorized,
    ProviderRateLimit,
    ProviderTimeout,
    ProviderUnavailable,
    UnsupportedDocumentType,
)
from backend.services.extraction.gemini_adapter import GeminiExtractionAdapter

__all__ = [
    "DocumentTooLarge",
    "DocumentInput",
    "ExtractionAdapter",
    "ExtractionAdapterError",
    "ExtractionValidationError",
    "GeminiExtractionAdapter",
    "InvalidProviderResponse",
    "MissingProviderCredentials",
    "ProviderNotAuthorized",
    "ProviderRateLimit",
    "ProviderTimeout",
    "ProviderUnavailable",
    "UnsupportedDocumentType",
]
