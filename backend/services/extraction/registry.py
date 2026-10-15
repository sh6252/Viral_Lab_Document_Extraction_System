"""Minimal registry for approved extraction adapter construction."""

from __future__ import annotations

from collections.abc import Callable

from backend.core.config import AppConfig
from backend.services.extraction.base import ExtractionAdapter
from backend.services.extraction.errors import ProviderUnavailable
from backend.services.extraction.gemini_adapter import GeminiExtractionAdapter
from backend.services.extraction.openai_adapter import OpenAIExtractionAdapter


AdapterBuilder = Callable[[AppConfig], ExtractionAdapter]

NO_ADAPTER_VALUES = {"", "local_ocr", "none"}
ADAPTER_BUILDERS: dict[str, AdapterBuilder] = {
    "gemini": lambda config: GeminiExtractionAdapter(
        config=config, max_document_bytes=config.max_upload_bytes
    ),
    "openai": lambda config: OpenAIExtractionAdapter(
        config=config, max_document_bytes=config.max_upload_bytes
    ),
}


def build_extraction_adapter(config: AppConfig) -> ExtractionAdapter | None:
    provider_name = config.default_extraction_adapter.strip().lower()
    if provider_name in NO_ADAPTER_VALUES:
        return None
    builder = ADAPTER_BUILDERS.get(provider_name)
    if builder is None:
        raise ProviderUnavailable(
            "Configured extraction adapter is not supported.",
            retryable=False,
            diagnostics={"provider_name": provider_name},
        )
    return builder(config)


def registered_provider_names() -> tuple[str, ...]:
    return tuple(sorted(ADAPTER_BUILDERS))
