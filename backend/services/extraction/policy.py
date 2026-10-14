"""Provider policy checks for external extraction adapters."""

from __future__ import annotations

from dataclasses import dataclass

from backend.core.config import AppConfig
from backend.domain import ExtractionRequest
from backend.services.extraction.errors import MissingProviderCredentials, ProviderNotAuthorized


@dataclass(frozen=True, slots=True)
class ProviderPolicyDecision:
    allowed: bool
    reason_code: str
    data_boundary: str
    allow_external_phi: bool
    requires_audit: bool
    safe_message: str


class ProviderPolicy:
    """Fail-closed policy for outbound AI providers."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config

    def require_external_phi_allowed(
        self,
        *,
        request: ExtractionRequest,
        provider_name: str,
        api_key: str | None,
    ) -> ProviderPolicyDecision:
        if not self._config.ai_outbound_enabled:
            raise ProviderNotAuthorized("External AI is disabled by configuration.")
        if not self._config.allow_external_phi:
            raise ProviderNotAuthorized("External PHI transmission is disabled by configuration.")
        if provider_name not in self._config.approved_ai_providers:
            raise ProviderNotAuthorized("The selected AI provider is not approved by configuration.")
        if request.provider_policy.data_boundary != "external_phi":
            raise ProviderNotAuthorized("The extraction request does not allow external PHI processing.")
        if not request.provider_policy.allow_external_phi:
            raise ProviderNotAuthorized("The extraction request does not permit external PHI transmission.")
        if not api_key:
            raise MissingProviderCredentials("The selected AI provider is missing required credentials.")

        return ProviderPolicyDecision(
            allowed=True,
            reason_code="allowed",
            data_boundary="external_phi",
            allow_external_phi=True,
            requires_audit=True,
            safe_message="External AI provider use is explicitly allowed for this request.",
        )
