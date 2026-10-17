"""Initial immutable review schema. Static DDL: no dependency on future models."""
from alembic import op
import sqlalchemy as sa

revision = "0001_review_persistence"
down_revision = None
branch_labels = None
depends_on = None

STATEMENTS = ('CREATE TABLE review_sessions (\n'
 '\tsession_id TEXT NOT NULL, \n'
 '\tdocument_id TEXT NOT NULL, \n'
 '\tform_id TEXT NOT NULL, \n'
 '\textraction_id TEXT NOT NULL, \n'
 '\tinput_fingerprint TEXT NOT NULL, \n'
 '\tworkflow_version TEXT NOT NULL, \n'
 '\tassessment_version TEXT NOT NULL, \n'
 '\treview_version TEXT NOT NULL, \n'
 '\tstorage_codec_version TEXT NOT NULL, \n'
 '\tprepared_at TIMESTAMP WITH TIME ZONE NOT NULL, \n'
 '\tcanonical_snapshot_json TEXT NOT NULL, \n'
 '\tcanonical_snapshot_sha256 TEXT NOT NULL, \n'
 '\tcanonical_snapshot_encoding_version TEXT NOT NULL, \n'
 '\tpreparation_json TEXT NOT NULL, \n'
 '\tpreparation_sha256 TEXT NOT NULL, \n'
 '\tpreparation_encoding_version TEXT NOT NULL, \n'
 '\tocr_source_status TEXT NOT NULL, \n'
 '\trevision BIGINT NOT NULL, \n'
 '\toverall_review_status TEXT NOT NULL, \n'
 '\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n'
 '\tcurrent_projection_jsonb JSONB NOT NULL, \n'
 '\tcreated_received_at TIMESTAMP WITH TIME ZONE NOT NULL, \n'
 '\tcreation_principal_issuer TEXT NOT NULL, \n'
 '\tcreation_principal_subject TEXT NOT NULL, \n'
 '\tprovenance_ingestion_reference TEXT NOT NULL, \n'
 '\tPRIMARY KEY (session_id), \n'
 '\tCONSTRAINT uq_review_session_scope UNIQUE (session_id, document_id, form_id), \n'
 '\tCONSTRAINT ck_review_revision CHECK (revision >= 0), \n'
 '\tCONSTRAINT ck_review_state CHECK (overall_review_status IN '
 "('no_review_required','review_required','partially_reviewed','completed')), \n"
 '\tCONSTRAINT ck_ocr_source CHECK (ocr_source_status IN '
 "('succeeded','disabled','unavailable','failed')), \n"
 '\tCONSTRAINT ck_session_session_id CHECK (length(btrim(session_id)) > 0), \n'
 '\tCONSTRAINT ck_session_document_id CHECK (length(btrim(document_id)) > 0), \n'
 '\tCONSTRAINT ck_session_form_id CHECK (length(btrim(form_id)) > 0), \n'
 '\tCONSTRAINT ck_session_extraction_id CHECK (length(btrim(extraction_id)) > 0), \n'
 "\tCONSTRAINT ck_input_fingerprint_sha256 CHECK (input_fingerprint ~ '^[0-9a-f]{64}$'), \n"
 '\tCONSTRAINT ck_canonical_snapshot_sha256_sha256 CHECK (canonical_snapshot_sha256 ~ '
 "'^[0-9a-f]{64}$'), \n"
 "\tCONSTRAINT ck_preparation_sha256_sha256 CHECK (preparation_sha256 ~ '^[0-9a-f]{64}$')\n"
 ')',
 'CREATE INDEX ix_review_document_form_extraction ON review_sessions (document_id, form_id, '
 'extraction_id)',
 'CREATE TABLE review_items (\n'
 '\tsession_id TEXT NOT NULL, \n'
 '\tcandidate_reference TEXT NOT NULL, \n'
 '\tdocument_id TEXT NOT NULL, \n'
 '\tform_id TEXT NOT NULL, \n'
 '\tfield_name TEXT NOT NULL, \n'
 '\tactionable BOOLEAN NOT NULL, \n'
 '\tcandidate_json TEXT NOT NULL, \n'
 '\tprovider_values_jsonb JSONB NOT NULL, \n'
 '\tprivate_artifact_reference TEXT, \n'
 '\tPRIMARY KEY (session_id, candidate_reference), \n'
 '\tCONSTRAINT fk_item_session_scope FOREIGN KEY(session_id, document_id, form_id) REFERENCES '
 'review_sessions (session_id, document_id, form_id) ON DELETE RESTRICT, \n'
 '\tCONSTRAINT uq_review_item_actionable UNIQUE (session_id, candidate_reference, actionable), \n'
 "\tCONSTRAINT ck_item_field CHECK (field_name IN ('central_numbers','commitment_numbers')), \n"
 "\tCONSTRAINT ck_candidate_reference_sha256 CHECK (candidate_reference ~ '^[0-9a-f]{64}$')\n"
 ')',
 'CREATE TABLE review_events (\n'
 '\tsession_id TEXT NOT NULL, \n'
 '\tdecision_id TEXT NOT NULL, \n'
 '\tcandidate_reference TEXT NOT NULL, \n'
 '\tactionable BOOLEAN NOT NULL, \n'
 '\tevent_type TEXT NOT NULL, \n'
 '\tdecision_json TEXT NOT NULL, \n'
 '\tdecision_content_fingerprint TEXT NOT NULL, \n'
 '\tdecision_codec_version TEXT NOT NULL, \n'
 '\tdecision_status TEXT NOT NULL, \n'
 '\tfield_assignment_status TEXT NOT NULL, \n'
 '\tvalue_verification_status TEXT NOT NULL, \n'
 '\treviewed_value TEXT, \n'
 '\thuman_correction_occurred BOOLEAN NOT NULL, \n'
 '\treview_reason TEXT NOT NULL, \n'
 '\treviewer_note TEXT, \n'
 '\tdeclared_reviewer_reference TEXT NOT NULL, \n'
 '\tdeclared_reviewed_at TIMESTAMP WITH TIME ZONE NOT NULL, \n'
 '\tdeclared_reviewed_at_representation TEXT NOT NULL, \n'
 '\tprincipal_issuer TEXT NOT NULL, \n'
 '\tprincipal_subject TEXT NOT NULL, \n'
 '\treceived_at TIMESTAMP WITH TIME ZONE NOT NULL, \n'
 '\tprevious_revision BIGINT NOT NULL, \n'
 '\tnew_revision BIGINT NOT NULL, \n'
 '\tprevious_state TEXT NOT NULL, \n'
 '\tnew_state TEXT NOT NULL, \n'
 '\tPRIMARY KEY (session_id, decision_id), \n'
 '\tCONSTRAINT fk_event_session FOREIGN KEY(session_id) REFERENCES review_sessions (session_id) ON '
 'DELETE RESTRICT, \n'
 '\tCONSTRAINT fk_event_actionable_item FOREIGN KEY(session_id, candidate_reference, actionable) '
 'REFERENCES review_items (session_id, candidate_reference, actionable) ON DELETE RESTRICT, \n'
 '\tCONSTRAINT uq_review_event_revision UNIQUE (session_id, new_revision), \n'
 '\tCONSTRAINT uq_review_event_candidate UNIQUE (session_id, candidate_reference), \n'
 '\tCONSTRAINT ck_event_actionable CHECK (actionable = true), \n'
 "\tCONSTRAINT ck_event_type CHECK (event_type = 'decision_accepted'), \n"
 '\tCONSTRAINT ck_event_revision CHECK (previous_revision >= 0 AND new_revision = '
 'previous_revision + 1), \n'
 '\tCONSTRAINT ck_event_decision CHECK (decision_status IN '
 "('approved','rejected','unresolved')), \n"
 '\tCONSTRAINT ck_event_field_assignment_status CHECK (field_assignment_status IN '
 "('unverified','confirmed','rejected','unresolved')), \n"
 '\tCONSTRAINT ck_event_value_verification_status CHECK (value_verification_status IN '
 "('unverified','confirmed','rejected','unresolved')), \n"
 '\tCONSTRAINT ck_event_previous_state CHECK (previous_state IN '
 "('no_review_required','review_required','partially_reviewed','completed')), \n"
 '\tCONSTRAINT ck_event_new_state CHECK (new_state IN '
 "('no_review_required','review_required','partially_reviewed','completed')), \n"
 '\tCONSTRAINT ck_event_decision_id CHECK (length(btrim(decision_id)) > 0), \n'
 "\tCONSTRAINT ck_candidate_reference_sha256 CHECK (candidate_reference ~ '^[0-9a-f]{64}$'), \n"
 '\tCONSTRAINT ck_decision_content_fingerprint_sha256 CHECK (decision_content_fingerprint ~ '
 "'^[0-9a-f]{64}$')\n"
 ')',
 'CREATE FUNCTION review_reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE '
 "EXCEPTION 'REVIEW_IMMUTABLE_RECORD'; END $$",
 'CREATE TRIGGER review_items_immutable BEFORE UPDATE OR DELETE ON review_items FOR EACH ROW '
 'EXECUTE FUNCTION review_reject_mutation()',
 'CREATE TRIGGER review_events_append_only BEFORE UPDATE OR DELETE ON review_events FOR EACH ROW '
 'EXECUTE FUNCTION review_reject_mutation()',
 'CREATE TRIGGER review_sessions_no_delete BEFORE DELETE ON review_sessions FOR EACH ROW EXECUTE '
 'FUNCTION review_reject_mutation()',
 'CREATE FUNCTION review_guard_preparation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN\n'
 'IF (to_jsonb(NEW) - '
 "ARRAY['revision','overall_review_status','updated_at','current_projection_jsonb']) IS DISTINCT "
 'FROM (to_jsonb(OLD) - '
 "ARRAY['revision','overall_review_status','updated_at','current_projection_jsonb']) OR "
 'NEW.revision <> OLD.revision + 1 THEN\n'
 "RAISE EXCEPTION 'REVIEW_IMMUTABLE_PREPARATION'; END IF; RETURN NEW; END $$",
 'CREATE TRIGGER review_sessions_preparation_immutable BEFORE UPDATE ON review_sessions FOR EACH '
 'ROW EXECUTE FUNCTION review_guard_preparation()')


def upgrade():
    for statement in STATEMENTS:
        op.execute(sa.text(statement))


def downgrade():
    raise RuntimeError("AUDIT_DELETION_REQUIRES_SEPARATE_APPROVED_PROCEDURE")
