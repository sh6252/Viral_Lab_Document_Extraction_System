"""Internal authorized persistence boundary, not authentication or a public API."""

from backend.persistence.review_repository import (
    PersistedReviewEvent, ReviewSessionRepository, TrustedReviewContext, authorize,
)
from backend.services.extraction.ocr_review import OCRReviewDecision
from backend.services.extraction.review_workflow import ReviewOutcome, ReviewSession, ReviewWorkflowService


class ReviewPersistenceService:
    def __init__(self, repository: ReviewSessionRepository):
        self.repository = repository
        self.workflow = ReviewWorkflowService()

    def create(self, session: ReviewSession, context: TrustedReviewContext,
               *, provenance_ingestion_reference: str) -> ReviewSession:
        authorize(context, session.document_id, session.form_id)
        return self.repository.create(session, context, provenance_ingestion_reference=provenance_ingestion_reference)

    def get(self, session_id: str, context: TrustedReviewContext) -> ReviewSession:
        if not isinstance(context, TrustedReviewContext) or not context.can_review:
            authorize(context, "", "")
        session = self.repository.get(session_id)
        authorize(context, session.document_id, session.form_id)
        return session

    def get_history(self, session_id: str, context: TrustedReviewContext) -> tuple[PersistedReviewEvent, ...]:
        self.get(session_id, context)
        return self.repository.get_history(session_id)

    def apply_decision(self, session_id: str, decision: OCRReviewDecision, *, expected_revision: int,
                       context: TrustedReviewContext) -> ReviewOutcome:
        authorize(context, decision.document_id, decision.form_id)
        return self.repository.apply_decision_atomic(session_id, decision, expected_revision, context,
                                                     self.workflow.apply_decision)
