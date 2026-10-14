"""Gemini extraction adapter with fail-closed provider policy checks."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.core.config import AppConfig
from backend.domain import ExtractionFields, ExtractionRequest, ExtractionResult, FieldResult
from backend.domain.models import LogicalFormResult, ProviderMetadata, RequiredFieldName
from backend.services.extraction.base import DocumentInput, ExtractionAdapter
from backend.services.extraction.errors import (
    DocumentTooLarge,
    ExtractionAdapterError,
    InvalidProviderResponse,
    ProviderRateLimit,
    ProviderTimeout,
    ProviderUnavailable,
    UnsupportedDocumentType,
)
from backend.services.extraction.policy import ProviderPolicy
from backend.services.extraction.prompts import build_gemini_extraction_prompt
from backend.services.extraction.schema_validation import validate_extraction_result_schema


SUPPORTED_MIME_TYPES = {"application/pdf", "image/jpeg", "image/png"}
DEFAULT_MAX_DOCUMENT_BYTES = 20 * 1024 * 1024

ClientFactory = Callable[[str, int], Any]
SleepFn = Callable[[float], None]


class _ProviderLogicalForm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    form_id: str | None = Field(default=None, min_length=1)
    fields: ExtractionFields


class _ProviderPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    logical_forms: list[_ProviderLogicalForm] | None = None
    fields: ExtractionFields | None = None


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
        sleep: SleepFn = time.sleep,
    ) -> None:
        self._config = config
        self._client_factory = client_factory or self._create_google_client
        self._max_document_bytes = max_document_bytes
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._sleep = sleep
        self._policy = ProviderPolicy(config)

    def extract(self, request: ExtractionRequest, document: DocumentInput) -> ExtractionResult:
        self._validate_document(document)
        self._policy.require_external_phi_allowed(
            request=request,
            provider_name=self.provider_name,
            api_key=self._config.gemini_api_key,
        )

        prompt = build_gemini_extraction_prompt(request.fields, request.locale)
        client = self._client_factory(self._config.gemini_api_key or "", self._timeout_seconds)
        response = self._generate_with_retries(client, prompt, document)
        payload = self._parse_provider_payload(response)
        result = self._to_canonical_result(request, payload)
        validate_extraction_result_schema(result)
        return result

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
                return self._call_provider(client, prompt, document)
            except ExtractionAdapterError:
                raise
            except Exception as exc:  # noqa: BLE001 - provider SDK exceptions vary by transport.
                error = self._classify_provider_error(exc)
                if not error.retryable or attempt == attempts - 1:
                    raise error from exc
                self._sleep(min(2**attempt, 5))
        raise ProviderUnavailable("AI provider did not return a response.")

    def _call_provider(self, client: Any, prompt: str, document: DocumentInput) -> Any:
        try:
            from google.genai import types
        except ImportError as exc:  # pragma: no cover - exercised when dependency is absent.
            raise ProviderUnavailable("Google Gen AI SDK is not installed.") from exc

        part = types.Part.from_bytes(data=document.content, mime_type=document.mime_type)
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0,
        )
        return client.models.generate_content(
            model=self._config.gemini_model,
            contents=[part, prompt],
            config=config,
        )

    def _parse_provider_payload(self, response: Any) -> _ProviderPayload:
        raw_payload = getattr(response, "parsed", None)
        if raw_payload is None:
            raw_payload = getattr(response, "text", response)

        if isinstance(raw_payload, str):
            try:
                raw_payload = json.loads(raw_payload)
            except json.JSONDecodeError as exc:
                raise InvalidProviderResponse("AI provider returned non-JSON content.") from exc

        try:
            return _ProviderPayload.model_validate(raw_payload)
        except ValidationError as exc:
            raise InvalidProviderResponse("AI provider response did not match the expected structure.") from exc

    def _to_canonical_result(
        self,
        request: ExtractionRequest,
        payload: _ProviderPayload,
    ) -> ExtractionResult:
        logical_forms = payload.logical_forms
        if logical_forms is None:
            if payload.fields is None:
                raise InvalidProviderResponse("AI provider response did not include extracted fields.")
            logical_forms = [_ProviderLogicalForm(form_id=request.logical_form_id, fields=payload.fields)]

        canonical_forms: list[LogicalFormResult] = []
        for form in logical_forms:
            fields = self._sanitize_fields(form.fields, request.fields)
            canonical_forms.append(
                LogicalFormResult(
                    form_id=form.form_id or request.logical_form_id,
                    fields=fields,
                )
            )

        return ExtractionResult(
            extraction_id=request.request_id,
            provider=ProviderMetadata(
                name=self.provider_name,
                mode="vision_ai",
                version=self._config.gemini_model,
            ),
            logical_forms=canonical_forms,
        )

    def _sanitize_fields(
        self,
        fields: ExtractionFields,
        requested_fields: list[RequiredFieldName],
    ) -> ExtractionFields:
        sanitized: dict[str, FieldResult] = {}
        for field_name in requested_fields:
            field_result = getattr(fields, field_name)
            if field_result is None:
                continue
            if field_result.value_status == "present" and not field_result.values:
                raise InvalidProviderResponse("AI provider marked a field present without values.")
            if field_result.value_status != "present" and field_result.values:
                raise InvalidProviderResponse("AI provider returned values for a non-present field.")
            if field_result.review_status in {
                "approved_as_extracted",
                "approved_corrected",
                "corrected",
            }:
                raise InvalidProviderResponse("AI provider is not allowed to approve or correct fields.")

            review_status = "unreviewed" if field_result.value_status == "present" else "needs_review"
            sanitized[field_name] = field_result.model_copy(update={"review_status": review_status})
        return ExtractionFields(**sanitized)

    def _classify_provider_error(self, exc: Exception) -> ExtractionAdapterError:
        status_code = (
            getattr(exc, "status_code", None)
            or getattr(getattr(exc, "response", None), "status_code", None)
            or getattr(exc, "code", None)
        )
        name = type(exc).__name__.lower()
        if isinstance(exc, TimeoutError) or "timeout" in name:
            return ProviderTimeout("AI provider request timed out.")
        if status_code == 429 or "ratelimit" in name or "rate_limit" in name:
            return ProviderRateLimit("AI provider rate limit was reached.")
        if status_code in {500, 502, 503, 504} or "unavailable" in name:
            return ProviderUnavailable("AI provider is temporarily unavailable.")
        return ProviderUnavailable("AI provider request failed.")

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
