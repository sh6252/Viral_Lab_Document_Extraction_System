"""SQLAlchemy Core schema for the isolated review aggregate."""

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Column, DateTime, ForeignKeyConstraint,
    Index, MetaData, Table, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()


def text(name: str, *, nullable: bool = False, primary_key: bool = False) -> Column:
    return Column(name, Text, nullable=nullable, primary_key=primary_key)


def stamp(name: str) -> Column:
    return Column(name, DateTime(timezone=True), nullable=False)


def hashes(*names: str) -> list[CheckConstraint]:
    return [CheckConstraint(f"{name} ~ '^[0-9a-f]{{64}}$'", name=f"ck_{name}_sha256") for name in names]


sessions = Table(
    "review_sessions", metadata,
    text("session_id", primary_key=True), text("document_id"), text("form_id"), text("extraction_id"),
    text("input_fingerprint"), text("workflow_version"), text("assessment_version"),
    text("review_version"), text("storage_codec_version"), stamp("prepared_at"),
    text("canonical_snapshot_json"), text("canonical_snapshot_sha256"),
    text("canonical_snapshot_encoding_version"), text("preparation_json"), text("preparation_sha256"),
    text("preparation_encoding_version"), text("ocr_source_status"),
    Column("revision", BigInteger, nullable=False), text("overall_review_status"), stamp("updated_at"),
    Column("current_projection_jsonb", JSONB, nullable=False), stamp("created_received_at"),
    text("creation_principal_issuer"), text("creation_principal_subject"), text("provenance_ingestion_reference"),
    UniqueConstraint("session_id", "document_id", "form_id", name="uq_review_session_scope"),
    CheckConstraint("revision >= 0", name="ck_review_revision"),
    CheckConstraint("overall_review_status IN ('no_review_required','review_required','partially_reviewed','completed')",
                    name="ck_review_state"),
    CheckConstraint("ocr_source_status IN ('succeeded','disabled','unavailable','failed')", name="ck_ocr_source"),
    *[CheckConstraint(f"length(btrim({name})) > 0", name=f"ck_session_{name}")
      for name in ("session_id", "document_id", "form_id", "extraction_id")],
    *hashes("input_fingerprint", "canonical_snapshot_sha256", "preparation_sha256"),
)
Index("ix_review_document_form_extraction", sessions.c.document_id, sessions.c.form_id, sessions.c.extraction_id)

items = Table(
    "review_items", metadata,
    text("session_id", primary_key=True), text("candidate_reference", primary_key=True),
    text("document_id"), text("form_id"), text("field_name"),
    Column("actionable", Boolean, nullable=False), text("candidate_json"),
    Column("provider_values_jsonb", JSONB, nullable=False), text("private_artifact_reference", nullable=True),
    ForeignKeyConstraint(["session_id", "document_id", "form_id"],
                         ["review_sessions.session_id", "review_sessions.document_id", "review_sessions.form_id"],
                         ondelete="RESTRICT", name="fk_item_session_scope"),
    UniqueConstraint("session_id", "candidate_reference", "actionable", name="uq_review_item_actionable"),
    CheckConstraint("field_name IN ('central_numbers','commitment_numbers')", name="ck_item_field"),
    *hashes("candidate_reference"),
)

events = Table(
    "review_events", metadata,
    text("session_id", primary_key=True), text("decision_id", primary_key=True), text("candidate_reference"),
    Column("actionable", Boolean, nullable=False), text("event_type"), text("decision_json"),
    text("decision_content_fingerprint"), text("decision_codec_version"), text("decision_status"),
    text("field_assignment_status"), text("value_verification_status"), text("reviewed_value", nullable=True),
    Column("human_correction_occurred", Boolean, nullable=False), text("review_reason"),
    text("reviewer_note", nullable=True), text("declared_reviewer_reference"), stamp("declared_reviewed_at"),
    text("declared_reviewed_at_representation"), text("principal_issuer"), text("principal_subject"),
    stamp("received_at"), Column("previous_revision", BigInteger, nullable=False),
    Column("new_revision", BigInteger, nullable=False), text("previous_state"), text("new_state"),
    ForeignKeyConstraint(["session_id"], ["review_sessions.session_id"], ondelete="RESTRICT", name="fk_event_session"),
    ForeignKeyConstraint(["session_id", "candidate_reference", "actionable"],
                         ["review_items.session_id", "review_items.candidate_reference", "review_items.actionable"],
                         ondelete="RESTRICT", name="fk_event_actionable_item"),
    UniqueConstraint("session_id", "new_revision", name="uq_review_event_revision"),
    UniqueConstraint("session_id", "candidate_reference", name="uq_review_event_candidate"),
    CheckConstraint("actionable = true", name="ck_event_actionable"),
    CheckConstraint("event_type = 'decision_accepted'", name="ck_event_type"),
    CheckConstraint("previous_revision >= 0 AND new_revision = previous_revision + 1", name="ck_event_revision"),
    CheckConstraint("decision_status IN ('approved','rejected','unresolved')", name="ck_event_decision"),
    *[CheckConstraint(f"{name} IN ('unverified','confirmed','rejected','unresolved')", name=f"ck_event_{name}")
      for name in ("field_assignment_status", "value_verification_status")],
    *[CheckConstraint(f"{name} IN ('no_review_required','review_required','partially_reviewed','completed')",
                      name=f"ck_event_{name}") for name in ("previous_state", "new_state")],
    CheckConstraint("length(btrim(decision_id)) > 0", name="ck_event_decision_id"),
    *hashes("candidate_reference", "decision_content_fingerprint"),
)
