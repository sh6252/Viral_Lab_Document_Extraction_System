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
from backend.services.extraction.openai_adapter import OpenAIExtractionAdapter
from backend.services.extraction.orchestrator import ExtractionOrchestrator
from backend.services.extraction.registry import build_extraction_adapter, registered_provider_names

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
    "OpenAIExtractionAdapter",
    "ProviderConnectionError",
    "ProviderNotAuthorized",
    "ProviderRateLimit",
    "ProviderResourceUnavailable",
    "ProviderTimeout",
    "ProviderUnavailable",
    "UnsupportedDocumentType",
    "build_extraction_adapter",
    "registered_provider_names",
]
