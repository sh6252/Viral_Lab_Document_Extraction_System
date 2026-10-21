"""Durable seven-field records; deliberately separate from OCR review tables."""

from sqlalchemy import (
    BigInteger, CheckConstraint, Column, DateTime, ForeignKey, ForeignKeyConstraint,
    Integer, LargeBinary, MetaData, Table, Text, UniqueConstraint,
)

metadata = MetaData()

documents = Table(
    "business_documents", metadata,
    Column("document_id", Text, primary_key=True),
    Column("review_token", Text, nullable=False),
    Column("source_bytes", LargeBinary, nullable=False),
    Column("source_sha256", Text, nullable=False),
    Column("mime_type", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("prompt_version", Text, nullable=False),
    Column("pipeline_version", Text, nullable=False),
    Column("extraction_id", Text),
    Column("canonical_snapshot_json", Text),
    Column("canonical_snapshot_sha256", Text),
    Column("failure_json", Text),
    Column("failure_sha256", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("status IN ('processing','succeeded','failed')", name="ck_business_document_status"),
    CheckConstraint("mime_type IN ('application/pdf','image/png','image/jpeg')", name="ck_business_document_mime"),
    CheckConstraint("octet_length(source_bytes) > 0 AND octet_length(source_bytes) <= 20971520",
                    name="ck_business_source_size"),
    CheckConstraint("source_sha256 ~ '^[0-9a-f]{64}$'", name="ck_business_source_hash"),
    CheckConstraint("length(review_token) >= 32", name="ck_business_review_token"),
    CheckConstraint("(status = 'succeeded' AND extraction_id IS NOT NULL AND canonical_snapshot_json IS NOT NULL "
                    "AND canonical_snapshot_sha256 IS NOT NULL AND canonical_snapshot_sha256 ~ '^[0-9a-f]{64}$' "
                    "AND failure_json IS NULL AND failure_sha256 IS NULL) OR "
                    "(status = 'failed' AND extraction_id IS NULL AND canonical_snapshot_json IS NULL "
                    "AND canonical_snapshot_sha256 IS NULL AND failure_json IS NOT NULL "
                    "AND failure_sha256 IS NOT NULL AND failure_sha256 ~ '^[0-9a-f]{64}$') OR "
                    "(status = 'processing' AND extraction_id IS NULL AND canonical_snapshot_json IS NULL "
                    "AND canonical_snapshot_sha256 IS NULL AND failure_json IS NULL AND failure_sha256 IS NULL)",
                    name="ck_business_document_outcome"),
)

records = Table(
    "business_records", metadata,
    Column("document_id", Text, ForeignKey("business_documents.document_id", ondelete="RESTRICT"), primary_key=True),
    Column("form_id", Text, primary_key=True),
    Column("ordinal", Integer, nullable=False),
    Column("revision", BigInteger, nullable=False),
    Column("review_status", Text, nullable=False),
    Column("projection_json", Text, nullable=False),
    Column("projection_sha256", Text, nullable=False),
    UniqueConstraint("document_id", "ordinal", name="uq_business_form_ordinal"),
    CheckConstraint("ordinal >= 0 AND revision >= 0", name="ck_business_record_revision"),
    CheckConstraint("review_status IN ('complete','unresolved')", name="ck_business_record_status"),
    CheckConstraint("projection_sha256 ~ '^[0-9a-f]{64}$'", name="ck_business_projection_hash"),
)

events = Table(
    "business_review_events", metadata,
    Column("document_id", Text, primary_key=True),
    Column("form_id", Text, primary_key=True),
    Column("decision_id", Text, primary_key=True),
    Column("field_name", Text, nullable=False),
    Column("previous_revision", BigInteger, nullable=False),
    Column("revision", BigInteger, nullable=False),
    Column("event_json", Text, nullable=False),
    Column("event_sha256", Text, nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(["document_id", "form_id"], ["business_records.document_id", "business_records.form_id"],
                         ondelete="RESTRICT", name="fk_business_event_record"),
    UniqueConstraint("document_id", "form_id", "revision", name="uq_business_event_revision"),
    CheckConstraint("previous_revision >= 0 AND revision = previous_revision + 1", name="ck_business_event_revision"),
    CheckConstraint("field_name IN ('first_name','last_name','israeli_id','central_numbers',"
                    "'sample_receipt_date','sender','commitment_numbers','__form__')", name="ck_business_event_field"),
    CheckConstraint("event_sha256 ~ '^[0-9a-f]{64}$'", name="ck_business_event_hash"),
)
