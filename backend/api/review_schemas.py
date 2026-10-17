"""Allowlisted public review DTOs, independent of canonical/storage models."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

REVIEWER_REFERENCE_OMITTED = "review-api-v1:reference-omitted"
Verification = Literal["unverified", "confirmed", "rejected", "unresolved"]
State = Literal["no_review_required", "review_required", "partially_reviewed", "completed"]
FieldName = Literal["first_name", "last_name", "israeli_id", "central_numbers",
                    "sample_receipt_date", "sender", "commitment_numbers"]


class ReviewDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReviewValueDTO(ReviewDTO):
    raw: StrictStr
    normalized: StrictStr | None = None


class ReviewEvidenceDTO(ReviewDTO):
    artifact_reference: StrictStr | None = None
    page_number: StrictInt = Field(ge=1)
    image_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    bbox: tuple[float, float, float, float] | None = None
    coordinate_system: StrictStr | None = None
    availability: Literal["available", "unavailable"]


class ReviewIssueDTO(ReviewDTO):
    field_name: FieldName
    reasons: tuple[str, ...]


class PriorDecisionDTO(ReviewDTO):
    decision_id: StrictStr
    decision: Literal["approved", "rejected", "unresolved"]
    reviewed_value: StrictStr | None
    human_correction_occurred: bool
    review_reason: StrictStr
    reviewer_note: StrictStr | None
    reviewer_reference: StrictStr | None
    declared_reviewed_at: datetime


class ReviewItemDTO(ReviewDTO):
    candidate_reference: StrictStr
    field_name: Literal["central_numbers", "commitment_numbers"]
    ocr_value: ReviewValueDTO
    provider_values: tuple[ReviewValueDTO, ...]
    review_state: Literal["unverified", "approved", "rejected", "unresolved"]
    field_assignment_status: Verification
    value_verification_status: Verification
    prior_decision: PriorDecisionDTO | None
    evidence: ReviewEvidenceDTO


class ReviewSessionDTO(ReviewDTO):
    session_id: StrictStr
    document_id: StrictStr
    form_id: StrictStr
    revision: StrictInt
    review_status: State
    actionable_items: tuple[ReviewItemDTO, ...]
    pending_issues: tuple[ReviewIssueDTO, ...]
    outside_scope_issue_indicators: tuple[ReviewIssueDTO, ...]
    ocr_source_status: Literal["succeeded", "disabled", "unavailable", "failed"]
    workflow_scope: Literal["ocr_candidates"] = "ocr_candidates"
    can_submit_decision: bool
    can_read_source_artifact: bool


class SubmitReviewDecisionDTO(ReviewDTO):
    decision_id: StrictStr
    candidate_reference: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    expected_revision: StrictInt = Field(ge=0)
    decision: Literal["approved", "rejected", "unresolved"]
    field_assignment_status: Verification
    value_verification_status: Verification
    reviewed_value: StrictStr | None = None
    review_reason: StrictStr
    reviewer_note: StrictStr | None = None
    reviewer_reference: StrictStr | None = None
    declared_reviewed_at: datetime

    @field_validator("decision_id", "review_reason", "reviewer_note", "reviewer_reference")
    @classmethod
    def nonblank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("NONBLANK_METADATA_REQUIRED")
        return value

    @field_validator("reviewer_reference")
    @classmethod
    def reject_reserved(cls, value):
        if value == REVIEWER_REFERENCE_OMITTED:
            raise ValueError("RESERVED_REFERENCE")
        return value

    @field_validator("declared_reviewed_at", mode="before")
    @classmethod
    def timestamp_input(cls, value):
        if not isinstance(value, (str, datetime)):
            raise ValueError("ISO_TIMESTAMP_REQUIRED")
        return value

    @field_validator("declared_reviewed_at")
    @classmethod
    def timestamp_aware(cls, value):
        if value.utcoffset() is None:
            raise ValueError("AWARE_TIMESTAMP_REQUIRED")
        return value


class ReviewDecisionResponseDTO(ReviewDTO):
    session_id: StrictStr
    form_id: StrictStr
    revision: StrictInt
    review_status: State
    replay: bool
    item: ReviewItemDTO
    workflow_scope: Literal["ocr_candidates"] = "ocr_candidates"
