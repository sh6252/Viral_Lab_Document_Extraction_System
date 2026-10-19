"""Separate durable seven-field business records. Frozen PostgreSQL DDL."""
from alembic import op
import sqlalchemy as sa

revision = "0002_business_persistence"
down_revision = "0001_review_persistence"
branch_labels = None
depends_on = None

STATEMENTS = (
    """CREATE TABLE business_documents (
	document_id TEXT NOT NULL,
	review_token TEXT NOT NULL,
	source_bytes BYTEA NOT NULL,
	source_sha256 TEXT NOT NULL,
	mime_type TEXT NOT NULL,
	status TEXT NOT NULL,
	prompt_version TEXT NOT NULL,
	pipeline_version TEXT NOT NULL,
	extraction_id TEXT,
	canonical_snapshot_json TEXT,
	canonical_snapshot_sha256 TEXT,
	failure_json TEXT,
	failure_sha256 TEXT,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (document_id),
	CONSTRAINT ck_business_document_status CHECK (status IN ('processing','succeeded','failed')),
	CONSTRAINT ck_business_document_mime CHECK (mime_type IN ('application/pdf','image/png','image/jpeg')),
	CONSTRAINT ck_business_source_size CHECK (octet_length(source_bytes) > 0 AND octet_length(source_bytes) <= 20971520),
	CONSTRAINT ck_business_source_hash CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
	CONSTRAINT ck_business_review_token CHECK (length(review_token) >= 32),
	CONSTRAINT ck_business_document_outcome CHECK ((status = 'succeeded' AND extraction_id IS NOT NULL AND canonical_snapshot_json IS NOT NULL AND canonical_snapshot_sha256 IS NOT NULL AND canonical_snapshot_sha256 ~ '^[0-9a-f]{64}$' AND failure_json IS NULL AND failure_sha256 IS NULL) OR (status = 'failed' AND extraction_id IS NULL AND canonical_snapshot_json IS NULL AND canonical_snapshot_sha256 IS NULL AND failure_json IS NOT NULL AND failure_sha256 IS NOT NULL AND failure_sha256 ~ '^[0-9a-f]{64}$') OR (status = 'processing' AND extraction_id IS NULL AND canonical_snapshot_json IS NULL AND canonical_snapshot_sha256 IS NULL AND failure_json IS NULL AND failure_sha256 IS NULL))
)""",
    """CREATE TABLE business_records (
	document_id TEXT NOT NULL,
	form_id TEXT NOT NULL,
	ordinal INTEGER NOT NULL,
	revision BIGINT NOT NULL,
	review_status TEXT NOT NULL,
	projection_json TEXT NOT NULL,
	projection_sha256 TEXT NOT NULL,
	PRIMARY KEY (document_id, form_id),
	CONSTRAINT uq_business_form_ordinal UNIQUE (document_id, ordinal),
	CONSTRAINT ck_business_record_revision CHECK (ordinal >= 0 AND revision >= 0),
	CONSTRAINT ck_business_record_status CHECK (review_status IN ('complete','unresolved')),
	CONSTRAINT ck_business_projection_hash CHECK (projection_sha256 ~ '^[0-9a-f]{64}$'),
	FOREIGN KEY(document_id) REFERENCES business_documents (document_id) ON DELETE RESTRICT
)""",
    """CREATE TABLE business_review_events (
	document_id TEXT NOT NULL,
	form_id TEXT NOT NULL,
	decision_id TEXT NOT NULL,
	field_name TEXT NOT NULL,
	previous_revision BIGINT NOT NULL,
	revision BIGINT NOT NULL,
	event_json TEXT NOT NULL,
	event_sha256 TEXT NOT NULL,
	received_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (document_id, form_id, decision_id),
	CONSTRAINT fk_business_event_record FOREIGN KEY(document_id, form_id) REFERENCES business_records (document_id, form_id) ON DELETE RESTRICT,
	CONSTRAINT uq_business_event_revision UNIQUE (document_id, form_id, revision),
	CONSTRAINT ck_business_event_revision CHECK (previous_revision >= 0 AND revision = previous_revision + 1),
	CONSTRAINT ck_business_event_field CHECK (field_name IN ('first_name','last_name','israeli_id','central_numbers','sample_receipt_date','sender','commitment_numbers')),
	CONSTRAINT ck_business_event_hash CHECK (event_sha256 ~ '^[0-9a-f]{64}$')
)""",
)

GUARDS = (
    """CREATE FUNCTION business_documents_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'BUSINESS_DOCUMENT_IMMUTABLE'; END IF;
        IF OLD.status <> 'processing' OR NEW.status NOT IN ('succeeded','failed') OR
           ROW(NEW.document_id, NEW.review_token, NEW.source_bytes, NEW.source_sha256, NEW.mime_type,
               NEW.prompt_version, NEW.pipeline_version, NEW.created_at) IS DISTINCT FROM
           ROW(OLD.document_id, OLD.review_token, OLD.source_bytes, OLD.source_sha256, OLD.mime_type,
               OLD.prompt_version, OLD.pipeline_version, OLD.created_at)
        THEN RAISE EXCEPTION 'BUSINESS_DOCUMENT_IMMUTABLE'; END IF;
        RETURN NEW;
    END $$""",
    "CREATE TRIGGER business_documents_immutable BEFORE UPDATE OR DELETE ON business_documents FOR EACH ROW EXECUTE FUNCTION business_documents_guard()",
    """CREATE FUNCTION business_events_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'BUSINESS_AUDIT_APPEND_ONLY'; END $$""",
    "CREATE TRIGGER business_events_append_only BEFORE UPDATE OR DELETE ON business_review_events FOR EACH ROW EXECUTE FUNCTION business_events_guard()",
    """CREATE FUNCTION business_records_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'BUSINESS_RECORD_IMMUTABLE'; END IF;
        IF ROW(NEW.document_id, NEW.form_id, NEW.ordinal) IS DISTINCT FROM
           ROW(OLD.document_id, OLD.form_id, OLD.ordinal) OR NEW.revision <= OLD.revision
        THEN RAISE EXCEPTION 'BUSINESS_RECORD_REVISION_REQUIRED'; END IF;
        RETURN NEW;
    END $$""",
    "CREATE TRIGGER business_records_revision BEFORE UPDATE OR DELETE ON business_records FOR EACH ROW EXECUTE FUNCTION business_records_guard()",
)


def upgrade():
    for statement in STATEMENTS + GUARDS:
        op.execute(sa.text(statement))


def downgrade():
    # No destructive downgrade of original documents or human audit history.
    raise RuntimeError("BUSINESS_DATA_DOWNGRADE_REQUIRES_EXPLICIT_ARCHIVAL_PLAN")
