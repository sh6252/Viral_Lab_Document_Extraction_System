"""Internal in-memory OCR review composition; no provider, OCR or business writes.

The existing validator reads the packaged canonical schema. No document/artifact
I/O, persistence, clock, global registry, authentication or export is owned here.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Literal

from backend.domain import ExtractionResult
from backend.domain.models import ExtractionFields
from backend.services.extraction.errors import ExtractionValidationError
from backend.services.extraction.ocr_candidates import (
    ASSESSMENT_VERSION,
    SUPPORTED_FIELDS,
    CandidateField,
    OCRCandidate,
    SourceStatus,
    SupportingAssessment,
    assess_ocr_candidates,
)
from backend.services.extraction.ocr_review import (
    REVIEW_VERSION,
    OCRReviewDecision,
    OCRReviewItem,
    apply_review_decision,
    candidate_reference,
)
from backend.services.extraction.schema_validation import validate_extraction_result_schema

WORKFLOW_VERSION = "ocr-review-workflow-v1"
SNAPSHOT_ENCODING_VERSION = "canonical-json-v1"
WorkflowStatus = Literal[
    "no_review_required", "review_required", "partially_reviewed", "completed",
]


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _text(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("NONEMPTY_WORKFLOW_ID_REQUIRED")


def _time(value: datetime) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("TIMEZONE_AWARE_WORKFLOW_TIME_REQUIRED")


@dataclass(frozen=True, slots=True)
class FormPageImage:
    form_id: str
    page_number: int
    source_image_sha256: str


@dataclass(frozen=True, slots=True)
class FieldReviewIssue:
    field_name: str
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AcceptedReviewDecision:
    decision: OCRReviewDecision
    received_at: datetime


@dataclass(frozen=True, slots=True)
class ReviewSession:
    session_id: str
    document_id: str
    form_id: str
    extraction_id: str
    canonical_snapshot_json: str
    canonical_snapshot_sha256: str
    canonical_snapshot_encoding_version: str
    form_page_images: tuple[FormPageImage, ...]
    supporting_assessment: SupportingAssessment
    review_items: tuple[OCRReviewItem, ...]
    pending_field_issues: tuple[FieldReviewIssue, ...]
    outside_scope_review_issues: tuple[FieldReviewIssue, ...]
    decision_history: tuple[AcceptedReviewDecision, ...]
    overall_review_status: WorkflowStatus
    revision: int
    prepared_at: datetime
    updated_at: datetime
    workflow_version: str
    assessment_version: str
    review_version: str
    input_fingerprint: str


@dataclass(frozen=True, slots=True)
class ApprovedReviewValue:
    """Human evidence only; never an export-ready/canonical field value."""

    candidate: OCRCandidate
    reviewed_value: str

    @property
    def candidate_id(self) -> str:
        return candidate_reference(self.candidate)

    @property
    def field_name(self) -> CandidateField:
        return self.candidate.field_name


@dataclass(frozen=True, slots=True)
class ReviewOutcome:
    session: ReviewSession
    replay: bool

    @property
    def approved_values(self) -> tuple[ApprovedReviewValue, ...]:
        return tuple(ApprovedReviewValue(item.candidate, value)
                     for item in self.session.review_items
                     if (value := item.approved_value) is not None)

    def _references(self, state: str) -> tuple[str, ...]:
        return tuple(item.candidate_id for item in self.session.review_items
                     if item.review_state == state)

    @property
    def rejected_references(self) -> tuple[str, ...]:
        return self._references("rejected")

    @property
    def unresolved_references(self) -> tuple[str, ...]:
        return self._references("unresolved")

    @property
    def unverified_references(self) -> tuple[str, ...]:
        return self._references("unverified")

    @property
    def pending_field_issues(self) -> tuple[FieldReviewIssue, ...]:
        return self.session.pending_field_issues

    @property
    def outside_scope_review_issues(self) -> tuple[FieldReviewIssue, ...]:
        return self.session.outside_scope_review_issues


def _status(items: tuple[OCRReviewItem, ...], issues: tuple[FieldReviewIssue, ...]) -> WorkflowStatus:
    if not items and not issues:
        return "no_review_required"
    if items and all(item.review_state in {"approved", "rejected"} for item in items) and not issues:
        return "completed"
    if not any(item.review_decision for item in items):
        return "review_required"
    return "partially_reviewed"


def _field_reasons(fields: ExtractionFields, name: str) -> set[str]:
    value = getattr(fields, name)
    reasons = set()
    if value is None:
        reasons.add("OMITTED_FIELD_REQUIRES_REVIEW")
    else:
        if value.value_status != "present":
            reasons.add("NONPRESENT_FIELD_REQUIRES_REVIEW")
        if value.review_status in {"needs_review", "needs_clarification"}:
            reasons.add("EXISTING_REVIEW_REQUIRED")
    return reasons


def _prepare(
    extraction_result: ExtractionResult,
    ocr_candidates: Iterable[OCRCandidate],
    *, document_id: str, form_id: str,
    form_page_images: Mapping[str, Mapping[int, str]],
    source_status: SourceStatus, session_id: str, prepared_at: datetime,
) -> ReviewSession:
    for identifier in (session_id, document_id, form_id):
        _text(identifier)
    _time(prepared_at)
    if not isinstance(extraction_result, ExtractionResult) or not extraction_result.logical_forms:
        raise ValueError("SUCCESSFUL_CANONICAL_RESULT_REQUIRED")
    try:
        snapshot = _json(extraction_result.model_dump(mode="json", warnings="error"))
        result = ExtractionResult.model_validate_json(snapshot)
        validate_extraction_result_schema(result)
    except (ValueError, TypeError, ExtractionValidationError):
        raise ValueError("INVALID_CANONICAL_RESULT") from None
    forms = {form.form_id: form for form in result.logical_forms}
    if len(forms) != len(result.logical_forms):
        raise ValueError("DUPLICATE_FORM_ID")
    if form_id not in forms:
        raise ValueError("SELECTED_FORM_NOT_FOUND")
    batch = tuple(ocr_candidates)
    assessment = assess_ocr_candidates(
        result, batch, document_id=document_id, form_page_images=form_page_images,
        source_status=source_status,
    )
    provenance = tuple(FormPageImage(name, page, digest)
                       for name, pages in sorted(form_page_images.items())
                       for page, digest in sorted(pages.items()))
    items: dict[str, OCRReviewItem] = {}
    pending = []
    for field in assessment.fields:
        if field.form_id != form_id:
            continue
        if field.review_recommended:
            for candidate in field.ocr_candidates:
                item = OCRReviewItem(candidate, field.provider_values)
                items[item.candidate_id] = item
        if not field.ocr_candidates:
            reasons = set(field.review_reasons) | _field_reasons(forms[form_id].fields, field.field_name)
            if reasons:
                pending.append(FieldReviewIssue(field.field_name, tuple(sorted(reasons))))
    outside = tuple(FieldReviewIssue(name, tuple(sorted(reasons)))
                    for name in ExtractionFields.model_fields if name not in SUPPORTED_FIELDS
                    if (reasons := _field_reasons(forms[form_id].fields, name)))
    ordered_items = tuple(items[key] for key in sorted(items))
    pending_issues = tuple(pending)
    snapshot_hash = _sha(snapshot)
    fingerprint = _sha(_json(dict(
        session_id=session_id, document_id=document_id, form_id=form_id,
        canonical_snapshot_sha256=snapshot_hash,
        canonical_snapshot_encoding_version=SNAPSHOT_ENCODING_VERSION,
        provenance=[asdict(image) for image in provenance],
        candidates=sorted(_json(asdict(candidate)) for candidate in batch),
        source_status=source_status, workflow_version=WORKFLOW_VERSION,
        assessment_version=ASSESSMENT_VERSION, review_version=REVIEW_VERSION,
    )))
    return ReviewSession(
        session_id, document_id, form_id, result.extraction_id, snapshot, snapshot_hash,
        SNAPSHOT_ENCODING_VERSION, provenance, assessment, ordered_items, pending_issues,
        outside, (), _status(ordered_items, pending_issues), 0, prepared_at, prepared_at,
        WORKFLOW_VERSION, ASSESSMENT_VERSION, REVIEW_VERSION, fingerprint,
    )


def _accept(
    session: ReviewSession, decision: OCRReviewDecision, *, expected_revision: int,
    received_at: datetime,
) -> ReviewOutcome:
    if not isinstance(decision, OCRReviewDecision):
        raise ValueError("EXPLICIT_REVIEW_DECISION_REQUIRED")
    selected = next((item for item in session.review_items
                     if item.candidate_id == decision.candidate_id), None)
    if selected is None:
        raise ValueError("CANDIDATE_NOT_ACTIONABLE")
    if (decision.document_id != session.document_id or decision.form_id != session.form_id
            or decision.page_number != selected.candidate.page_number
            or decision.source_image_sha256 != selected.candidate.source_image_sha256
            or FormPageImage(decision.form_id, decision.page_number, decision.source_image_sha256)
            not in session.form_page_images):
        raise ValueError("REVIEW_PROVENANCE_MISMATCH")
    for event in session.decision_history:
        if event.decision.decision_id == decision.decision_id:
            if event.decision == decision:
                return ReviewOutcome(session, replay=True)
            raise ValueError("REVIEW_DECISION_ID_CONFLICT")
    # Reuse the gate, including its single-action and approval semantics.
    reviewed = apply_review_decision(selected, decision)
    if type(expected_revision) is not int or expected_revision != session.revision:
        raise ValueError("STALE_REVIEW_REVISION")
    _time(received_at)
    items = tuple(reviewed if item.candidate_id == decision.candidate_id else item
                  for item in session.review_items)
    return ReviewOutcome(replace(
        session, review_items=items,
        decision_history=session.decision_history + (AcceptedReviewDecision(decision, received_at),),
        revision=session.revision + 1, updated_at=received_at,
        overall_review_status=_status(items, session.pending_field_issues),
    ), replay=False)


def _validate_session(session: ReviewSession) -> None:
    """Re-derive immutable inputs and replay history; never trust editable summaries.

    This detects inconsistent internal records, not malicious forgery/authentication.
    No session registry exists and no global ID uniqueness is claimed.
    """
    if not isinstance(session, ReviewSession):
        raise ValueError("INVALID_REVIEW_SESSION")
    try:
        pages: dict[str, dict[int, str]] = {}
        for image in session.form_page_images:
            pages.setdefault(image.form_id, {})[image.page_number] = image.source_image_sha256
        candidates = tuple(candidate for field in session.supporting_assessment.fields
                           for candidate in field.ocr_candidates)
        expected = _prepare(
            ExtractionResult.model_validate_json(session.canonical_snapshot_json), candidates,
            document_id=session.document_id, form_id=session.form_id, form_page_images=pages,
            source_status=session.supporting_assessment.source_status,
            session_id=session.session_id, prepared_at=session.prepared_at,
        )
        for event in session.decision_history:
            outcome = _accept(expected, event.decision, expected_revision=expected.revision,
                              received_at=event.received_at)
            if outcome.replay:
                raise ValueError("DUPLICATE_HISTORY_EVENT")
            expected = outcome.session
        if expected != session or type(session.revision) is not int:
            raise ValueError("SESSION_CONTENT_MISMATCH")
    except (ValueError, TypeError, AttributeError, ExtractionValidationError):
        raise ValueError("INVALID_REVIEW_SESSION") from None


@dataclass(frozen=True, slots=True)
class ReviewWorkflowService:
    """Stateless composition; caller owns session lifecycle and trusted provenance."""

    def prepare_review(
        self, extraction_result: ExtractionResult, ocr_candidates: Iterable[OCRCandidate],
        *, document_id: str, form_id: str,
        form_page_images: Mapping[str, Mapping[int, str]], source_status: SourceStatus,
        session_id: str, prepared_at: datetime,
    ) -> ReviewSession:
        return _prepare(
            extraction_result, ocr_candidates, document_id=document_id, form_id=form_id,
            form_page_images=form_page_images, source_status=source_status,
            session_id=session_id, prepared_at=prepared_at,
        )

    def apply_decision(
        self, review_session: ReviewSession, review_decision: OCRReviewDecision,
        *, expected_revision: int, received_at: datetime,
    ) -> ReviewOutcome:
        _validate_session(review_session)
        return _accept(review_session, review_decision, expected_revision=expected_revision,
                       received_at=received_at)
