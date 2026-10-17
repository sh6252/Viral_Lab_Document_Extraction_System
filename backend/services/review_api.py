"""Authorized selected-form projection and deterministic decision adaptation."""

from functools import wraps

from backend.api.review_schemas import (
    REVIEWER_REFERENCE_OMITTED, PriorDecisionDTO, ReviewDecisionResponseDTO, ReviewEvidenceDTO,
    ReviewIssueDTO, ReviewItemDTO, ReviewSessionDTO, ReviewValueDTO, SubmitReviewDecisionDTO,
)
from backend.core.review_access import (
    AuthenticatedPrincipal, AuthorizationPolicy, ReviewBoundaryError, ReviewResourceScope, permitted,
)
from backend.persistence.review_codec import ReviewIntegrityError
from backend.persistence.review_repository import ReviewPersistenceError, ReviewSessionRepository, TrustedReviewContext
from backend.services.extraction.ocr_review import OCRReviewDecision, OCRReviewItem
from backend.services.extraction.review_workflow import ReviewSession
from backend.services.review_artifacts import ReviewArtifactService
from backend.services.review_persistence import ReviewPersistenceService

_ERROR_MAP = {
    "REVIEW_SESSION_NOT_FOUND": "REVIEW_RESOURCE_NOT_FOUND",
    "REVIEW_ACCESS_DENIED": "REVIEW_OPERATION_FORBIDDEN",
    "REVIEW_ACTOR_CONFLICT": "REVIEW_OPERATION_FORBIDDEN",
    "STALE_REVIEW_REVISION": "REVIEW_STALE_REVISION",
    "REVIEW_DECISION_ID_CONFLICT": "REVIEW_DECISION_CONFLICT",
    "REVIEW_DECISION_CONFLICT": "REVIEW_DECISION_CONFLICT",
    "REVIEW_PROVENANCE_MISMATCH": "INVALID_REVIEW_DECISION",
    "CANDIDATE_NOT_ACTIONABLE": "INVALID_REVIEW_DECISION",
    "APPROVAL_REQUIRES_BOTH_CHECKS_AND_REVIEWED_VALUE": "INVALID_REVIEW_DECISION",
    "REJECTED_CHECK_REQUIRES_REJECTION": "INVALID_REVIEW_DECISION",
    "NONEMPTY_TEXT_REQUIRED": "INVALID_REVIEW_DECISION",
    "INVALID_REVIEW_SESSION": "REVIEW_INTEGRITY_FAILURE",
}
_SAFE_REASONS = {
    "EXISTING_REVIEW_REQUIRED", "NONPRESENT_FIELD_REQUIRES_REVIEW", "OMITTED_FIELD_REQUIRES_REVIEW",
    "OCR_PROVIDER_DISAGREEMENT", "OCR_CANDIDATE_WITHOUT_PROVIDER_VALUE", "UNSUPPORTED_NORMALIZATION",
}


