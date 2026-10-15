"""OpenAI extraction adapter with provider-independent canonical output."""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable
from typing import Any, get_args

from pydantic import ValidationError

from backend.core.config import AppConfig
from backend.domain import ExtractionRequest, ExtractionResult
from backend.domain.models import Evidence, ExtractionValueStatus, ProviderMetadata, RequiredFieldName
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
from backend.services.extraction.policy import ProviderPolicy
from backend.services.extraction.provider_errors import (
    is_transport_error,
    safe_provider_diagnostics,
)
from backend.services.extraction.prompts import build_openai_extraction_prompt
from backend.services.extraction.schema_validation import validate_extraction_result_schema


SUPPORTED_MIME_TYPES = {"application/pdf", "image/jpeg", "image/png"}
DEFAULT_MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
NON_TRANSIENT_RATE_LIMIT_STATUSES = frozenset(
    {
        "billing_hard_limit_reached",
        "insufficient_quota",
        "quota_exceeded",
    }
)

ClientFactory = Callable[[str, int], Any]
SleepFn = Callable[[float], None]


class OpenAIExtractionAdapter(ExtractionAdapter):
    """Normalize OpenAI Responses output into the canonical extraction result."""

    provider_name = "openai"

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
        self._client_factory = client_factory or self._create_openai_client
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
            api_key=self._config.openai_api_key,
        )

        prompt = build_openai_extraction_prompt(request.fields, request.locale)
        try:
            client = self._client_factory(self._config.openai_api_key or "", self._timeout_seconds)
        except ExtractionAdapterError:
            raise
        except Exception as exc:
            raise self._classify_provider_error(exc) from exc
        response = self._generate_with_retries(client, prompt, document)
        payload = self._parse_provider_payload(response)
        result = to_canonical_result(
            request,
            payload,
            ProviderMetadata(
                name=self.provider_name, mode="vision_ai", version=self._config.openai_model
            ),
        )
        validate_extraction_result_schema(result)
        return result

    def _validate_document(self, document: DocumentInput) -> None:
        if document.mime_type not in SUPPORTED_MIME_TYPES:
            raise UnsupportedDocumentType("Document MIME type is not supported for OpenAI extraction.")
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
        return client.responses.create(
            model=self._config.openai_model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        self._document_input_part(document),
                    ],
                }
            ],
            text=_openai_text_format(),
            timeout=self._timeout_seconds,
            store=False,
        )

    def _document_input_part(self, document: DocumentInput) -> dict[str, str]:
        encoded = base64.b64encode(document.content).decode("ascii")
        data_url = f"data:{document.mime_type};base64,{encoded}"
        if document.mime_type == "application/pdf":
            return {
                "type": "input_file",
                "filename": "document.pdf",
                "file_data": data_url,
            }
        return {
            "type": "input_image",
            "image_url": data_url,
        }

    def _parse_provider_payload(self, response: Any) -> ProviderPayload:
        status = _response_attribute(response, "status")
        if status is not None and status != "completed":
            raise InvalidProviderResponse("AI provider did not complete the extraction response.")
        for item in _response_attribute(response, "output") or []:
            for content in _response_attribute(item, "content") or []:
                if _response_attribute(content, "type") == "refusal":
                    raise InvalidProviderResponse("AI provider declined the extraction request.")
        raw_payload = (
            _response_attribute(response, "output_parsed")
            or _response_attribute(response, "parsed")
            or _response_attribute(response, "output_text")
            or _response_attribute(response, "text")
        )
        if raw_payload is None and isinstance(response, dict) and (
            "logical_forms" in response or "fields" in response
        ):
            raw_payload = response
        if raw_payload is None:
            raw_payload = _response_text_from_output(response)
        if raw_payload is None:
            raise InvalidProviderResponse("AI provider response did not include extracted fields.")

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
        provider_status = str(diagnostics.get("provider_status", "")).lower()
        name = type(exc).__name__.lower()
        if isinstance(exc, TimeoutError) or "timeout" in name or status_code == 408:
            return ProviderTimeout(
                "AI provider request timed out.",
                diagnostics=diagnostics,
            )
        if status_code in {400, 422}:
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
            retryable = provider_status not in NON_TRANSIENT_RATE_LIMIT_STATUSES
            return ProviderRateLimit(
                "AI provider rate limit or quota was reached.",
                retryable=retryable,
                diagnostics=diagnostics,
            )
        if status_code in {409, 500, 502, 503, 504} or "unavailable" in name:
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
    def _create_openai_client(api_key: str, timeout_seconds: int) -> Any:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - exercised when dependency is absent.
            raise ProviderUnavailable("OpenAI SDK is not installed.", retryable=False) from exc

        return OpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=0)


def _response_attribute(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _response_text_from_output(response: Any) -> str | None:
    output = _response_attribute(response, "output")
    if output is None:
        return None
    chunks: list[str] = []
    for item in output:
        for content in _response_attribute(item, "content") or []:
            text = _response_attribute(content, "text")
            if isinstance(text, str):
                chunks.append(text)
    return "".join(chunks) if chunks else None


def _openai_text_format() -> dict[str, Any]:
    return {
        "format": {
            "type": "json_schema",
            "name": "virology_extraction_payload",
            "schema": _openai_provider_payload_schema(),
            "strict": False,
        }
    }


def _openai_provider_payload_schema() -> dict[str, Any]:
    evidence_schema = Evidence.model_json_schema()
    definitions = evidence_schema.pop("$defs", {})
    definitions["Evidence"] = evidence_schema
    return {
        "type": "object",
        "$defs": definitions,
        "additionalProperties": False,
        "properties": {
            "logical_forms": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["fields"],
                    "properties": {
                        "form_id": {"type": "string"},
                        "fields": _openai_fields_schema(),
                    },
                },
            },
            "fields": _openai_fields_schema(),
        },
    }


def _openai_fields_schema() -> dict[str, Any]:
    field_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["value_status", "values"],
        "properties": {
            "value_status": {
                "type": "string",
                "enum": list(get_args(ExtractionValueStatus)),
            },
            "values": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["raw"],
                    "properties": {
                        "raw": {"type": "string"},
                        "normalized": {"type": "string"},
                    },
                },
            },
            "notes": {"type": "string"},
            "evidence": {"type": "array", "items": {"$ref": "#/$defs/Evidence"}},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            field_name: field_schema for field_name in get_args(RequiredFieldName)
        },
    }
