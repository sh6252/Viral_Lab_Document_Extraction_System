"""Authorization boundary tests with synthetic internal contexts, not auth itself."""

from unittest.mock import Mock

import pytest

from backend.persistence.review_repository import ReviewPersistenceError, TrustedReviewContext
from backend.services.review_persistence import ReviewPersistenceService
from tests.test_ocr_review import decision
from tests.test_review_workflow import required


def actor(**changes):
    values = dict(issuer="synthetic-runtime", subject="trusted-subject", document_id="doc-1",
                  form_id="form-1", can_review=True)
    values.update(changes)
    return TrustedReviewContext(**values)


@pytest.mark.parametrize("context", [None, actor(can_review=False), actor(document_id="other"), actor(subject="")])
def test_untrusted_or_out_of_scope_context_cannot_create_or_decide(context):
    repository = Mock()
    service = ReviewPersistenceService(repository)
    session = required()
    with pytest.raises(ReviewPersistenceError, match="REVIEW_ACCESS_DENIED"):
        service.create(session, context, provenance_ingestion_reference="synthetic-ingestion")
    with pytest.raises(ReviewPersistenceError, match="REVIEW_ACCESS_DENIED"):
        service.apply_decision(session.session_id, decision(session.review_items[0]), expected_revision=0, context=context)
    assert repository.mock_calls == []


def test_declared_reviewer_is_separate_and_existing_workflow_is_injected():
    repository = Mock()
    service = ReviewPersistenceService(repository)
    session = required()
    action = decision(session.review_items[0], reviewer_reference="not-the-trusted-subject")
    returned = service.apply_decision(session.session_id, action, expected_revision=0, context=actor())
    assert returned is repository.apply_decision_atomic.return_value
    args = repository.apply_decision_atomic.call_args.args
    assert args[1].reviewer_reference == "not-the-trusted-subject"
    assert args[3].subject == "trusted-subject"
    assert args[4].__self__ is service.workflow


def test_wrapper_does_not_return_a_success_if_transaction_fails():
    repository = Mock()
    repository.apply_decision_atomic.side_effect = ReviewPersistenceError("REVIEW_DATABASE_ERROR")
    service = ReviewPersistenceService(repository)
    session = required()
    with pytest.raises(ReviewPersistenceError, match="REVIEW_DATABASE_ERROR"):
        service.apply_decision(session.session_id, decision(session.review_items[0]), expected_revision=0, context=actor())


def test_get_checks_document_scope_before_returning_data():
    repository = Mock()
    repository.get.return_value = required()
    with pytest.raises(ReviewPersistenceError, match="REVIEW_ACCESS_DENIED"):
        ReviewPersistenceService(repository).get("session-1", actor(form_id="other"))
