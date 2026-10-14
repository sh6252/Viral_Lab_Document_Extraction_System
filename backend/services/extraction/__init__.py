"""Provider-independent extraction adapters."""

from backend.services.extraction.base import DocumentInput, ExtractionAdapter
from backend.services.extraction.errors import (
    DocumentTooLarge,
    ExtractionAdapterError,
    ExtractionValidationError,
    InvalidUpload,
    InvalidProviderResponse,
    InvalidProviderRequest,
    MissingProviderCredentials,
    ProviderConnectionError,
    ProviderNotAuthorized,
    ProviderRateLimit,
    ProviderResourceUnavailable,
    ProviderTimeout,
    ProviderUnavailable,
    UnsupportedDocumentType,
)
from backend.services.extraction.gemini_adapter import GeminiExtractionAdapter
from backend.services.extraction.orchestrator import ExtractionOrchestrator

__all__ = [
    "DocumentTooLarge",
    "DocumentInput",
    "ExtractionAdapter",
    "ExtractionAdapterError",
    "ExtractionOrchestrator",
    "ExtractionValidationError",
    "InvalidUpload",
    "GeminiExtractionAdapter",
    "InvalidProviderResponse",
    "InvalidProviderRequest",
    "MissingProviderCredentials",
    "ProviderConnectionError",
    "ProviderNotAuthorized",
    "ProviderRateLimit",
    "ProviderResourceUnavailable",
    "ProviderTimeout",
    "ProviderUnavailable",
    "UnsupportedDocumentType",
]
