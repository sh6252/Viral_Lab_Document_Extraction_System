"""Injected synthetic authentication/policy only; never a production default."""

from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from backend.api.review_schemas import REVIEWER_REFERENCE_OMITTED
from backend.core.review_access import AuthenticatedPrincipal, ReviewResourceScope
from backend.main import create_app
from backend.persistence.review_codec import ReviewIntegrityError
from backend.persistence.review_repository import ReviewPersistenceError, authorize
from backend.services.extraction.ocr_candidates import FieldAssignmentEvidence
from backend.services.extraction.review_workflow import ReviewWorkflowService
from backend.services.review_api import ReviewAPIService
from tests.test_ocr_candidates import candidate
from tests.test_review_workflow import LATER, NOW, multi_form, prepare, required

PRINCIPAL = AuthenticatedPrincipal("PRIVATE_VERIFIED_ISSUER", "PRIVATE_VERIFIED_SUBJECT", ("broad-role",))
AUTO = object()
PATH = "/api/v1/review-sessions/session-1"


class SyntheticAuthenticator:
    def __init__(self, principal=PRINCIPAL):
        self.principal = principal

    async def authenticate(self, request):
        return self.principal


class ExactPolicy:
    def __init__(self, scope, operations=("read_review", "submit_review_decision")):
        self.grants = {(PRINCIPAL.issuer, PRINCIPAL.subject, scope.session_id, scope.document_id,
                        scope.form_id, operation) for operation in operations}
        self.calls = []

    def allows(self, principal, scope, operation):
        self.calls.append((principal, scope, operation))
        return (principal.issuer, principal.subject, scope.session_id, scope.document_id, scope.form_id,
                operation) in self.grants


class SyntheticRepository:
    """API fixture reusing the pure workflow; durable behavior tested on PostgreSQL."""

    def __init__(self, session):
        self.session = session
        self.calls = []
        self.contexts = []

    def get_scope(self, session_id):
        self.calls.append("scope")
        if session_id != self.session.session_id:
            raise ReviewPersistenceError("REVIEW_SESSION_NOT_FOUND")
        return ReviewResourceScope(session_id, self.session.document_id, self.session.form_id)

    def get(self, session_id):
        self.calls.append("load")
        assert session_id == self.session.session_id
        return self.session

    def apply_decision_atomic(self, session_id, decision, expected_revision, actor, transition):
        self.calls.append("apply")
        self.contexts.append(actor)
        authorize(actor, self.session.document_id, self.session.form_id)
        outcome = transition(self.session, decision, expected_revision=expected_revision, received_at=LATER)
        self.session = outcome.session
        return outcome


def build_client(session=None, *, operations=("read_review", "submit_review_decision"),
                 authenticator=AUTO, policy=AUTO):
    repo = SyntheticRepository(required() if session is None else session)
    scope = ReviewResourceScope(repo.session.session_id, repo.session.document_id, repo.session.form_id)
    policy = ExactPolicy(scope, operations) if policy is AUTO else policy
    authenticator = SyntheticAuthenticator() if authenticator is AUTO else authenticator
    app = create_app(review_api_service=ReviewAPIService(repo), review_authenticator=authenticator,
                     review_authorization_policy=policy)
    return TestClient(app), repo, policy


def body(session, **changes):
    values = dict(decision_id="api-decision-1", candidate_reference=session.review_items[0].candidate_id,
                  expected_revision=session.revision, decision="approved", field_assignment_status="confirmed",
                  value_verification_status="confirmed", reviewed_value="000123", review_reason="Synthetic verification",
                  reviewer_note=None, reviewer_reference=None, declared_reviewed_at=NOW.isoformat())
    values.update(changes)
    return values


