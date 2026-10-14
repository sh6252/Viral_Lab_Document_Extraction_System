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


@dataclass(frozen=True, slots=True)
class AppConfig:
    app_name: str = "Virology Lab IDP"
    app_version: str = "0.1.0"
    ai_outbound_enabled: bool = False
    allow_external_phi: bool = False
    approved_ai_providers: tuple[str, ...] = ()
    default_extraction_adapter: str = "local_ocr"

    @classmethod
    def from_env(cls) -> "AppConfig":
        return cls(
            ai_outbound_enabled=_env_bool("AI_OUTBOUND_ENABLED", default=False),
            allow_external_phi=_env_bool("ALLOW_EXTERNAL_PHI", default=False),
            approved_ai_providers=_env_csv("APPROVED_AI_PROVIDERS"),
            default_extraction_adapter=os.getenv("DEFAULT_EXTRACTION_ADAPTER", "local_ocr"),
        )

    def external_ai_allowed(self, provider_name: str) -> bool:
        """Fail closed unless outbound AI and the named provider are approved."""

        return self.ai_outbound_enabled and provider_name in self.approved_ai_providers
