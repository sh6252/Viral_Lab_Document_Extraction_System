"""Synthetic lossless storage records; no database/provider/OCR required."""

import json
from dataclasses import replace
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from backend.persistence import review_codec as codec
from backend.persistence.db import create_review_engine
from backend.services.extraction.ocr_review import OCRReviewDecision
from backend.services.extraction.review_workflow import ReviewSession
from tests.test_ocr_candidates import candidate
from tests.test_ocr_review import decision
from tests.test_review_workflow import NOW, apply, prepare, required


@pytest.mark.parametrize("value", ["000123", "944963", "002"])
def test_preparation_roundtrip_preserves_text_and_hash(value):
    session = prepare(candidates=[candidate(value)])
    encoded = codec.encode(session)
    loaded = codec.decode(encoded, ReviewSession)
    assert loaded == session
    assert codec.encode(loaded) == encoded
    assert codec.validate_preparation(loaded) == session
    assert codec.digest(loaded.canonical_snapshot_json) == session.canonical_snapshot_sha256


@pytest.mark.parametrize("box", [(1, 2, 3, 4), (1.0, 2.5, 3.0, 4.75)])
def test_bbox_number_representation_and_occurrence_multiplicity_survive(box):
    token = candidate("002", bbox=box, bbox_coordinate_system="synthetic-pixels")
    session = prepare(candidates=[token, token])
    loaded = codec.decode(codec.encode(session), ReviewSession)
    assert len(loaded.supporting_assessment.fields[0].ocr_candidates) == 2
    assert loaded.review_items[0].candidate_id == session.review_items[0].candidate_id
    assert codec.encode(loaded) == codec.encode(session)


@pytest.mark.parametrize("text", [" 000-12/3) ", "בדיקה-0001", "001\n002"])
def test_reviewed_value_and_declared_time_are_lossless(text):
    session = required()
    original = decision(session.review_items[0], reviewed_value=text,
                        reviewed_at=NOW.astimezone(timezone(timedelta(hours=3))))
    loaded = codec.decode(codec.encode(original), OCRReviewDecision)
    assert loaded == original
    assert loaded.reviewed_value == text
    assert loaded.reviewed_at.isoformat() == original.reviewed_at.isoformat()
    assert codec.decision_fingerprint(loaded) == codec.decision_fingerprint(original)


def test_decision_identity_is_compatible_with_equal_fixed_offset_instants():
    session = required()
    action = decision(session.review_items[0])
    offset = replace(action, reviewed_at=NOW.astimezone(timezone(timedelta(hours=2))))
    assert action == offset
    assert codec.decision_fingerprint(action) == codec.decision_fingerprint(offset)
    assert codec.encode(action) != codec.encode(offset)


def test_zoneinfo_timestamp_roundtrip():
    session = required(prepared_at=NOW.astimezone(ZoneInfo("Asia/Jerusalem")))
    assert codec.encode(codec.decode(codec.encode(session), ReviewSession)) == codec.encode(session)


@pytest.mark.parametrize("mutation", [
    lambda value: value.update(version="unknown"),
    lambda value: value.update(unexpected="extra"),
    lambda value: value["payload"]["fields"].update(input_fingerprint="a" * 64),
])
def test_unknown_version_and_corrupt_preparation_fail_closed(mutation):
    payload = json.loads(codec.encode(required()))
    mutation(payload)
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with pytest.raises(codec.ReviewIntegrityError):
        codec.validate_preparation(codec.decode(text, ReviewSession))


@pytest.mark.parametrize("text", ["not json", "null", '{"version":1,"version":2}', '{}'])
def test_malformed_payload_has_safe_error(text):
    with pytest.raises(codec.ReviewIntegrityError, match="^REVIEW_STORAGE_INTEGRITY_ERROR$"):
        codec.decode(text, ReviewSession)


def test_changed_status_and_hash_are_not_repaired():
    with pytest.raises(codec.ReviewIntegrityError):
        codec.validate_preparation(replace(required(), canonical_snapshot_sha256="b" * 64))
    with pytest.raises(codec.ReviewIntegrityError):
        codec.validate_preparation(apply(required()).session)


@pytest.mark.parametrize("dsn", ["sqlite://", "not-a-dsn", "mysql://example/db", None])
def test_database_boundary_refuses_nonpostgres_without_exposing_input(dsn):
    with pytest.raises(ValueError, match="^POSTGRESQL_DSN_REQUIRED$"):
        create_review_engine(dsn)
