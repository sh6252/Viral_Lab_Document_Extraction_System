"""Versioned lossless internal records. No pickle, dynamic imports or repairs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from backend.domain import ExtractionResult
from backend.services.extraction import ocr_candidates as candidates
from backend.services.extraction import ocr_review as review
from backend.services.extraction import review_workflow as workflow

CODEC_VERSION = "review-storage-v1"


class ReviewIntegrityError(ValueError):
    def __init__(self) -> None:
        super().__init__("REVIEW_STORAGE_INTEGRITY_ERROR")


_TYPES = {cls.__name__: cls for cls in (
    candidates.FieldAssignmentEvidence, candidates.OCRCandidate, candidates.ProviderValue,
    candidates.FieldAssessment, candidates.SupportingAssessment,
    review.OCRReviewDecision, review.OCRReviewItem,
    workflow.FormPageImage, workflow.FieldReviewIssue, workflow.AcceptedReviewDecision,
    workflow.ReviewSession,
)}


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _wire(value: object, *, identity: bool = False) -> object:
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            raise ReviewIntegrityError()
        if identity:
            value = value.astimezone(timezone.utc)
        return {"type": "datetime", "iso": value.isoformat(), "fold": value.fold,
                "zone": getattr(value.tzinfo, "key", None)}
    if is_dataclass(value) and type(value) is _TYPES.get(type(value).__name__):
        return {"type": type(value).__name__, "fields": {
            field.name: _wire(getattr(value, field.name), identity=identity) for field in fields(value)
        }}
    if isinstance(value, tuple):
        return {"type": "tuple", "items": [_wire(v, identity=identity) for v in value]}
    if value is None or type(value) in {str, int, float, bool}:
        return value
    raise ReviewIntegrityError()


def _unwire(value: object) -> object:
    if not isinstance(value, dict):
        if value is None or type(value) in {str, int, float, bool}:
            return value
        raise ReviewIntegrityError()
    kind = value.get("type")
    if kind == "tuple" and set(value) == {"type", "items"}:
        return tuple(_unwire(v) for v in value["items"])
    if kind == "datetime" and set(value) == {"type", "iso", "fold", "zone"}:
        parsed = datetime.fromisoformat(value["iso"])
        if value["zone"] is not None:
            parsed = parsed.astimezone(ZoneInfo(value["zone"]))
        return parsed.replace(fold=value["fold"])
    if kind in _TYPES and set(value) == {"type", "fields"}:
        cls = _TYPES[kind]
        data = value["fields"]
        if set(data) != {field.name for field in fields(cls)}:
            raise ReviewIntegrityError()
        return cls(**{field.name: _unwire(data[field.name]) for field in fields(cls) if field.init})
    raise ReviewIntegrityError()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def encode(value: object) -> str:
    try:
        return _json({"version": CODEC_VERSION, "payload": _wire(value)})
    except (ValueError, TypeError, AttributeError):
        raise ReviewIntegrityError() from None


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    data = dict(pairs)
    if len(data) != len(pairs):
        raise ReviewIntegrityError()
    return data


def decode(text: str, expected_type: type):
    try:
        data = json.loads(text, object_pairs_hook=_pairs)
        if set(data) != {"version", "payload"} or data["version"] != CODEC_VERSION:
            raise ReviewIntegrityError()
        value = _unwire(data["payload"])
        if type(value) is not expected_type or encode(value) != text:
            raise ReviewIntegrityError()
        if expected_type is workflow.ReviewSession:
            validate_preparation(value)
        if isinstance(value, candidates.SupportingAssessment) and value.version != candidates.ASSESSMENT_VERSION:
            raise ReviewIntegrityError()
        return value
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        raise ReviewIntegrityError() from None


def decision_fingerprint(decision: review.OCRReviewDecision) -> str:
    return digest(_json({"version": CODEC_VERSION, "payload": _wire(decision, identity=True)}))


def validate_preparation(session: workflow.ReviewSession) -> workflow.ReviewSession:
    try:
        if not isinstance(session, workflow.ReviewSession) or session.revision != 0 or session.decision_history:
            raise ReviewIntegrityError()
        pages: dict[str, dict[int, str]] = {}
        for image in session.form_page_images:
            pages.setdefault(image.form_id, {})[image.page_number] = image.source_image_sha256
        rebuilt = workflow.ReviewWorkflowService().prepare_review(
            ExtractionResult.model_validate_json(session.canonical_snapshot_json),
            tuple(c for field in session.supporting_assessment.fields for c in field.ocr_candidates),
            document_id=session.document_id, form_id=session.form_id, form_page_images=pages,
            source_status=session.supporting_assessment.source_status,
            session_id=session.session_id, prepared_at=session.prepared_at,
        )
        if encode(rebuilt) != encode(session):
            raise ReviewIntegrityError()
        return rebuilt
    except (ValueError, TypeError, AttributeError):
        raise ReviewIntegrityError() from None


def projection(session: workflow.ReviewSession) -> dict:
    return {"revision": session.revision, "state": session.overall_review_status,
            "items": {item.candidate_id: {"state": item.review_state,
                      "decision_id": item.review_decision.decision_id if item.review_decision else None}
                      for item in session.review_items}}
