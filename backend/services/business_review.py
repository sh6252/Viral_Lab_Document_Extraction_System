"""Pure seven-field human review. No OCR, providers, persistence or canonical writes."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal, get_args

from backend.domain import ExtractionResult
from backend.domain.models import RequiredFieldName
from backend.services.extraction.schema_validation import validate_extraction_result_schema

FIELDS: tuple[str, ...] = get_args(RequiredFieldName)
MULTI_FIELDS = frozenset({"central_numbers", "commitment_numbers"})
REVIEW_VERSION = "business-review-v1"
SNAPSHOT_ENCODING = "canonical-json-v1"
ActionKind = Literal["approve_as_extracted", "correct_and_approve", "acknowledge_blank",
                     "reject", "leave_unresolved"]
FieldReviewState = Literal["approved_provider", "approved_corrected", "acknowledged_blank",
                           "unresolved", "rejected_unresolved", "unreviewed"]


class BusinessReviewError(ValueError):
    """Messages are safe reason codes, never submitted values."""


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise BusinessReviewError(code)


def _text(value: object) -> None:
    _require(isinstance(value, str) and bool(value.strip()), "NONBLANK_TEXT_REQUIRED")


def _hash(value: object) -> None:
    _require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
             "INVALID_SHA256")


def _revision(value: object) -> None:
    _require(type(value) is int and value >= 0, "INVALID_REVIEW_REVISION")


def deterministic_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def snapshot_canonical(value: ExtractionResult | str) -> tuple[str, ExtractionResult]:
    """Detached all-fields/nulls snapshot; hash its UTF-8 text, not input file bytes."""
    try:
        result = (ExtractionResult.model_validate_json(value) if isinstance(value, str)
                  else ExtractionResult.model_validate(value.model_dump(mode="json", warnings="error")))
        validate_extraction_result_schema(result)
        _require(bool(result.logical_forms), "SUCCESSFUL_EXTRACTION_REQUIRED")
        ids = [form.form_id for form in result.logical_forms]
        _require(len(ids) == len(set(ids)), "DUPLICATE_FORM_ID")
        for form in result.logical_forms:
            for name in FIELDS:
                field = getattr(form.fields, name)
                if field is not None:
                    _require((field.value_status == "present") == bool(field.values),
                             "INCONSISTENT_CANONICAL_FIELD")
        text = deterministic_json(result.model_dump(mode="json", warnings="error"))
        text.encode("utf-8")
        return text, result
    except BusinessReviewError:
        raise
    except Exception:
        raise BusinessReviewError("INVALID_CANONICAL_SNAPSHOT") from None


def snapshot_sha256(value: ExtractionResult | str) -> str:
    return hashlib.sha256(snapshot_canonical(value)[0].encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SourceBinding:
    document_id: str
    source_sha256: str
    extraction_id: str
    canonical_snapshot_sha256: str
    data_origin: Literal["live", "historical_replay"]
    snapshot_kind: Literal["live", "preserved"]
    prompt_version: str | None = None
    pipeline_version: str | None = None
    relationship_status: str = "unresolved"

    def __post_init__(self) -> None:
        _text(self.document_id)
        _text(self.extraction_id)
        _hash(self.source_sha256)
        _hash(self.canonical_snapshot_sha256)
        _require((self.snapshot_kind, self.data_origin) in
                 {("live", "live"), ("preserved", "historical_replay")}, "EXPLICIT_DATA_ORIGIN_REQUIRED")
        for value in (self.prompt_version, self.pipeline_version):
            if value is not None:
                _text(value)
        # No automatic assertion that identifiers have known pairings.
        _require(self.relationship_status == "unresolved", "RELATIONSHIP_REVIEW_OUT_OF_SCOPE")


@dataclass(frozen=True, slots=True)
class OriginalValue:
    raw: str
    normalized: str | None = None

    def __post_init__(self) -> None:
        _require(isinstance(self.raw, str) and (self.normalized is None or isinstance(self.normalized, str)),
                 "EXACT_STRING_VALUES_REQUIRED")


@dataclass(frozen=True, slots=True)
class OriginalField:
    value_status: str
    review_status: str | None
    values: tuple[OriginalValue, ...]

    def __post_init__(self) -> None:
        _require(isinstance(self.values, (tuple, list)), "VALUE_LIST_REQUIRED")
        object.__setattr__(self, "values", tuple(self.values))
        _require(all(isinstance(value, OriginalValue) for value in self.values), "INVALID_ORIGINAL_VALUES")
        _require(self.value_status in {"omitted", "present", "missing", "unreadable", "ambiguous", "not_processed"},
                 "INVALID_ORIGINAL_STATUS")
        _require(self.review_status is None or isinstance(self.review_status, str), "INVALID_ORIGINAL_STATUS")


def original_field(snapshot: ExtractionResult | str, form_id: str, field_name: str) -> OriginalField:
    _, result = snapshot_canonical(snapshot)
    _require(field_name in FIELDS, "UNKNOWN_BUSINESS_FIELD")
    form = next((form for form in result.logical_forms if form.form_id == form_id), None)
    _require(form is not None, "UNKNOWN_LOGICAL_FORM")
    value = getattr(form.fields, field_name)
    return (OriginalField("omitted", None, ()) if value is None else
            OriginalField(value.value_status, value.review_status,
                          tuple(OriginalValue(v.raw, v.normalized) for v in value.values)))


@dataclass(frozen=True, slots=True)
class BusinessReviewAction:
    source_sha256: str
    extraction_id: str
    canonical_snapshot_sha256: str
    form_id: str
    field_name: str
    decision_id: str
    expected_revision: int
    operator_declaration: str
    declared_at: datetime
    reason: str
    original: OriginalField
    action: ActionKind
    reviewed_values: tuple[str, ...]

    def __post_init__(self) -> None:
        _hash(self.source_sha256)
        _hash(self.canonical_snapshot_sha256)
        for value in (self.extraction_id, self.form_id, self.decision_id,
                      self.operator_declaration, self.reason):
            _text(value)
        _require(self.field_name in FIELDS, "UNKNOWN_BUSINESS_FIELD")
        _revision(self.expected_revision)
        _require(isinstance(self.declared_at, datetime) and self.declared_at.utcoffset() is not None,
                 "AWARE_DECLARED_TIMESTAMP_REQUIRED")
        _require(isinstance(self.original, OriginalField), "ORIGINAL_FIELD_REQUIRED")
        _require(self.action in get_args(ActionKind), "UNKNOWN_REVIEW_ACTION")
        _require(isinstance(self.reviewed_values, (tuple, list)), "VALUE_LIST_REQUIRED")
        object.__setattr__(self, "reviewed_values", tuple(self.reviewed_values))
        _require(all(isinstance(v, str) for v in self.reviewed_values), "EXACT_STRING_VALUES_REQUIRED")


@dataclass(frozen=True, slots=True)
class ReviewedField:
    field_name: str
    original: OriginalField
    reviewed_values: tuple[str, ...] = ()
    review_status: FieldReviewState = "unreviewed"

    def __post_init__(self) -> None:
        _require(self.field_name in FIELDS and isinstance(self.original, OriginalField), "INVALID_REVIEWED_FIELD")
        _require(isinstance(self.reviewed_values, (tuple, list)), "VALUE_LIST_REQUIRED")
        object.__setattr__(self, "reviewed_values", tuple(self.reviewed_values))
        _require(all(isinstance(v, str) for v in self.reviewed_values)
                 and self.review_status in get_args(FieldReviewState), "INVALID_REVIEWED_FIELD")

    @property
    def value_source(self) -> str:
        return {"approved_provider": "human_approved_provider", "approved_corrected": "human_corrected",
                "acknowledged_blank": "human_approved_blank"}.get(self.review_status, "none")

    @property
    def value_status(self) -> str:
        if self.review_status in {"approved_provider", "approved_corrected"}:
            return "present"
        if self.review_status == "acknowledged_blank":
            return "blank"
        return "unresolved" if self.original.value_status == "present" else self.original.value_status


@dataclass(frozen=True, slots=True)
class ReviewedBusinessRecord:
    form_id: str
    binding: SourceBinding
    provider: str
    model: str | None
    fields: tuple[ReviewedField, ...]
    revision: int = 0

    def __post_init__(self) -> None:
        _text(self.form_id)
        _require(isinstance(self.binding, SourceBinding), "SOURCE_BINDING_REQUIRED")
        _require(isinstance(self.fields, (tuple, list)), "REVIEWED_FIELDS_REQUIRED")
        object.__setattr__(self, "fields", tuple(self.fields))
        _require(all(isinstance(f, ReviewedField) for f in self.fields)
                 and tuple(f.field_name for f in self.fields) == FIELDS, "ALL_SEVEN_FIELDS_REQUIRED")
        _text(self.provider)
        _require(self.model is None or isinstance(self.model, str), "INVALID_PROVIDER_METADATA")
        _revision(self.revision)

    @property
    def unresolved_fields(self) -> tuple[str, ...]:
        return tuple(f.field_name for f in self.fields if f.review_status not in
                     {"approved_provider", "approved_corrected", "acknowledged_blank"})

    @property
    def review_status(self) -> Literal["complete", "unresolved"]:
        return "unresolved" if self.unresolved_fields else "complete"


@dataclass(frozen=True, slots=True)
class BusinessReviewAuditEntry:
    action: BusinessReviewAction
    old_review_state: FieldReviewState
    new_review_state: FieldReviewState
    previous_revision: int
    revision: int

    def __post_init__(self) -> None:
        _require(isinstance(self.action, BusinessReviewAction)
                 and self.old_review_state in get_args(FieldReviewState)
                 and self.new_review_state in get_args(FieldReviewState), "INVALID_REVIEW_AUDIT_ENTRY")
        _revision(self.previous_revision)
        _revision(self.revision)
        _require(self.revision == self.previous_revision + 1, "INVALID_REVIEW_AUDIT_REVISION")


@dataclass(frozen=True, slots=True)
class BusinessReviewResult:
    canonical_snapshot_json: str
    record: ReviewedBusinessRecord
    audit: tuple[BusinessReviewAuditEntry, ...] = ()
    version: str = REVIEW_VERSION
    snapshot_encoding: str = SNAPSHOT_ENCODING

    def __post_init__(self) -> None:
        _require(isinstance(self.canonical_snapshot_json, str) and isinstance(self.record, ReviewedBusinessRecord)
                 and isinstance(self.audit, (tuple, list)), "INVALID_BUSINESS_REVIEW_RESULT")
        object.__setattr__(self, "audit", tuple(self.audit))
        _require(all(isinstance(a, BusinessReviewAuditEntry) for a in self.audit), "INVALID_BUSINESS_REVIEW_RESULT")


def _initial(snapshot: ExtractionResult | str, binding: SourceBinding, form_id: str) -> BusinessReviewResult:
    _require(isinstance(binding, SourceBinding), "SOURCE_BINDING_REQUIRED")
    text, result = snapshot_canonical(snapshot)
    _require(hashlib.sha256(text.encode("utf-8")).hexdigest() == binding.canonical_snapshot_sha256
             and result.extraction_id == binding.extraction_id, "SOURCE_SNAPSHOT_MISMATCH")
    fields = tuple(ReviewedField(name, original_field(text, form_id, name)) for name in FIELDS)
    return BusinessReviewResult(text, ReviewedBusinessRecord(form_id, binding,
                               result.provider.name, result.provider.version, fields))


def _transition(current: BusinessReviewResult, action: BusinessReviewAction) -> BusinessReviewResult:
    _require(isinstance(action, BusinessReviewAction), "INVALID_REVIEW_ACTION")
    record, binding = current.record, current.record.binding
    _require((action.source_sha256, action.extraction_id, action.canonical_snapshot_sha256, action.form_id)
             == (binding.source_sha256, binding.extraction_id, binding.canonical_snapshot_sha256, record.form_id),
             "ACTION_SCOPE_MISMATCH")
    _require(action.expected_revision == record.revision, "STALE_REVIEW_REVISION")
    original = next(f for f in record.fields if f.field_name == action.field_name)
    _require(original.original == action.original, "ORIGINAL_FIELD_MISMATCH")
    values = action.reviewed_values
    if action.action == "approve_as_extracted":
        _require(action.original.value_status == "present" and bool(values)
                 and values == tuple(v.raw for v in action.original.values), "WHOLE_PROVIDER_LIST_REQUIRED")
        state = "approved_provider"
    elif action.action == "correct_and_approve":
        _require(bool(values), "USE_ACKNOWLEDGE_BLANK")
        state = "approved_corrected"
    elif action.action == "acknowledge_blank":
        _require(not values, "BLANK_REQUIRES_EMPTY_LIST")
        state = "acknowledged_blank"
    elif action.action == "reject":
        _require(not values, "REJECTION_REQUIRES_EMPTY_LIST")
        state = "rejected_unresolved"
    else:
        state = "unresolved"
    if state in {"approved_provider", "approved_corrected"}:
        _require(all(v.strip() for v in values), "APPROVED_VALUES_MUST_BE_NONBLANK")
        _require(action.field_name in MULTI_FIELDS or len(values) == 1, "SCALAR_REQUIRES_ONE_VALUE")
    # Tentative unresolved text lives in the audit only. No candidate is auto-promoted.
    field = replace(original, reviewed_values=values if state.startswith("approved_") else (), review_status=state)
    entry = BusinessReviewAuditEntry(action, original.review_status, state, record.revision, record.revision + 1)
    return replace(current, record=replace(record, fields=tuple(field if f.field_name == action.field_name else f
                                                              for f in record.fields), revision=record.revision + 1),
                   audit=current.audit + (entry,))


def validate_review_result(result: BusinessReviewResult) -> None:
    """Rebuild to reject altered records/history; consistency is not authentication."""
    try:
        _require(isinstance(result, BusinessReviewResult), "INVALID_BUSINESS_REVIEW_RESULT")
        rebuilt = _initial(result.canonical_snapshot_json, result.record.binding, result.record.form_id)
        ids: set[str] = set()
        for entry in result.audit:
            _require(entry.action.decision_id not in ids, "DUPLICATE_DECISION_ID")
            ids.add(entry.action.decision_id)
            rebuilt = _transition(rebuilt, entry.action)
        _require(rebuilt == result, "INVALID_BUSINESS_REVIEW_RESULT")
    except Exception:
        raise BusinessReviewError("INVALID_BUSINESS_REVIEW_RESULT") from None


def apply_business_review(
    canonical_snapshot: ExtractionResult | str, source_binding: SourceBinding, form_id: str,
    actions: Iterable[BusinessReviewAction] = (), expected_revision: int = 0,
    *, previous: BusinessReviewResult | None = None,
) -> BusinessReviewResult:
    """Atomic local batch; revisions/decision IDs are scoped to exactly one form.

    Duplicate IDs within a command fail. Previously accepted identical actions
    replay before the revision precondition. Later explicit actions can resolve
    rejected/unresolved fields without removing their historical entries.
    """
    _revision(expected_revision)
    initial = _initial(canonical_snapshot, source_binding, form_id)
    if previous is not None:
        validate_review_result(previous)
        _require((previous.canonical_snapshot_json, previous.record.binding, previous.record.form_id)
                 == (initial.canonical_snapshot_json, source_binding, form_id), "PREVIOUS_REVIEW_SCOPE_MISMATCH")
    current = previous if previous is not None else initial
    batch = tuple(actions)
    _require(all(isinstance(a, BusinessReviewAction) for a in batch), "INVALID_REVIEW_ACTION")
    ids = [a.decision_id for a in batch]
    _require(len(ids) == len(set(ids)), "DUPLICATE_DECISION_ID")
    accepted = {entry.action.decision_id: entry.action for entry in current.audit}
    new = []
    for action in batch:
        if action.decision_id in accepted:
            _require(accepted[action.decision_id] == action, "CONFLICTING_DECISION_REPLAY")
        else:
            new.append(action)
    if batch and not new:
        return current
    _require(expected_revision == current.record.revision, "STALE_REVIEW_REVISION")
    for action in new:
        current = _transition(current, action)
    return current
