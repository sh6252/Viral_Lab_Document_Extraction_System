"""Real PostgreSQL migration, restart, race, rollback and audit invariants."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta, timezone
from threading import Barrier

import pytest
from sqlalchemy import delete, inspect, select, text, update
from sqlalchemy.exc import SQLAlchemyError

from backend.persistence import review_codec as codec
from backend.persistence.review_models import events, items, sessions
from backend.persistence.review_repository import ReviewPersistenceError, ReviewSessionRepository
from backend.services.extraction.ocr_review import OCRReviewItem
from backend.services.extraction.review_workflow import ReviewWorkflowService
from backend.services.review_persistence import ReviewPersistenceService
from tests.test_ocr_candidates import candidate
from tests.test_ocr_review import decision
from tests.test_review_persistence import actor
from tests.test_review_workflow import NOW, prepare, required

pytestmark = pytest.mark.postgresql


def create(repository, session=None):
    session = required() if session is None else session
    return repository.create(session, actor(), provenance_ingestion_reference="synthetic-ingestion")


def apply(repository, session, *, index=0, revision=None, **changes):
    action = decision(session.review_items[index], **changes)
    return repository.apply_decision_atomic(
        session.session_id, action, session.revision if revision is None else revision,
        actor(), ReviewWorkflowService().apply_decision,
    )


def test_migration_upgrade_and_constraints_exist(pg_engine):
    inspector = inspect(pg_engine)
    assert set(inspector.get_table_names()) == {"alembic_version", "review_sessions", "review_items", "review_events"}
    assert {c["name"] for c in inspector.get_unique_constraints("review_events")} >= {
        "uq_review_event_revision", "uq_review_event_candidate",
    }
    assert {c["name"] for c in inspector.get_foreign_keys("review_events")} == {
        "fk_event_session", "fk_event_actionable_item",
    }
    assert {c["name"] for c in inspector.get_check_constraints("review_events")} >= {
        "ck_event_actionable", "ck_event_revision",
    }
    assert "ix_review_document_form_extraction" in {i["name"] for i in inspector.get_indexes("review_sessions")}


def test_create_load_and_reconnect_preserve_snapshot(repository, pg_engine):
    original = required()
    assert create(repository, original) == original
    pg_engine.dispose()
    fresh = ReviewSessionRepository(pg_engine)
    assert fresh.get(original.session_id) == original
    assert fresh.get(original.session_id).canonical_snapshot_json == original.canonical_snapshot_json


def test_create_retry_returns_current_decided_session(repository):
    original = create(repository)
    accepted = apply(repository, original)
    assert create(repository, original) == accepted.session
    assert len(repository.get_history(original.session_id)) == 1


def test_conflicting_create_preserves_old_aggregate(repository):
    original = create(repository)
    changed = prepare(candidates=[candidate("003")])
    with pytest.raises(ReviewPersistenceError, match="REVIEW_SESSION_CREATE_CONFLICT"):
        create(repository, changed)
    assert repository.get(original.session_id) == original


def test_exact_replay_after_reconnect_keeps_receipt_revision_and_attribution(repository, pg_engine):
    original = create(repository)
    accepted = apply(repository, original)
    before = repository.get_history(original.session_id)
    pg_engine.dispose()
    replay = apply(ReviewSessionRepository(pg_engine), original)
    assert replay.replay
    assert replay.session == accepted.session
    assert repository.get_history(original.session_id) == before
    assert replay.session.revision == 1


def test_same_id_different_payload_and_second_action_conflict(repository):
    original = create(repository)
    apply(repository, original)
    for changes in ({"reviewer_note": "different"}, {"decision_id": "new-id"}):
        with pytest.raises(ValueError, match="REVIEW_DECISION_ID_CONFLICT|REVIEW_DECISION_CONFLICT"):
            apply(repository, original, **changes)
    assert len(repository.get_history(original.session_id)) == 1


def test_stale_revision_changes_neither_root_nor_events(repository):
    original = create(repository)
    with pytest.raises(ValueError, match="STALE_REVIEW_REVISION"):
        apply(repository, original, revision=1)
    assert repository.get(original.session_id) == original
    assert repository.get_history(original.session_id) == ()


@pytest.mark.parametrize("operation", ["update_event", "delete_event", "update_item", "delete_item", "update_preparation", "delete_session"])
def test_append_only_and_immutable_guards(pg_engine, repository, operation):
    original = create(repository)
    apply(repository, original)
    statements = {
        "update_event": update(events).values(review_reason="overwritten"),
        "delete_event": delete(events), "update_item": update(items).values(actionable=False),
        "delete_item": delete(items), "update_preparation": update(sessions).values(document_id="other"),
        "delete_session": delete(sessions),
    }
    with pytest.raises(SQLAlchemyError):
        with pg_engine.begin() as conn:
            conn.execute(statements[operation])
    assert len(repository.get_history(original.session_id)) == 1


def test_history_order_and_projection_consistent(repository):
    original = create(repository, prepare(candidates=[candidate("002"), candidate("003")]))
    first = apply(repository, original)
    final = apply(repository, first.session, index=1, decision_id="second")
    history = repository.get_history(original.session_id)
    assert [event.new_revision for event in history] == [1, 2]
    assert [event.previous_revision for event in history] == [0, 1]
    assert history[1].previous_state == history[0].new_state == "partially_reviewed"
    assert history[1].new_state == "completed"
    assert repository.get(original.session_id) == final.session


def test_evidence_only_fk_guard_and_repository_rejection(repository, pg_engine):
    original = create(repository, prepare(candidates=[candidate(), candidate("002", name="commitment_numbers")]))
    evidence = OCRReviewItem(candidate())
    action = decision(evidence)
    with pytest.raises(ValueError, match="CANDIDATE_NOT_ACTIONABLE"):
        repository.apply_decision_atomic(original.session_id, action, 0, actor(), ReviewWorkflowService().apply_decision)
    accepted = apply(repository, original)
    with pg_engine.connect() as conn:
        template = dict(conn.execute(select(events)).mappings().one())
    template.update(decision_id="forged", candidate_reference=evidence.candidate_id,
                    previous_revision=1, new_revision=2)
    with pytest.raises(SQLAlchemyError):
        with pg_engine.begin() as conn:
            conn.execute(events.insert().values(**template))
    assert repository.get(original.session_id) == accepted.session


def test_event_insert_failure_rolls_back_and_redacts_parameters(repository, monkeypatch, caplog):
    original = create(repository)
    append = repository._append_event

    def invalid_insert(conn, values):
        values["candidate_reference"] = "f" * 64
        values["reviewed_value"] = "SYNTHETIC_PRIVATE_BIND"
        append(conn, values)

    monkeypatch.setattr(repository, "_append_event", invalid_insert)
    with pytest.raises(ReviewPersistenceError, match="^REVIEW_DATABASE_ERROR$") as error:
        apply(repository, original)
    assert "SYNTHETIC_PRIVATE_BIND" not in str(error.value) + caplog.text
    assert repository.get(original.session_id) == original
    assert not repository.get_history(original.session_id)


def test_root_update_failure_rolls_back_inserted_event(repository, monkeypatch):
    original = create(repository)

    def failed_cas(conn, before, after):
        raise ReviewPersistenceError("STALE_REVIEW_REVISION")

    monkeypatch.setattr(repository, "_update_root", failed_cas)
    with pytest.raises(ReviewPersistenceError, match="STALE_REVIEW_REVISION"):
        apply(repository, original)
    assert repository.get(original.session_id) == original
    assert not repository.get_history(original.session_id)


def test_failure_after_both_writes_also_rolls_back(repository, monkeypatch):
    original = create(repository)
    update_root = repository._update_root

    def failed_after_update(conn, before, after):
        update_root(conn, before, after)
        raise ReviewPersistenceError("SYNTHETIC_ABORT")

    monkeypatch.setattr(repository, "_update_root", failed_after_update)
    with pytest.raises(ReviewPersistenceError, match="SYNTHETIC_ABORT"):
        apply(repository, original)
    assert repository.get(original.session_id) == original
    assert not repository.get_history(original.session_id)


@pytest.mark.parametrize("race", ["identical", "conflicting_payload", "different_items"])
def test_parallel_decision_requests(repository, pg_engine, race):
    original = create(repository, prepare(candidates=[candidate("002"), candidate("003")]))
    barrier = Barrier(2)

    def worker(index):
        repo = ReviewSessionRepository(pg_engine)
        item = 1 if race == "different_items" and index else 0
        options = {"reviewer_note": "different"} if race == "conflicting_payload" and index else {}
        if race == "different_items" and index:
            options["decision_id"] = "other-id"
        barrier.wait(timeout=10)
        try:
            return apply(repo, original, index=item, **options)
        except ValueError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, [0, 1]))
    successes = [value for value in results if not isinstance(value, str)]
    if race == "identical":
        assert len(successes) == 2 and sum(value.replay for value in successes) == 1
    else:
        assert len(successes) == 1
        assert any("CONFLICT" in value or "STALE_REVIEW_REVISION" in value
                   for value in results if isinstance(value, str))
    assert repository.get(original.session_id).revision == 1
    assert len(repository.get_history(original.session_id)) == 1


@pytest.mark.parametrize("state", ["unresolved", "rejected"])
def test_rejected_unresolved_and_exact_human_text_persist(repository, pg_engine, state):
    original = create(repository)
    outcome = apply(repository, original, decision=state, field_assignment_status="unresolved",
                    reviewed_value=" 000-12/3) בדיקה ")
    pg_engine.dispose()
    loaded = repository.get(original.session_id)
    assert loaded == outcome.session
    assert loaded.review_items[0].approved_value is None
    assert repository.get_history(original.session_id)[0].decision.reviewed_value == " 000-12/3) בדיקה "
    assert loaded.overall_review_status == ("completed" if state == "rejected" else "partially_reviewed")


def test_trusted_identity_is_separate_and_replay_cannot_reassign_actor(repository):
    original = create(repository)
    service = ReviewPersistenceService(repository)
    action = decision(original.review_items[0], reviewer_reference="human-entered-reference")
    service.apply_decision(original.session_id, action, expected_revision=0, context=actor())
    persisted = repository.get_history(original.session_id)[0]
    assert persisted.principal_subject == "trusted-subject"
    assert persisted.decision.reviewer_reference == "human-entered-reference"
    with pytest.raises(ReviewPersistenceError, match="REVIEW_ACTOR_CONFLICT"):
        service.apply_decision(original.session_id, action, expected_revision=0, context=actor(subject="another"))


def test_equal_timestamp_offsets_replay_without_rewriting_original_representation(repository):
    original = create(repository)
    accepted = apply(repository, original)
    action = decision(original.review_items[0], reviewed_at=NOW.astimezone(timezone(timedelta(hours=2))))
    replay = repository.apply_decision_atomic(original.session_id, action, 0, actor(), ReviewWorkflowService().apply_decision)
    assert replay.replay and replay.session == accepted.session
    assert repository.get_history(original.session_id)[0].decision.reviewed_at.isoformat() == NOW.isoformat()


@pytest.mark.parametrize("column,value", [("preparation_sha256", "a" * 64), ("preparation_json", "{}")])
def test_privileged_corruption_fails_closed(repository, pg_engine, column, value):
    original = create(repository)
    # Simulate privileged tampering only within this disposable schema, not normal writes.
    with pg_engine.begin() as conn:
        conn.execute(text("ALTER TABLE review_sessions DISABLE TRIGGER USER"))
        conn.execute(update(sessions).values(**{column: value}))
        conn.execute(text("ALTER TABLE review_sessions ENABLE TRIGGER USER"))
    with pytest.raises(codec.ReviewIntegrityError, match="REVIEW_STORAGE_INTEGRITY_ERROR"):
        repository.get(original.session_id)


def test_consistent_reads_while_another_connection_commits(repository, pg_engine):
    original = create(repository)
    barrier = Barrier(2)

    def writer():
        barrier.wait(timeout=10)
        apply(ReviewSessionRepository(pg_engine), original)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(writer)
        barrier.wait(timeout=10)
        for _ in range(5):
            loaded = repository.get(original.session_id)
            assert loaded.revision == len(loaded.decision_history)
        future.result(timeout=15)
    assert repository.get(original.session_id).revision == 1


def test_multi_form_catalog_never_admits_other_form_action(repository):
    from tests.test_review_workflow import multi_form
    extracted, pages = multi_form()
    prepared = prepare(extracted, [candidate("002", form_id="form-2")], form_page_images=pages)
    loaded = create(repository, prepared)
    assert not loaded.review_items
    assert len(loaded.supporting_assessment.fields) == 4


def test_create_race_is_idempotent(repository, pg_engine):
    original = required()
    barrier = Barrier(2)

    def worker(_):
        barrier.wait(timeout=10)
        return create(ReviewSessionRepository(pg_engine), original)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(worker, [0, 1])) == [original, original]


def test_approved_unicode_punctuation_and_leading_zeroes_survive_restart(repository, pg_engine):
    original = create(repository)
    accepted = apply(repository, original, reviewed_value=" 000-12/3) בדיקה ")
    pg_engine.dispose()
    loaded = ReviewSessionRepository(pg_engine).get(original.session_id)
    assert loaded == accepted.session
    assert loaded.review_items[0].approved_value == " 000-12/3) בדיקה "
    assert loaded.canonical_snapshot_json == original.canonical_snapshot_json


@pytest.mark.parametrize("column,value", [("storage_codec_version", "unknown"), ("current_projection_jsonb", {})])
def test_unknown_codec_and_projection_corruption_fail_closed(repository, pg_engine, column, value):
    original = create(repository)
    with pg_engine.begin() as conn:
        conn.execute(text("ALTER TABLE review_sessions DISABLE TRIGGER USER"))
        conn.execute(update(sessions).values(**{column: value}))
        conn.execute(text("ALTER TABLE review_sessions ENABLE TRIGGER USER"))
    with pytest.raises(codec.ReviewIntegrityError):
        repository.get(original.session_id)


def test_scope_lookup_reads_only_identity_columns(repository, pg_engine, monkeypatch):
    from sqlalchemy import event
    from backend.core.review_access import ReviewResourceScope
    original = create(repository)
    observed = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        observed.append(statement)

    monkeypatch.setattr(repository, "_load", lambda *args: pytest.fail("scope lookup must not load aggregate"))
    event.listen(pg_engine, "before_cursor_execute", capture)
    try:
        scope = repository.get_scope(original.session_id)
    finally:
        event.remove(pg_engine, "before_cursor_execute", capture)
    assert scope == ReviewResourceScope(original.session_id, original.document_id, original.form_id)
    assert len(observed) == 1
    for forbidden in ("canonical_snapshot", "preparation_json", "review_events", "review_items", "current_projection"):
        assert forbidden not in observed[0]


def test_unknown_scope_has_safe_not_found(repository):
    with pytest.raises(ReviewPersistenceError, match="^REVIEW_SESSION_NOT_FOUND$"):
        repository.get_scope("unknown-synthetic-session")


def test_api_adapter_uses_durable_decision_replay_after_reconnect(repository, pg_engine):
    from fastapi.testclient import TestClient
    from backend.main import create_app
    from backend.services.review_api import ReviewAPIService
    from tests.test_review_api import SyntheticAuthenticator, ExactPolicy, PRINCIPAL, body, PATH

    original = create(repository)
    policy = ExactPolicy(repository.get_scope(original.session_id))
    client = TestClient(create_app(review_api_service=ReviewAPIService(repository),
                                  review_authenticator=SyntheticAuthenticator(), review_authorization_policy=policy))
    request = body(original, reviewed_value=" 000-12/3) בדיקה ")
    first = client.post(PATH + "/decisions", json=request)
    assert first.status_code == 200 and first.json()["replay"] is False
    pg_engine.dispose()
    reconnected = ReviewSessionRepository(pg_engine)
    client = TestClient(create_app(review_api_service=ReviewAPIService(reconnected),
                                  review_authenticator=SyntheticAuthenticator(), review_authorization_policy=policy))
    replay = client.post(PATH + "/decisions", json=request)
    assert replay.status_code == 200 and replay.json()["replay"] is True
    assert replay.json()["revision"] == 1
    assert reconnected.get(original.session_id).canonical_snapshot_json == original.canonical_snapshot_json
    history = reconnected.get_history(original.session_id)
    assert len(history) == 1 and history[0].principal_subject == PRINCIPAL.subject
    assert history[0].decision.reviewer_reference == "review-api-v1:reference-omitted"
    denied = client.post(PATH + "/decisions", json={**request, "reviewer_note": "different"})
    assert denied.status_code == 409 and len(reconnected.get_history(original.session_id)) == 1
