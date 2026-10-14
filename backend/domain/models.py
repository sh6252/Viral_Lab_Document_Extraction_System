"""Pydantic contracts aligned with evaluation extraction-result schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr

ExtractionValueStatus = Literal["present", "missing", "unreadable", "ambiguous", "not_processed"]
ExtractionMode = Literal["local_ocr", "vision_ai", "hybrid", "mock"]
EvidenceSourceKind = Literal[
    "ocr_text",
    "vision_model",
    "barcode",
    "sticker",
    "stamp",
    "handwriting",
    "manual_review",
]
ReviewStatus = Literal[
    "unreviewed",
    "needs_review",
    "approved_as_extracted",
    "corrected",
    "approved_corrected",
    "rejected",
    "acknowledged_blank",
    "needs_clarification",
]
RequiredFieldName = Literal[
    "first_name",
    "last_name",
    "israeli_id",
    "central_numbers",
    "sample_receipt_date",
    "sender",
    "commitment_numbers",
]
DataBoundary = Literal["local_only", "external_no_phi", "external_phi"]


class StrictDomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProviderMetadata(StrictDomainModel):
    name: StrictStr = Field(min_length=1)
    mode: ExtractionMode
    version: StrictStr | None = None


class FieldValue(StrictDomainModel):
    raw: StrictStr
    normalized: StrictStr | None = None


class BoundingBox(StrictDomainModel):
    x: float
    y: float
    width: float
    height: float


class Evidence(StrictDomainModel):
    page_index: int = Field(ge=0)
    source_kind: EvidenceSourceKind
    bounding_box: BoundingBox | None = None
    text_snippet: StrictStr | None = None
    notes: StrictStr | None = None


class ExtractionPage(StrictDomainModel):
    page_id: StrictStr = Field(min_length=1)
    page_index: int = Field(ge=0)
    image_uri: StrictStr
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    rotation_degrees: int
    ocr_text_uri: StrictStr | None = None


class ProviderPolicySnapshot(StrictDomainModel):
    data_boundary: DataBoundary = "local_only"
    allow_external_phi: bool = False


class ExtractionRequest(StrictDomainModel):
    request_id: StrictStr = Field(min_length=1)
    logical_form_id: StrictStr = Field(min_length=1)
    document_id: StrictStr = Field(min_length=1)
    pages: list[ExtractionPage]
    fields: list[RequiredFieldName]
    locale: StrictStr = "he-IL"
    pipeline_version: StrictStr
    provider_policy: ProviderPolicySnapshot = Field(default_factory=ProviderPolicySnapshot)


class FieldResult(StrictDomainModel):
    value_status: ExtractionValueStatus
    values: list[FieldValue]
    signals: dict[str, Any] | None = None
    evidence: list[Evidence] | None = None
    review_status: ReviewStatus | None = None
    notes: StrictStr | None = None


class ExtractionFields(StrictDomainModel):
    first_name: FieldResult | None = None
    last_name: FieldResult | None = None
    israeli_id: FieldResult | None = None
    central_numbers: FieldResult | None = None
    sample_receipt_date: FieldResult | None = None
    sender: FieldResult | None = None
    commitment_numbers: FieldResult | None = None


class LogicalFormResult(StrictDomainModel):
    form_id: StrictStr = Field(min_length=1)
    fields: ExtractionFields


class ExtractionResult(StrictDomainModel):
    schema_version: Literal["1.0"] = "1.0"
    extraction_id: StrictStr = Field(min_length=1)
    provider: ProviderMetadata
    logical_forms: list[LogicalFormResult]
