"""Environment-based application configuration.

External AI access is fail-closed by default. This module does not read files,
open network connections, or initialize providers at import time.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_csv(name: str) -> tuple[str, ...]:
    value = os.getenv(name, "")
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    return int(value)


@dataclass(frozen=True, slots=True)
class AppConfig:
    app_name: str = "Virology Lab IDP"
    app_version: str = "0.1.0"
    ai_outbound_enabled: bool = False
    allow_external_phi: bool = False
    approved_ai_providers: tuple[str, ...] = ()
    default_extraction_adapter: str = "local_ocr"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    max_upload_bytes: int = 20 * 1024 * 1024
    extraction_pipeline_version: str = "api-v1"
    gemini_structured_outputs_experimental: bool = False

    @classmethod
    def from_env(cls) -> "AppConfig":
        return cls(
            ai_outbound_enabled=_env_bool("AI_OUTBOUND_ENABLED", default=False),
            allow_external_phi=_env_bool("ALLOW_EXTERNAL_PHI", default=False),
            approved_ai_providers=_env_csv("APPROVED_AI_PROVIDERS"),
            default_extraction_adapter=os.getenv("DEFAULT_EXTRACTION_ADAPTER", "local_ocr"),
            gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            max_upload_bytes=_env_int("MAX_UPLOAD_BYTES", 20 * 1024 * 1024),
            extraction_pipeline_version=os.getenv("EXTRACTION_PIPELINE_VERSION", "api-v1"),
            gemini_structured_outputs_experimental=_env_bool(
                "GEMINI_STRUCTURED_OUTPUTS_EXPERIMENTAL", default=False
            ),
        )

    def external_ai_allowed(self, provider_name: str) -> bool:
        """Fail closed unless outbound AI, PHI transfer, and provider approval are explicit."""

        return (
            self.ai_outbound_enabled
            and self.allow_external_phi
            and provider_name in self.approved_ai_providers
        )

    def provider_api_key(self, provider_name: str) -> str | None:
        if provider_name == "gemini":
            return self.gemini_api_key
        return None

    def provider_model(self, provider_name: str) -> str | None:
        if provider_name == "gemini":
            return self.gemini_model
        return None
