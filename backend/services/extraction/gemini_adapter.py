"""Gemini extraction adapter with fail-closed provider policy checks."""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from backend.core.config import AppConfig
from backend.domain import ExtractionRequest, ExtractionResult
from backend.domain.models import ProviderMetadata
from backend.services.extraction.base import DocumentInput, ExtractionAdapter
from backend.services.extraction.canonical import ProviderPayload, to_canonical_result
from backend.services.extraction.errors import (
    DocumentTooLarge,
    ExtractionAdapterError,
    InvalidProviderResponse,
    InvalidProviderRequest,
    ProviderConnectionError,
    ProviderNotAuthorized,
    ProviderRateLimit,
    ProviderResourceUnavailable,
    ProviderTimeout,
    ProviderUnavailable,
    UnsupportedDocumentType,
)
from backend.services.extraction.gemini_schema import build_gemini_response_schema
from backend.services.extraction.policy import ProviderPolicy
from backend.services.extraction.provider_errors import (
    is_transport_error,
    safe_provider_diagnostics,
)
from backend.services.extraction.prompts import build_gemini_extraction_prompt
from backend.services.extraction.schema_validation import validate_extraction_result_schema


SUPPORTED_MIME_TYPES = {"application/pdf", "image/jpeg", "image/png"}
DEFAULT_MAX_DOCUMENT_BYTES = 20 * 1024 * 1024

ClientFactory = Callable[[str, int], Any]
SleepFn = Callable[[float], None]


