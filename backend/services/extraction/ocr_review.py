"""Pure, internal human-review records. No canonical mutation, I/O or export.

Checks record a caller's explicit human attestation, not independent proof of
image inspection or reviewer authentication. Runtime authorization is out of scope.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Literal

from backend.services.extraction.ocr_candidates import (
    OCRCandidate,
    ProviderValue,
    SUPPORTED_FIELDS,
)

VerificationStatus = Literal["unverified", "confirmed", "rejected", "unresolved"]
DecisionState = Literal["approved", "rejected", "unresolved"]
REVIEW_VERSION = "ocr-review-v1"
_CHECKS = {"unverified", "confirmed", "rejected", "unresolved"}


def _text(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("NONEMPTY_TEXT_REQUIRED")


def candidate_reference(candidate: OCRCandidate) -> str:
    """Bind every candidate/provenance attribute, without value-based matching."""
    if not isinstance(candidate, OCRCandidate):
        raise ValueError("SCREENED_CANDIDATE_REQUIRED")
    if candidate.field_name not in SUPPORTED_FIELDS:
        raise ValueError("UNSUPPORTED_FIELD")
    payload = json.dumps(asdict(candidate), sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class OCRReviewDecision:
    """Explicit human action; both checks refer to the exact reviewed string.

    A correction is allowed only as explicitly entered text, never normalization.
    Times/identities are caller-supplied audit declarations, not authenticated here.
    """

    decision_id: str
    candidate_id: str
    decision: DecisionState
    field_assignment_status: VerificationStatus
    value_verification_status: VerificationStatus
    reviewed_value: str | None
    review_reason: str
    reviewer_reference: str
    reviewed_at: datetime
    source_image_sha256: str
    document_id: str
    form_id: str
    page_number: int
    reviewer_note: str | None = None

    def __post_init__(self) -> None:
        for value in (self.decision_id, self.review_reason, self.reviewer_reference,
                      self.document_id, self.form_id):
            _text(value)
        for digest in (self.candidate_id, self.source_image_sha256):
            if (not isinstance(digest, str) or len(digest) != 64
                    or any(c not in "0123456789abcdef" for c in digest)):
                raise ValueError("LOWERCASE_SHA256_REQUIRED")
        if type(self.page_number) is not int or self.page_number < 1:
            raise ValueError("ONE_BASED_PAGE_REQUIRED")
        if (not isinstance(self.reviewed_at, datetime)
                or self.reviewed_at.utcoffset() is None):
            raise ValueError("TIMEZONE_AWARE_REVIEW_TIME_REQUIRED")
        if self.reviewer_note is not None:
            _text(self.reviewer_note)
        if self.reviewed_value is not None:
            _text(self.reviewed_value)
        if (self.field_assignment_status not in _CHECKS
                or self.value_verification_status not in _CHECKS):
            raise ValueError("UNKNOWN_VERIFICATION_STATUS")
        if self.decision not in {"approved", "rejected", "unresolved"}:
            raise ValueError("UNKNOWN_REVIEW_DECISION")
        if self.decision == "approved":
            if (self.field_assignment_status != "confirmed"
                    or self.value_verification_status != "confirmed"
                    or self.reviewed_value is None):
                raise ValueError("APPROVAL_REQUIRES_BOTH_CHECKS_AND_REVIEWED_VALUE")
        if (self.decision == "unresolved"
                and "rejected" in {self.field_assignment_status, self.value_verification_status}):
            raise ValueError("REJECTED_CHECK_REQUIRES_REJECTION")


@dataclass(frozen=True, slots=True)
class OCRReviewItem:
    """Detached artifact; only an attached explicit decision changes its state.

    Provider values are immutable snapshots for context, never approval inputs.
    Approval here grants no canonical write or export operation.
    """

    candidate: OCRCandidate
    provider_values: tuple[ProviderValue, ...] = ()
    review_decision: OCRReviewDecision | None = None

    def __post_init__(self) -> None:
        candidate_reference(self.candidate)
        if (not isinstance(self.provider_values, tuple)
                or any(not isinstance(v, ProviderValue) for v in self.provider_values)):
            raise ValueError("IMMUTABLE_PROVIDER_SNAPSHOTS_REQUIRED")
        for value in self.provider_values:
            _text(value.raw_value)
            if value.normalized_value is not None:
                _text(value.normalized_value)
        if self.review_decision is not None:
            decision = self.review_decision
            if not isinstance(decision, OCRReviewDecision):
                raise ValueError("EXPLICIT_REVIEW_DECISION_REQUIRED")
            source = self.candidate
            if (decision.candidate_id != self.candidate_id
                    or decision.document_id != source.document_id
                    or decision.form_id != source.form_id
                    or decision.page_number != source.page_number
                    or decision.source_image_sha256 != source.source_image_sha256):
                raise ValueError("REVIEW_PROVENANCE_MISMATCH")

    @property
    def candidate_id(self) -> str:
        return candidate_reference(self.candidate)

    @property
    def field_assignment_status(self) -> VerificationStatus:
        return self.review_decision.field_assignment_status if self.review_decision else "unverified"

    @property
    def value_verification_status(self) -> VerificationStatus:
        return self.review_decision.value_verification_status if self.review_decision else "unverified"

    @property
    def review_state(self) -> Literal["unverified", "approved", "rejected", "unresolved"]:
        return self.review_decision.decision if self.review_decision else "unverified"

    @property
    def review_reason(self) -> str | None:
        return self.review_decision.review_reason if self.review_decision else None

    @property
    def approved_value(self) -> str | None:
        return self.review_decision.reviewed_value if self.review_state == "approved" else None

    @property
    def human_correction_occurred(self) -> bool:
        return (self.review_decision is not None
                and self.review_decision.reviewed_value is not None
                and self.review_decision.reviewed_value != self.candidate.raw_value)

    def to_json(self) -> str:
        """Stable internal audit serialization, including raw and reviewed values."""
        data = asdict(self)
        if self.review_decision:
            data["review_decision"]["reviewed_at"] = self.review_decision.reviewed_at.isoformat()
        data.update(version=REVIEW_VERSION, candidate_id=self.candidate_id,
                    review_state=self.review_state, approved_value=self.approved_value,
                    human_correction_occurred=self.human_correction_occurred)
        return json.dumps(data, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False)


def apply_review_decision(item: OCRReviewItem, decision: OCRReviewDecision) -> OCRReviewItem:
    """Return a new artifact; identical replays are idempotent, conflicts fail.

    This bounded gate accepts one decision per item. A later re-review/versioning
    workflow is deliberately not implemented; no silent last-write-wins behavior.
    """
    if not isinstance(item, OCRReviewItem) or not isinstance(decision, OCRReviewDecision):
        raise ValueError("REVIEW_ITEM_AND_DECISION_REQUIRED")
    if item.review_decision is not None:
        if item.review_decision == decision:
            return item
        raise ValueError("REVIEW_DECISION_CONFLICT")
    return OCRReviewItem(item.candidate, item.provider_values, decision)