def _safe(method):
    @wraps(method)
    def invoke(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except ReviewBoundaryError:
            raise
        except ReviewIntegrityError:
            raise ReviewBoundaryError("REVIEW_INTEGRITY_FAILURE") from None
        except (ValueError, ReviewPersistenceError) as error:
            raise ReviewBoundaryError(_ERROR_MAP.get(str(error), "REVIEW_SERVICE_UNAVAILABLE")) from None
        except Exception:
            raise ReviewBoundaryError("REVIEW_SERVICE_UNAVAILABLE") from None
    return invoke


class ReviewAPIService:
    def __init__(self, repository: ReviewSessionRepository,
                 persistence: ReviewPersistenceService | None = None,
                 artifacts: ReviewArtifactService | None = None):
        self.repository = repository
        self.persistence = persistence if persistence is not None else ReviewPersistenceService(repository)
        self.artifacts = artifacts if artifacts is not None else ReviewArtifactService()

    def _scope(self, session_id: str, principal: AuthenticatedPrincipal,
               policy: AuthorizationPolicy | None) -> ReviewResourceScope:
        if not isinstance(principal, AuthenticatedPrincipal):
            raise ReviewBoundaryError("AUTHENTICATION_REQUIRED")
        if policy is None:
            raise ReviewBoundaryError("REVIEW_RESOURCE_NOT_FOUND")
        scope = self.repository.get_scope(session_id)
        if not isinstance(scope, ReviewResourceScope) or scope.session_id != session_id:
            raise ReviewBoundaryError("REVIEW_INTEGRITY_FAILURE")
        if not permitted(policy, principal, scope, "read_review"):
            raise ReviewBoundaryError("REVIEW_RESOURCE_NOT_FOUND")
        return scope

    def _load(self, scope: ReviewResourceScope) -> ReviewSession:
        session = self.repository.get(scope.session_id)
        self._check_session(session, scope)
        return session

    @staticmethod
    def _check_session(session: ReviewSession, scope: ReviewResourceScope) -> None:
        if (not isinstance(session, ReviewSession)
                or (session.session_id, session.document_id, session.form_id)
                != (scope.session_id, scope.document_id, scope.form_id)
                or any((item.candidate.document_id, item.candidate.form_id) != (scope.document_id, scope.form_id)
                       for item in session.review_items)):
            raise ReviewBoundaryError("REVIEW_INTEGRITY_FAILURE")

    def _item(self, item: OCRReviewItem, scope: ReviewResourceScope,
              principal: AuthenticatedPrincipal, source_allowed: bool) -> ReviewItemDTO:
        candidate = item.candidate
        action = item.review_decision
        prior = None
        if action is not None:
            prior = PriorDecisionDTO(
                decision_id=action.decision_id, decision=action.decision, reviewed_value=action.reviewed_value,
                human_correction_occurred=item.human_correction_occurred, review_reason=action.review_reason,
                reviewer_note=action.reviewer_note,
                reviewer_reference=None if action.reviewer_reference == REVIEWER_REFERENCE_OMITTED else action.reviewer_reference,
                declared_reviewed_at=action.reviewed_at,
            )
        descriptor = self.artifacts.describe(principal, scope, candidate, source_authorized=source_allowed)
        return ReviewItemDTO(
            candidate_reference=item.candidate_id, field_name=candidate.field_name,
            ocr_value=ReviewValueDTO(raw=candidate.raw_value, normalized=candidate.normalized_value),
            provider_values=tuple(ReviewValueDTO(raw=value.raw_value, normalized=value.normalized_value)
                                  for value in item.provider_values),
            review_state=item.review_state, field_assignment_status=item.field_assignment_status,
            value_verification_status=item.value_verification_status, prior_decision=prior,
            evidence=ReviewEvidenceDTO(
                artifact_reference=descriptor.artifact_reference, page_number=descriptor.page_number,
                image_sha256=descriptor.image_sha256, bbox=descriptor.bbox,
                coordinate_system=descriptor.coordinate_system, availability=descriptor.availability,
            ),
        )

    @staticmethod
    def _issues(issues) -> tuple[ReviewIssueDTO, ...]:
        return tuple(ReviewIssueDTO(field_name=issue.field_name,
                                   reasons=tuple(reason if reason in _SAFE_REASONS else "REVIEW_REQUIRED"
                                                 for reason in issue.reasons)) for issue in issues)

    @_safe
    def get(self, session_id: str, principal: AuthenticatedPrincipal,
            policy: AuthorizationPolicy | None) -> ReviewSessionDTO:
        scope = self._scope(session_id, principal, policy)
        session = self._load(scope)
        submit = permitted(policy, principal, scope, "submit_review_decision")
        source = permitted(policy, principal, scope, "read_source_artifact")
        return ReviewSessionDTO(
            session_id=session.session_id, document_id=session.document_id, form_id=session.form_id,
            revision=session.revision, review_status=session.overall_review_status,
            actionable_items=tuple(self._item(item, scope, principal, source) for item in session.review_items),
            pending_issues=self._issues(session.pending_field_issues),
            outside_scope_issue_indicators=self._issues(session.outside_scope_review_issues),
            ocr_source_status=session.supporting_assessment.source_status,
            can_submit_decision=submit, can_read_source_artifact=source,
        )

    @_safe
    def submit(self, session_id: str, request: SubmitReviewDecisionDTO,
               principal: AuthenticatedPrincipal, policy: AuthorizationPolicy | None) -> ReviewDecisionResponseDTO:
        scope = self._scope(session_id, principal, policy)
        if not permitted(policy, principal, scope, "submit_review_decision"):
            raise ReviewBoundaryError("REVIEW_OPERATION_FORBIDDEN")
        session = self._load(scope)
        item = next((item for item in session.review_items if item.candidate_id == request.candidate_reference), None)
        if item is None:
            raise ReviewBoundaryError("INVALID_REVIEW_DECISION")
        candidate = item.candidate
        action = OCRReviewDecision(
            decision_id=request.decision_id, candidate_id=request.candidate_reference, decision=request.decision,
            field_assignment_status=request.field_assignment_status,
            value_verification_status=request.value_verification_status, reviewed_value=request.reviewed_value,
            review_reason=request.review_reason,
            reviewer_reference=request.reviewer_reference if request.reviewer_reference is not None else REVIEWER_REFERENCE_OMITTED,
            reviewer_note=request.reviewer_note, reviewed_at=request.declared_reviewed_at,
            source_image_sha256=candidate.source_image_sha256, document_id=scope.document_id,
            form_id=scope.form_id, page_number=candidate.page_number,
        )
        context = TrustedReviewContext(principal.issuer, principal.subject, scope.document_id, scope.form_id, True)
        outcome = self.persistence.apply_decision(session_id, action, expected_revision=request.expected_revision, context=context)
        self._check_session(outcome.session, scope)
        reviewed = next((item for item in outcome.session.review_items
                         if item.candidate_id == request.candidate_reference), None)
        if reviewed is None:
            raise ReviewBoundaryError("REVIEW_INTEGRITY_FAILURE")
        source = permitted(policy, principal, scope, "read_source_artifact")
        return ReviewDecisionResponseDTO(
            session_id=session_id, form_id=scope.form_id, revision=outcome.session.revision,
            review_status=outcome.session.overall_review_status, replay=outcome.replay,
            item=self._item(reviewed, scope, principal, source),
        )
