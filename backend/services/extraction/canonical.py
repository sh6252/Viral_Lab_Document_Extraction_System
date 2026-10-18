"""Shared provider payload validation and candidate-only canonical mapping."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from backend.domain import ExtractionFields, ExtractionRequest, ExtractionResult, FieldResult
from backend.domain.models import LogicalFormResult, ProviderMetadata, RequiredFieldName
from backend.services.extraction.errors import InvalidProviderResponse


class ProviderLogicalForm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    form_id: str | None = Field(default=None, min_length=1)
    fields: ExtractionFields


class ProviderPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    logical_forms: list[ProviderLogicalForm] | None = None
    fields: ExtractionFields | None = None


def to_canonical_result(
    request: ExtractionRequest,
    payload: ProviderPayload,
    provider: ProviderMetadata,
) -> ExtractionResult:
    if payload.logical_forms is not None and payload.fields is not None:
        raise InvalidProviderResponse("AI provider returned conflicting result representations.")
    forms = payload.logical_forms
    if forms is None:
        if payload.fields is None:
            raise InvalidProviderResponse("AI provider response did not include extracted fields.")
        forms = [ProviderLogicalForm(fields=payload.fields)]
    if not forms:
        raise InvalidProviderResponse("AI provider response did not include a logical form.")

    canonical_forms: list[LogicalFormResult] = []
    seen_ids: set[str] = set()
    for form in forms:
        if form.form_id is None and len(forms) > 1:
            raise InvalidProviderResponse("Multiple logical forms require distinct form identifiers.")
        form_id = form.form_id or request.logical_form_id
        if form_id in seen_ids:
            raise InvalidProviderResponse("AI provider returned duplicate logical form identifiers.")
        seen_ids.add(form_id)
        canonical_forms.append(
            LogicalFormResult(form_id=form_id, fields=sanitize_fields(form.fields, request.fields))
        )
    return ExtractionResult(
        extraction_id=request.request_id,
        provider=provider,
        logical_forms=canonical_forms,
    )


def sanitize_fields(
    fields: ExtractionFields, requested_fields: list[RequiredFieldName]
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
            "rejected",
            "acknowledged_blank",
        }:
            raise InvalidProviderResponse("AI provider is not allowed to make terminal review decisions.")
        # Warnings are candidate metadata, never a human approval or disposition.
        review_status = field_result.review_status
        if review_status not in {"needs_review", "needs_clarification"}:
            review_status = "unreviewed" if field_result.value_status == "present" else "needs_review"
        sanitized[field_name] = field_result.model_copy(update={"review_status": review_status})
    return ExtractionFields(**sanitized)
