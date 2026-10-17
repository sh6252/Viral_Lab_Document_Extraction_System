"""Offline, non-authoritative OCR evidence assessment. No I/O or result mutation."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from backend.domain import ExtractionResult

CandidateField = Literal["central_numbers", "commitment_numbers"]
SourceStatus = Literal["succeeded", "disabled", "unavailable", "failed"]
ReconciliationState = Literal[
    "no_support", "corroborated", "partial_support", "ocr_only",
    "disagreement", "unresolved_normalization",
]
SUPPORTED_FIELDS: tuple[CandidateField, ...] = ("central_numbers", "commitment_numbers")
ASSESSMENT_VERSION = "ocr-candidates-v1"


def _require_text(value: str | None) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("NONEMPTY_TEXT_REQUIRED")


def _require_hash(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("LOWERCASE_SHA256_REQUIRED")


@dataclass(frozen=True, slots=True)
class FieldAssignmentEvidence:
    """Caller-supplied, independently established association; not label detection.

    A reference identifies the label/column relation or reviewed region in the
    private source artifact. Merely naming a barcode is not assignment evidence.
    """

    field_name: CandidateField
    kind: Literal["label_relation", "reviewed_region"]
    reference: str

    def __post_init__(self) -> None:
        if self.field_name not in SUPPORTED_FIELDS:
            raise ValueError("UNSUPPORTED_FIELD")
        if self.kind not in {"label_relation", "reviewed_region"}:
            raise ValueError("EXPLICIT_FIELD_ASSIGNMENT_REQUIRED")
        _require_text(self.reference)


@dataclass(frozen=True, slots=True)
class OCRCandidate:
    """A screened numeric token, never an approved business identifier.

    Numeric-only admission limits this OCR path, not the business identifier
    grammar. Rejected raw tokens remain the caller's responsibility to retain.
    Page numbers are ONE-based; bbox is (x, y, width, height) in its named space.
    """

    field_name: CandidateField
    raw_value: str
    source_engine: str
    source_engine_version: str
    source_run_id: str
    source_image_sha256: str
    document_id: str
    form_id: str
    page_number: int
    field_assignment_evidence: FieldAssignmentEvidence
    normalized_value: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    bbox_coordinate_system: str | None = None
    validation_status: Literal["syntax_screened_unverified"] = field(
        default="syntax_screened_unverified", init=False,
    )

    def __post_init__(self) -> None:
        if self.field_name not in SUPPORTED_FIELDS:
            raise ValueError("UNSUPPORTED_FIELD")
        if not isinstance(self.raw_value, str) or re.fullmatch(r"[0-9]+", self.raw_value) is None:
            raise ValueError("INTACT_ASCII_NUMERIC_TOKEN_REQUIRED")
        for value in (
            self.source_engine, self.source_engine_version, self.source_run_id,
            self.document_id, self.form_id,
        ):
            _require_text(value)
        _require_hash(self.source_image_sha256)
        if type(self.page_number) is not int or self.page_number < 1:
            raise ValueError("ONE_BASED_PAGE_REQUIRED")
        evidence = self.field_assignment_evidence
        if not isinstance(evidence, FieldAssignmentEvidence):
            raise ValueError("EXPLICIT_FIELD_ASSIGNMENT_REQUIRED")
        if evidence.field_name != self.field_name:
            raise ValueError("FIELD_ASSIGNMENT_MISMATCH")
        if self.normalized_value is not None:
            _require_text(self.normalized_value)
        if self.bbox is None:
            if self.bbox_coordinate_system is not None:
                raise ValueError("BOX_REQUIRED_FOR_COORDINATE_SYSTEM")
        else:
            if (not isinstance(self.bbox, tuple) or len(self.bbox) != 4
                    or any(type(x) not in {int, float} or not math.isfinite(x) for x in self.bbox)
                    or self.bbox[2] <= 0 or self.bbox[3] <= 0):
                raise ValueError("IMMUTABLE_FINITE_BOX_REQUIRED")
            _require_text(self.bbox_coordinate_system)


@dataclass(frozen=True, slots=True)
class ProviderValue:
    """Detached snapshot, rather than a reference to mutable public FieldValue."""

    raw_value: str
    normalized_value: str | None = None


@dataclass(frozen=True, slots=True)
class FieldAssessment:
    document_id: str
    form_id: str
    field_name: CandidateField
    provider_status: str | None
    provider_review_status: str | None
    provider_values: tuple[ProviderValue, ...]
    ocr_candidates: tuple[OCRCandidate, ...]
    matched_values: tuple[str, ...]
    provider_only_values: tuple[str, ...]
    ocr_only_values: tuple[str, ...]
    state: ReconciliationState
    review_recommended: bool
    review_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SupportingAssessment:
    extraction_id: str
    document_id: str
    source_status: SourceStatus
    fields: tuple[FieldAssessment, ...]
    version: str = ASSESSMENT_VERSION


def _candidate_key(candidate: OCRCandidate) -> tuple[str, ...]:
    # Every provenance difference participates. Equal occurrences are retained;
    # only value sets are deduplicated for reconciliation, never the evidence.
    evidence = candidate.field_assignment_evidence
    return (
        candidate.raw_value, candidate.normalized_value or "", candidate.source_engine,
        candidate.source_engine_version, candidate.source_run_id, candidate.source_image_sha256,
        candidate.document_id, candidate.form_id, str(candidate.page_number),
        repr(candidate.bbox), candidate.bbox_coordinate_system or "", evidence.kind,
        evidence.reference,
    )


def assess_ocr_candidates(
    result: ExtractionResult,
    candidates: Iterable[OCRCandidate],
    *,
    document_id: str,
    form_page_images: Mapping[str, Mapping[int, str]],
    source_status: SourceStatus = "succeeded",
) -> SupportingAssessment:
    """Return immutable advice, never change canonical values or review states.

    The caller supplies trusted document/form/page-image provenance, absent from
    ExtractionResult itself. No form/value matching or filesystem verification is
    attempted here. Provider failures are exceptions, not valid inputs; a future
    coordinator must preserve those failures rather than call this with OCR alone.
    """
    if not isinstance(result, ExtractionResult) or not result.logical_forms:
        raise ValueError("SUCCESSFUL_CANONICAL_RESULT_REQUIRED")
    _require_text(document_id)
    if source_status not in {"succeeded", "disabled", "unavailable", "failed"}:
        raise ValueError("UNKNOWN_OCR_SOURCE_STATUS")
    forms = {form.form_id: form for form in result.logical_forms}
    if len(forms) != len(result.logical_forms) or set(forms) != set(form_page_images):
        raise ValueError("UNAMBIGUOUS_FORM_PROVENANCE_REQUIRED")
    for pages in form_page_images.values():
        if not pages:
            raise ValueError("FORM_PAGE_PROVENANCE_REQUIRED")
        for page, digest in pages.items():
            if type(page) is not int or page < 1:
                raise ValueError("ONE_BASED_PAGE_REQUIRED")
            _require_hash(digest)

    batch = tuple(candidates)
    if batch and source_status != "succeeded":
        raise ValueError("CANDIDATES_REQUIRE_SUCCESSFUL_OCR_SOURCE")
    grouped: dict[tuple[str, CandidateField], list[OCRCandidate]] = {}
    for candidate in batch:
        if not isinstance(candidate, OCRCandidate):
            raise ValueError("SCREENED_CANDIDATE_REQUIRED")
        if (candidate.document_id != document_id
                or form_page_images.get(candidate.form_id, {}).get(candidate.page_number)
                != candidate.source_image_sha256):
            raise ValueError("CANDIDATE_PROVENANCE_MISMATCH")
        grouped.setdefault((candidate.form_id, candidate.field_name), []).append(candidate)

    assessments = []
    for form_id in sorted(forms):
        for name in SUPPORTED_FIELDS:
            provider_field = getattr(forms[form_id].fields, name)
            values = tuple(sorted((
                ProviderValue(v.raw, v.normalized) for v in provider_field.values
            ), key=lambda v: (
                v.raw_value, v.normalized_value is not None, v.normalized_value or "",
            ))) if provider_field else ()
            status = provider_field.value_status if provider_field else None
            review = provider_field.review_status if provider_field else None
            # Fail explicitly if a caller bypassed canonical sanitization.
            if (status == "present" and not values) or (values and status != "present"):
                raise ValueError("SANITIZED_CANONICAL_FIELDS_REQUIRED")
            evidence = tuple(sorted(grouped.get((form_id, name), []), key=_candidate_key))
            provider_set = {v.raw_value for v in values}
            ocr_set = {c.raw_value for c in evidence}
            reasons = {"EXISTING_REVIEW_REQUIRED"} if review in {"needs_review", "needs_clarification"} else set()
            state: ReconciliationState = "no_support"
            if evidence:
                if any(v.normalized_value is not None and v.normalized_value != v.raw_value
                       for v in (*values, *evidence)):
                    state = "unresolved_normalization"
                    reasons.add("UNSUPPORTED_NORMALIZATION")
                elif not provider_set:
                    state = "ocr_only"
                    reasons.add("OCR_CANDIDATE_WITHOUT_PROVIDER_VALUE")
                elif ocr_set - provider_set:
                    state = "disagreement"
                    reasons.add("OCR_PROVIDER_DISAGREEMENT")
                elif provider_set == ocr_set:
                    state = "corroborated"
                else:
                    state = "partial_support"
            assessments.append(FieldAssessment(
                document_id=document_id, form_id=form_id, field_name=name,
                provider_status=status, provider_review_status=review, provider_values=values,
                ocr_candidates=evidence, matched_values=tuple(sorted(provider_set & ocr_set)),
                provider_only_values=tuple(sorted(provider_set - ocr_set)),
                ocr_only_values=tuple(sorted(ocr_set - provider_set)), state=state,
                review_recommended=bool(reasons), review_reasons=tuple(sorted(reasons)),
            ))
    return SupportingAssessment(result.extraction_id, document_id, source_status, tuple(assessments))
