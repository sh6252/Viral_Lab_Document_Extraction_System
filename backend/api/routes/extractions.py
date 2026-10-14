"""Document extraction API routes."""

from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from backend.core.config import AppConfig
from backend.domain import ExtractionRequest, ExtractionResult, ProviderPolicySnapshot
from backend.domain.models import RequiredFieldName
from backend.services.extraction import DocumentInput, ExtractionAdapter
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
from backend.services.extraction.orchestrator import ExtractionOrchestrator
from backend.services.extraction.upload_validation import detect_supported_mime_type, read_upload_bytes

router = APIRouter(tags=["extractions"])

REQUIRED_FIELDS: list[RequiredFieldName] = [
    "first_name",
    "last_name",
    "israeli_id",
    "central_numbers",
    "sample_receipt_date",
    "sender",
    "commitment_numbers",
]


class ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    retryable: bool


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: ErrorDetail


def _error_response(status_code: int, error: ExtractionAdapterError) -> JSONResponse:
    payload = ErrorResponse(
        error=ErrorDetail(
            code=error.code,
            message=error.safe_message,
            retryable=error.retryable,
        )
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


def _status_code_for(error: ExtractionAdapterError) -> int:
    if isinstance(error, DocumentTooLarge):
        return 413
    if isinstance(error, UnsupportedDocumentType):
        return 415
    if isinstance(error, InvalidUpload):
        return 400
    if isinstance(error, ProviderNotAuthorized):
        return 403
    if isinstance(error, MissingProviderCredentials):
        return 503
    if isinstance(error, InvalidProviderRequest):
        return 502
    if isinstance(error, ProviderResourceUnavailable):
        return 503
    if isinstance(error, ProviderConnectionError):
        return 503
    if isinstance(error, ProviderTimeout):
        return 504
    if isinstance(error, ProviderRateLimit):
        return 429
    if isinstance(error, ProviderUnavailable):
        return 503
    if isinstance(error, (InvalidProviderResponse, ExtractionValidationError)):
        return 502
    return 500


def get_extraction_orchestrator(request: Request) -> ExtractionOrchestrator:
    adapter: ExtractionAdapter | None = request.app.state.extraction_adapter
    if adapter is None:
        raise ProviderUnavailable("No extraction adapter is configured for this application.")
    return ExtractionOrchestrator(adapter=adapter)


@router.post(
    "/extractions",
    response_model=ExtractionResult,
    response_model_exclude_none=True,
    responses={
        403: {"model": ErrorResponse},
        400: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
        415: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
        504: {"model": ErrorResponse},
    },
)
async def create_extraction(
    request: Request,
    file: Annotated[UploadFile, File()],
    allow_external_processing: Annotated[bool, Form()] = False,
    locale: Annotated[str, Form()] = "he-IL",
) -> ExtractionResult | JSONResponse:
    """Extract canonical fields from an uploaded document without persisting it."""

    config: AppConfig = request.app.state.config
    try:
        orchestrator = get_extraction_orchestrator(request)
        content = await read_upload_bytes(file, max_bytes=config.max_upload_bytes)
        detected_mime_type = detect_supported_mime_type(content)
        if file.content_type and file.content_type != detected_mime_type:
            raise UnsupportedDocumentType(
                "Uploaded file MIME type does not match the document content."
            )

        document_id = f"document-{uuid4()}"
        logical_form_id = f"logical-form-{uuid4()}"
        extraction_request = ExtractionRequest(
            request_id=f"extraction-{uuid4()}",
            logical_form_id=logical_form_id,
            document_id=document_id,
            pages=[],
            fields=REQUIRED_FIELDS,
            locale=locale,
            pipeline_version=config.extraction_pipeline_version,
            provider_policy=ProviderPolicySnapshot(
                data_boundary="external_phi" if allow_external_processing else "local_only",
                allow_external_phi=allow_external_processing,
            ),
        )
        document = DocumentInput(
            content=content,
            mime_type=detected_mime_type,
            filename=file.filename,
        )
        return orchestrator.extract(request=extraction_request, document=document)
    except ExtractionAdapterError as exc:
        return _error_response(_status_code_for(exc), exc)