@pytest.mark.parametrize("authenticator", [None, SyntheticAuthenticator(None), SyntheticAuthenticator({"subject": "forged"})])
def test_missing_or_invalid_authentication_fails_before_scope_or_body(authenticator):
    client, repo, _ = build_client(authenticator=authenticator)
    for response in (client.get(PATH, headers={"X-User-Subject": "forged"}),
                     client.post(PATH + "/decisions", content="invalid private JSON")):
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
        assert response.headers["cache-control"] == "no-store"
    assert not repo.calls


def test_default_application_is_closed_and_still_has_health_and_extraction_routes():
    client = TestClient(create_app())
    assert client.get(PATH).status_code == 401
    assert client.get("/api/v1/health").status_code == 200
    assert "/api/v1/extractions" in client.get("/openapi.json").json()["paths"]


def test_absent_policy_fails_closed_without_sensitive_load():
    client, repo, _ = build_client(policy=None)
    assert client.get(PATH).status_code == 404
    assert repo.calls == []


def test_invisible_and_nonexistent_resources_have_identical_errors():
    client, repo, _ = build_client(operations=())
    denied = client.get(PATH)
    missing = client.get("/api/v1/review-sessions/missing")
    assert denied.status_code == missing.status_code == 404
    assert denied.json() == missing.json()
    assert "load" not in repo.calls


@pytest.mark.parametrize("foreign", ["document", "form", "session"])
def test_cross_resource_grants_do_not_authorize(foreign):
    client, repo, policy = build_client()
    scope = ReviewResourceScope("different" if foreign == "session" else "session-1",
                               "different" if foreign == "document" else "doc-1",
                               "different" if foreign == "form" else "form-1")
    policy.grants = ExactPolicy(scope).grants
    assert client.get(PATH).status_code == 404
    assert "load" not in repo.calls


def test_read_only_access_loads_without_any_writable_context_and_cannot_post():
    client, repo, _ = build_client(operations=("read_review",))
    response = client.get(PATH)
    assert response.status_code == 200
    assert response.json()["can_submit_decision"] is False
    assert response.json()["can_read_source_artifact"] is False
    assert client.post(PATH + "/decisions", json=body(repo.session)).status_code == 403
    assert not repo.contexts and "apply" not in repo.calls


def test_submit_without_read_permission_is_masked_and_never_loads():
    client, repo, _ = build_client(operations=("submit_review_decision",))
    response = client.post(PATH + "/decisions", json=body(repo.session))
    assert response.status_code == 404 and "load" not in repo.calls


def test_projection_is_allowlisted_recursively_and_multiform_data_is_not_leaked():
    extracted, pages = multi_form()
    extracted.logical_forms[1].fields.central_numbers.values[0].raw = "OTHER_FORM_PROVIDER_SECRET"
    batch = [candidate("002", field_assignment_evidence=FieldAssignmentEvidence(
        "central_numbers", "label_relation", "C:/private/evaluation/artifacts/locator")),
        candidate("987654321", form_id="form-2")]
    session = prepare(extracted, batch, form_page_images=pages)
    client, repo, _ = build_client(session)
    response = client.get(PATH)
    assert response.status_code == 200
    content = response.json()
    assert len(content["actionable_items"]) == 1
    assert content["actionable_items"][0]["ocr_value"]["raw"] == "002"
    assert content["outside_scope_issue_indicators"]
    assert all(set(issue) == {"field_name", "reasons"} for issue in content["outside_scope_issue_indicators"])
    for forbidden in ("canonical_snapshot", "preparation_json", "input_fingerprint", "supporting_assessment",
                      "PRIVATE_VERIFIED_", "OTHER_FORM_PROVIDER_SECRET", "987654321", "C:/private", "codec",
                      "source_run_id", "field_assignment_evidence"):
        assert forbidden not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert repo.calls == ["scope", "load"]