class GeminiExtractionAdapter(ExtractionAdapter):
    """Normalize Gemini output into the canonical extraction result contract."""

    provider_name = "gemini"

    def __init__(
        self,
        *,
        config: AppConfig,
        client_factory: ClientFactory | None = None,
        max_document_bytes: int = DEFAULT_MAX_DOCUMENT_BYTES,
        timeout_seconds: int = 60,
        max_retries: int = 1,
        retry_unavailable_only: bool = False,
        sleep: SleepFn = time.sleep,
        observer: Callable[[str, Exception | None], None] | None = None,
    ) -> None:
        self._config = config
        self._client_factory = client_factory or self._create_google_client
        self._max_document_bytes = max_document_bytes
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        if retry_unavailable_only and not 0 <= max_retries <= 2:
            raise ValueError("UNAVAILABLE_RETRIES_MUST_BE_0_TO_2")
        self._retry_unavailable_only = retry_unavailable_only
        self._sleep = sleep
        self._policy = ProviderPolicy(config)
        self._observer = observer

    def extract(self, request: ExtractionRequest, document: DocumentInput) -> ExtractionResult:
        stage = "provider_policy"
        try:
            self._observe(stage)
            self._validate_document(document)
            self._policy.require_external_phi_allowed(request=request, provider_name=self.provider_name,
                                                     api_key=self._config.gemini_api_key)
            stage = "provider_client"
            self._observe(stage)
            client = self._client_factory(self._config.gemini_api_key or "", self._timeout_seconds)
            stage = "provider_request"
            self._observe(stage)
            prompt = build_gemini_extraction_prompt(request.fields, request.locale)
            response = self._generate_with_retries(client, prompt, document)
            stage = "provider_parse"
            self._observe(stage)
            payload = self._parse_provider_payload(response)
            stage = "canonicalize"
            self._observe(stage)
            result = to_canonical_result(request, payload, ProviderMetadata(
                name=self.provider_name, mode="vision_ai", version=self._config.gemini_model))
            validate_extraction_result_schema(result)
            return result
        except Exception as error:
            self._observe(stage, error)
            raise

    def _observe(self, stage: str, error: Exception | None = None) -> None:
        if self._observer is not None:
            self._observer(stage, error)

    def _validate_document(self, document: DocumentInput) -> None:
        if document.mime_type not in SUPPORTED_MIME_TYPES:
            raise UnsupportedDocumentType("Document MIME type is not supported for Gemini extraction.")
        if not document.content:
            raise UnsupportedDocumentType("Document content is empty.")
        if len(document.content) > self._max_document_bytes:
            raise DocumentTooLarge("Document exceeds the configured extraction size limit.")

    def _generate_with_retries(self, client: Any, prompt: str, document: DocumentInput) -> Any:
        attempts = self._max_retries + 1
        for attempt in range(attempts):
            try:
                self._observe("provider_attempt")
                return self._call_provider(client, prompt, document)
            except ExtractionAdapterError:
                raise
            except Exception as exc:  # noqa: BLE001 - provider SDK exceptions vary by transport.
                error = self._classify_provider_error(exc)
                details = error.safe_diagnostics()
                retryable = (details.get("http_status_code") == 503 and details.get("provider_status") == "UNAVAILABLE"
                             if self._retry_unavailable_only else error.retryable)
                if not retryable or attempt == attempts - 1:
                    raise error from exc
                if self._retry_unavailable_only:
                    delay = 3 * (8 / 3) ** attempt  # Approximately 3s, then 8s; at most two retries.
                    self._sleep(random.uniform(delay * 0.8, delay * 1.2))
                else:
                    self._sleep(min(2**attempt, 5))
        raise ProviderUnavailable("AI provider did not return a response.")

    def _call_provider(self, client: Any, prompt: str, document: DocumentInput) -> Any:
        try:
            from google.genai import types
        except ImportError as exc:  # pragma: no cover - exercised when dependency is absent.
            raise ProviderUnavailable("Google Gen AI SDK is not installed.") from exc

        part = types.Part.from_bytes(data=document.content, mime_type=document.mime_type)
        # Gemini 3.8 migration removes deprecated sampling parameters. Keep the
        # existing sampling contract for other explicitly configured models.
        config = types.GenerateContentConfig(response_mime_type="application/json")
        if self._config.gemini_model != "gemini-3.8-flash":
            config.temperature = 0
        if self._config.gemini_structured_outputs_experimental:
            config.response_json_schema = build_gemini_response_schema()
        return client.models.generate_content(
            model=self._config.gemini_model,
            contents=[part, prompt],
            config=config,
        )

    def _parse_provider_payload(self, response: Any) -> ProviderPayload:
        raw_payload = getattr(response, "parsed", None)
        if raw_payload is None:
            raw_payload = getattr(response, "text", response)

        if isinstance(raw_payload, str):
            try:
                raw_payload = json.loads(raw_payload)
            except json.JSONDecodeError as exc:
                raise InvalidProviderResponse("AI provider returned non-JSON content.") from exc

        try:
            return ProviderPayload.model_validate(raw_payload)
        except ValidationError as exc:
            raise InvalidProviderResponse("AI provider response did not match the expected structure.") from exc

    def _classify_provider_error(self, exc: Exception) -> ExtractionAdapterError:
        diagnostics = safe_provider_diagnostics(exc)
        status_code = diagnostics.get("http_status_code")
        name = type(exc).__name__.lower()
        if status_code == 504 or isinstance(exc, TimeoutError) or "timeout" in name:
            return ProviderTimeout(
                "AI provider request timed out.",
                diagnostics=diagnostics,
            )
        if status_code == 400:
            return InvalidProviderRequest(
                "AI provider rejected the extraction request.",
                diagnostics=diagnostics,
            )
        if status_code in {401, 403}:
            return ProviderNotAuthorized(
                "AI provider authentication or permission check failed.",
                diagnostics=diagnostics,
            )
        if status_code == 404:
            return ProviderResourceUnavailable(
                "AI provider resource or model is unavailable.",
                diagnostics=diagnostics,
            )
        if status_code == 429 or "ratelimit" in name or "rate_limit" in name:
            return ProviderRateLimit(
                "AI provider rate limit or quota was reached.",
                diagnostics=diagnostics,
            )
        if status_code in {500, 502, 503, 504} or "unavailable" in name:
            return ProviderUnavailable(
                "AI provider is temporarily unavailable.",
                diagnostics=diagnostics,
            )
        if is_transport_error(exc):
            return ProviderConnectionError(
                "AI provider connection failed.",
                diagnostics=diagnostics,
            )
        return ProviderUnavailable(
            "AI provider request failed.",
            retryable=False,
            diagnostics=diagnostics,
        )

    @staticmethod
    def _create_google_client(api_key: str, timeout_seconds: int) -> Any:
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:  # pragma: no cover - exercised when dependency is absent.
            raise ProviderUnavailable("Google Gen AI SDK is not installed.") from exc

        return genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=timeout_seconds * 1000),
        )
