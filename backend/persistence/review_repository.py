"""Atomic PostgreSQL review aggregate, with safe errors and immutable history."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import wraps
from typing import Callable

from sqlalchemy import Engine, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from backend.core.review_access import ReviewResourceScope
from backend.persistence import review_codec as codec
from backend.persistence.review_models import events, items, sessions
from backend.services.extraction.ocr_candidates import OCRCandidate
from backend.services.extraction.ocr_review import OCRReviewDecision, candidate_reference
from backend.services.extraction.review_workflow import ReviewOutcome, ReviewSession, ReviewWorkflowService


class ReviewPersistenceError(ValueError):
    """Safe reason codes only; never expose driver parameters or exception chains."""


@dataclass(frozen=True, slots=True)
class TrustedReviewContext:
    """Internal assertion from future authenticated runtime, NOT authentication."""

    issuer: str
    subject: str
    document_id: str
    form_id: str
    can_review: bool = False


@dataclass(frozen=True, slots=True)
class PersistedReviewEvent:
    decision: OCRReviewDecision
    principal_issuer: str
    principal_subject: str
    received_at: datetime
    previous_revision: int
    new_revision: int
    previous_state: str
    new_state: str
    decision_content_fingerprint: str


def authorize(context: TrustedReviewContext, document_id: str, form_id: str) -> None:
    if (not isinstance(context, TrustedReviewContext) or context.can_review is not True
            or not all(isinstance(s, str) and s.strip() for s in (context.issuer, context.subject))
            or context.document_id != document_id or context.form_id != form_id):
        raise ReviewPersistenceError("REVIEW_ACCESS_DENIED")


def _safe(method):
    @wraps(method)
    def invoke(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except SQLAlchemyError:
            raise ReviewPersistenceError("REVIEW_DATABASE_ERROR") from None
    return invoke


def _catalog(session: ReviewSession) -> list[dict]:
    actionable = {item.candidate_id for item in session.review_items}
    rows = {}
    for field in session.supporting_assessment.fields:
        if field.form_id != session.form_id:
            continue
        for candidate in field.ocr_candidates:
            reference = candidate_reference(candidate)
            rows[reference] = dict(
                session_id=session.session_id, candidate_reference=reference,
                document_id=session.document_id, form_id=session.form_id, field_name=candidate.field_name,
                actionable=reference in actionable, candidate_json=codec.encode(candidate),
                provider_values_jsonb=[asdict(value) for value in field.provider_values],
                private_artifact_reference=None,
            )
    return [rows[key] for key in sorted(rows)]


def _preparation_columns(session: ReviewSession) -> dict:
    payload = codec.encode(session)
    names = ("session_id", "document_id", "form_id", "extraction_id", "input_fingerprint",
             "workflow_version", "assessment_version", "review_version", "prepared_at",
             "canonical_snapshot_json", "canonical_snapshot_sha256", "canonical_snapshot_encoding_version")
    return dict(**{name: getattr(session, name) for name in names},
                storage_codec_version=codec.CODEC_VERSION, preparation_json=payload,
                preparation_sha256=codec.digest(payload), preparation_encoding_version=codec.CODEC_VERSION,
                ocr_source_status=session.supporting_assessment.source_status)


def _event_columns(before: ReviewSession, outcome: ReviewOutcome, decision: OCRReviewDecision,
                   actor: TrustedReviewContext, receipt: datetime) -> dict:
    item = next(item for item in outcome.session.review_items if item.candidate_id == decision.candidate_id)
    return dict(
        session_id=before.session_id, decision_id=decision.decision_id, candidate_reference=decision.candidate_id,
        actionable=True, event_type="decision_accepted", decision_json=codec.encode(decision),
        decision_content_fingerprint=codec.decision_fingerprint(decision), decision_codec_version=codec.CODEC_VERSION,
        decision_status=decision.decision, field_assignment_status=decision.field_assignment_status,
        value_verification_status=decision.value_verification_status, reviewed_value=decision.reviewed_value,
        human_correction_occurred=item.human_correction_occurred, review_reason=decision.review_reason,
        reviewer_note=decision.reviewer_note, declared_reviewer_reference=decision.reviewer_reference,
        declared_reviewed_at=decision.reviewed_at, declared_reviewed_at_representation=decision.reviewed_at.isoformat(),
        principal_issuer=actor.issuer, principal_subject=actor.subject, received_at=receipt,
        previous_revision=before.revision, new_revision=outcome.session.revision,
        previous_state=before.overall_review_status, new_state=outcome.session.overall_review_status,
    )


class ReviewSessionRepository:
    def __init__(self, engine: Engine, *, clock: Callable[[], datetime] | None = None):
        if engine.dialect.name != "postgresql" or not engine.hide_parameters or engine.echo:
            raise ReviewPersistenceError("SAFE_POSTGRESQL_ENGINE_REQUIRED")
        self.engine = engine
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.workflow = ReviewWorkflowService()

    def _now(self) -> datetime:
        value = self.clock()
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ReviewPersistenceError("TRUSTED_AWARE_CLOCK_REQUIRED")
        return value

    def _root(self, conn, session_id: str, *, lock: bool = False):
        statement = select(sessions).where(sessions.c.session_id == session_id)
        row = conn.execute(statement.with_for_update() if lock else statement).mappings().one_or_none()
        if row is None:
            raise ReviewPersistenceError("REVIEW_SESSION_NOT_FOUND")
        return row

    def _load(self, conn, root) -> ReviewSession:
        if (root["storage_codec_version"] != codec.CODEC_VERSION
                or root["preparation_encoding_version"] != codec.CODEC_VERSION
                or codec.digest(root["preparation_json"]) != root["preparation_sha256"]):
            raise codec.ReviewIntegrityError()
        prepared = codec.validate_preparation(codec.decode(root["preparation_json"], ReviewSession))
        if any(root[name] != value for name, value in _preparation_columns(prepared).items()):
            raise codec.ReviewIntegrityError()
        stored = [dict(row) for row in conn.execute(select(items).where(
            items.c.session_id == prepared.session_id).order_by(items.c.candidate_reference)).mappings()]
        if stored != _catalog(prepared):
            raise codec.ReviewIntegrityError()
        current = prepared
        for event in conn.execute(select(events).where(events.c.session_id == prepared.session_id)
                                  .order_by(events.c.new_revision)).mappings():
            if event["decision_codec_version"] != codec.CODEC_VERSION:
                raise codec.ReviewIntegrityError()
            decision = codec.decode(event["decision_json"], OCRReviewDecision)
            actor = TrustedReviewContext(event["principal_issuer"], event["principal_subject"],
                                         prepared.document_id, prepared.form_id, True)
            authorize(actor, prepared.document_id, prepared.form_id)
            try:
                outcome = self.workflow.apply_decision(current, decision, expected_revision=current.revision,
                                                       received_at=event["received_at"])
            except ValueError:
                raise codec.ReviewIntegrityError() from None
            expected = _event_columns(current, outcome, decision, actor, event["received_at"])
            if outcome.replay or dict(event) != expected:
                raise codec.ReviewIntegrityError()
            current = outcome.session
        if (root["revision"] != current.revision or root["overall_review_status"] != current.overall_review_status
                or root["updated_at"] != current.updated_at
                or root["current_projection_jsonb"] != codec.projection(current)):
            raise codec.ReviewIntegrityError()
        return current

    @_safe
    def create(self, prepared_session: ReviewSession, trusted_creation_context: TrustedReviewContext,
               *, provenance_ingestion_reference: str) -> ReviewSession:
        prepared = codec.validate_preparation(prepared_session)
        authorize(trusted_creation_context, prepared.document_id, prepared.form_id)
        if not isinstance(provenance_ingestion_reference, str) or not provenance_ingestion_reference.strip():
            raise ReviewPersistenceError("PROVENANCE_INGESTION_REFERENCE_REQUIRED")
        columns = _preparation_columns(prepared)
        with self.engine.begin() as conn:
            inserted = conn.execute(insert(sessions).values(
                **columns, revision=0, overall_review_status=prepared.overall_review_status,
                updated_at=prepared.updated_at, current_projection_jsonb=codec.projection(prepared),
                created_received_at=self._now(), creation_principal_issuer=trusted_creation_context.issuer,
                creation_principal_subject=trusted_creation_context.subject,
                provenance_ingestion_reference=provenance_ingestion_reference,
            ).on_conflict_do_nothing(index_elements=[sessions.c.session_id]).returning(sessions.c.session_id)).scalar_one_or_none()
            if inserted is not None:
                catalog = _catalog(prepared)
                if catalog:
                    conn.execute(items.insert(), catalog)
            root = self._root(conn, prepared.session_id, lock=True)
            if any(root[name] != value for name, value in columns.items()):
                raise ReviewPersistenceError("REVIEW_SESSION_CREATE_CONFLICT")
            return self._load(conn, root)

    @_safe
    def get_scope(self, session_id: str) -> ReviewResourceScope:
        """Identity metadata only, before authorization; no aggregate/patient read."""
        with self.engine.connect() as conn:
            row = conn.execute(select(sessions.c.session_id, sessions.c.document_id, sessions.c.form_id)
                               .where(sessions.c.session_id == session_id)).one_or_none()
            if row is None:
                raise ReviewPersistenceError("REVIEW_SESSION_NOT_FOUND")
            return ReviewResourceScope(*row)

    @_safe
    def get(self, session_id: str) -> ReviewSession:
        with self.engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
            with conn.begin():
                return self._load(conn, self._root(conn, session_id))

    @_safe
    def get_history(self, session_id: str) -> tuple[PersistedReviewEvent, ...]:
        with self.engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
            with conn.begin():
                self._load(conn, self._root(conn, session_id))
                return tuple(PersistedReviewEvent(
                    codec.decode(row["decision_json"], OCRReviewDecision), row["principal_issuer"],
                    row["principal_subject"], row["received_at"], row["previous_revision"], row["new_revision"],
                    row["previous_state"], row["new_state"], row["decision_content_fingerprint"],
                ) for row in conn.execute(select(events).where(events.c.session_id == session_id)
                                           .order_by(events.c.new_revision)).mappings())

    def _append_event(self, conn, values: dict) -> None:
        conn.execute(events.insert().values(**values))

    def _update_root(self, conn, before: ReviewSession, after: ReviewSession) -> None:
        changed = conn.execute(update(sessions).where(
            sessions.c.session_id == before.session_id, sessions.c.revision == before.revision,
        ).values(revision=after.revision, overall_review_status=after.overall_review_status,
                 updated_at=after.updated_at, current_projection_jsonb=codec.projection(after)))
        if changed.rowcount != 1:
            raise ReviewPersistenceError("STALE_REVIEW_REVISION")

    @_safe
    def apply_decision_atomic(self, session_id: str, decision: OCRReviewDecision, expected_revision: int,
                              trusted_actor: TrustedReviewContext, transition: Callable) -> ReviewOutcome:
        with self.engine.begin() as conn:
            root = self._root(conn, session_id, lock=True)
            authorize(trusted_actor, root["document_id"], root["form_id"])
            current = self._load(conn, root)
            old = conn.execute(select(events).where(events.c.session_id == session_id,
                                                     events.c.decision_id == decision.decision_id)).mappings().one_or_none()
            if old is not None:
                if codec.decode(old["decision_json"], OCRReviewDecision) != decision:
                    raise ReviewPersistenceError("REVIEW_DECISION_ID_CONFLICT")
                if (old["principal_issuer"], old["principal_subject"]) != (trusted_actor.issuer, trusted_actor.subject):
                    raise ReviewPersistenceError("REVIEW_ACTOR_CONFLICT")
                return transition(current, decision, expected_revision=expected_revision, received_at=old["received_at"])
            receipt = self._now()
            outcome = transition(current, decision, expected_revision=expected_revision, received_at=receipt)
            if outcome.replay or outcome.session.revision != current.revision + 1:
                raise codec.ReviewIntegrityError()
            self._append_event(conn, _event_columns(current, outcome, decision, trusted_actor, receipt))
            self._update_root(conn, current, outcome.session)
            return outcome