def test_authorized_decision_mapping_replay_and_canonical_isolation():
    client, repo, policy = build_client()
    snapshot = repo.session.canonical_snapshot_json
    request = body(repo.session, reviewed_value=" 000-12/3) בדיקה ")
    accepted = client.post(PATH + "/decisions", json=request)
    assert accepted.status_code == 200
    result = accepted.json()
    assert result["revision"] == 1 and result["replay"] is False
    assert result["item"]["prior_decision"]["reviewed_value"] == request["reviewed_value"]
    assert result["item"]["prior_decision"]["reviewer_reference"] is None
    replay = client.post(PATH + "/decisions", json=request)
    assert replay.status_code == 200 and replay.json()["replay"] is True
    assert repo.session.revision == 1
    assert repo.session.canonical_snapshot_json == snapshot
    assert repo.contexts[0].issuer == PRINCIPAL.issuer and repo.contexts[0].subject == PRINCIPAL.subject
    assert repo.session.decision_history[0].decision.reviewer_reference == REVIEWER_REFERENCE_OMITTED
    policy.grants.clear()
    assert client.post(PATH + "/decisions", json=request).status_code == 404
    assert repo.session.revision == 1


def test_new_stale_action_and_conflicting_decisions_are_mapped():
    session = prepare(candidates=[candidate("002"), candidate("003")])
    client, repo, _ = build_client(session)
    first = body(session)
    assert client.post(PATH + "/decisions", json=first).status_code == 200
    stale = body(session, candidate_reference=session.review_items[1].candidate_id, decision_id="other-id")
    response = client.post(PATH + "/decisions", json=stale)
    assert response.status_code == 409 and response.json()["error"]["code"] == "REVIEW_STALE_REVISION"
    for changes in ({"reviewer_note": "different"}, {"decision_id": "second-action", "expected_revision": 1}):
        response = client.post(PATH + "/decisions", json={**first, **changes})
        assert response.status_code == 409 and response.json()["error"]["code"] == "REVIEW_DECISION_CONFLICT"


@pytest.mark.parametrize("changes", [
    {"value_verification_status": "unresolved"}, {"reviewed_value": None},
    {"reviewer_reference": REVIEWER_REFERENCE_OMITTED}, {"declared_reviewed_at": "2026-01-01T00:00:00"},
    {"declared_reviewed_at": 12345}, {"expected_revision": True}, {"expected_revision": "0"},
    {"issuer": "FORGED_ISSUER"}, {"subject": "FORGED_SUBJECT"}, {"can_review": True},
    {"received_at": NOW.isoformat()}, {"document_id": "forged"}, {"form_id": "forged"},
    {"page_number": 2}, {"source_image_sha256": "a" * 64},
    {"can_submit_decision": True}, {"can_read_source_artifact": True},
])
def test_invalid_requests_do_not_echo_values_or_forge_context(changes):
    client, repo, _ = build_client()
    request = body(repo.session, reviewed_value="PRIVATE_REVIEWED_TEXT", reviewer_note="PRIVATE_REVIEW_NOTE")
    request.update(changes)
    response = client.post(PATH + "/decisions", json=request)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REVIEW_DECISION"
    assert "PRIVATE_REVIEW" not in response.text and "FORGED_" not in response.text
    assert repo.session.revision == 0


@pytest.mark.parametrize("error,expected", [
    (ReviewPersistenceError("REVIEW_DATABASE_ERROR"), 503), (ReviewIntegrityError(), 500),
    (RuntimeError("SELECT PRIVATE_SQL_BIND"), 503),
])
def test_repository_errors_are_safe_and_do_not_echo_sql_or_data(error, expected):
    client, repo, _ = build_client()
    repo.get = Mock(side_effect=error)
    response = client.get(PATH)
    assert response.status_code == expected
    assert "PRIVATE_SQL_BIND" not in response.text and "SELECT" not in response.text


def test_corrupt_loaded_scope_is_not_projected():
    client, repo, _ = build_client()
    repo.get = Mock(return_value=replace(repo.session, document_id="other"))
    assert client.get(PATH).status_code == 500


def test_provided_reference_is_untrusted_metadata_and_principal_cannot_be_header_forged():
    client, repo, policy = build_client()
    response = client.post(PATH + "/decisions", json=body(repo.session, reviewer_reference="Declared person"),
                           headers={"X-User-Subject": "forged", "X-Request-ID": "forged-correlation"})
    assert response.status_code == 200
    assert response.json()["item"]["prior_decision"]["reviewer_reference"] == "Declared person"
    assert repo.contexts[0].subject == PRINCIPAL.subject
    assert all(principal.correlation_id != "forged-correlation" for principal, _, _ in policy.calls)


def test_unavailable_evidence_and_no_artifact_http_endpoint():
    client, repo, _ = build_client(operations=("read_review", "read_source_artifact"))
    response = client.get(PATH)
    evidence = response.json()["actionable_items"][0]["evidence"]
    assert response.json()["can_read_source_artifact"] is True
    assert evidence["availability"] == "unavailable" and evidence["artifact_reference"] is None
    assert client.get(PATH + "/artifacts/arbitrary-path").status_code == 404


def test_authenticator_exception_is_safe_and_never_resolves_scope():
    class BrokenAuthenticator:
        async def authenticate(self, request):
            raise RuntimeError("PRIVATE_AUTH_DETAILS")
    client, repo, _ = build_client(authenticator=BrokenAuthenticator())
    response = client.get(PATH)
    assert response.status_code == 401 and "PRIVATE_AUTH_DETAILS" not in response.text
    assert not repo.calls


def test_authenticated_malformed_json_is_sanitized():
    client, repo, _ = build_client()
    response = client.post(PATH + "/decisions", content='{"private":"PRIVATE_MALFORMED')
    assert response.status_code == 422 and "PRIVATE_MALFORMED" not in response.text
    assert repo.session.revision == 0


def test_omitted_reference_and_different_stale_precondition_replay():
    client, repo, _ = build_client()
    request = body(repo.session)
    request.pop("reviewer_reference")
    assert client.post(PATH + "/decisions", json=request).status_code == 200
    response = client.post(PATH + "/decisions", json={**request, "expected_revision": 999})
    assert response.status_code == 200 and response.json()["replay"] is True
    assert response.json()["item"]["prior_decision"]["reviewer_reference"] is None


def test_evidence_only_candidate_is_not_actionable_through_post():
    from backend.services.extraction.ocr_review import candidate_reference
    session = prepare(candidates=[candidate(), candidate("002", name="commitment_numbers")])
    client, repo, _ = build_client(session)
    response = client.post(PATH + "/decisions", json=body(session, candidate_reference=candidate_reference(candidate())))
    assert response.status_code == 422 and not repo.contexts


def test_actor_conflict_is_forbidden_without_attribution_disclosure():
    client, repo, _ = build_client()
    repo.apply_decision_atomic = Mock(side_effect=ReviewPersistenceError("REVIEW_ACTOR_CONFLICT"))
    response = client.post(PATH + "/decisions", json=body(repo.session))
    assert response.status_code == 403 and "PRIVATE_VERIFIED" not in response.text


def test_routes_do_not_call_extraction_or_create_a_database_engine(monkeypatch):
    from backend.services.extraction.orchestrator import ExtractionOrchestrator
    from backend.services.extraction.gemini_adapter import GeminiExtractionAdapter
    from backend.persistence import db

    def unexpected(*args, **kwargs):
        pytest.fail("review must not call extraction/provider or construct a DB engine")

    monkeypatch.setattr(ExtractionOrchestrator, "extract", unexpected)
    monkeypatch.setattr(GeminiExtractionAdapter, "extract", unexpected)
    monkeypatch.setattr(db, "create_review_engine", unexpected)
    client, repo, _ = build_client()
    assert client.get(PATH).status_code == 200
    assert client.post(PATH + "/decisions", json=body(repo.session)).status_code == 200
