"""Local/private operator command: existing snapshot + explicit actions -> XLSX.

No server, provider, OCR, DB or implicit artifact directory. Inputs/outputs contain
sensitive data. The operator chooses a private location and declares provenance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr, field_validator

from backend.services.business_review import (
    ActionKind, BusinessReviewAction, BusinessReviewError, OriginalField, OriginalValue,
    SourceBinding, apply_business_review, snapshot_canonical,
)
from backend.services.excel_export import ExcelExportError, ProcessingFailure, export_workbook


class LocalInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class BindingInput(LocalInput):
    document_id: StrictStr
    source_sha256: StrictStr
    extraction_id: StrictStr
    canonical_snapshot_sha256: StrictStr
    data_origin: Literal["live", "historical_replay"]
    snapshot_kind: Literal["live", "preserved"]
    prompt_version: StrictStr | None = None
    pipeline_version: StrictStr | None = None
    relationship_status: Literal["unresolved"] = "unresolved"


class SourceInput(LocalInput):
    source_path: StrictStr
    binding: BindingInput


class ValueInput(LocalInput):
    raw: StrictStr
    normalized: StrictStr | None = None


class OriginalInput(LocalInput):
    value_status: StrictStr
    review_status: StrictStr | None
    values: list[ValueInput]


class ActionInput(LocalInput):
    source_sha256: StrictStr
    extraction_id: StrictStr
    canonical_snapshot_sha256: StrictStr
    form_id: StrictStr
    field_name: StrictStr
    decision_id: StrictStr
    expected_revision: StrictInt
    operator_declaration: StrictStr
    declared_at: datetime
    reason: StrictStr
    original: OriginalInput
    action: ActionKind
    reviewed_values: list[StrictStr]

    @field_validator("declared_at", mode="before")
    @classmethod
    def declared_time(cls, value: object) -> datetime:
        if not isinstance(value, str):
            raise ValueError("ISO_TIME_REQUIRED")
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            raise ValueError("AWARE_DECLARED_TIMESTAMP_REQUIRED")
        return parsed

    def to_action(self) -> BusinessReviewAction:
        values = self.model_dump()
        values["original"] = OriginalField(self.original.value_status, self.original.review_status,
                                           tuple(OriginalValue(v.raw, v.normalized) for v in self.original.values))
        return BusinessReviewAction(**values)


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise BusinessReviewError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise BusinessReviewError("INVALID_JSON_CONSTANT")


def _load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_object,
                      parse_constant=_invalid_constant)


def _json_time(value: object) -> str:
    if not isinstance(value, datetime):
        raise TypeError("UNSUPPORTED_BUNDLE_TYPE")
    return value.isoformat()


def run_operator(*, snapshot_path: Path, source_path: Path, actions_path: Path,
                 output_path: Path, failures_path: Path | None = None,
                 bundle_path: Path | None = None) -> dict[str, object]:
    """Validate all inputs, build all forms, then exclusively create two outputs."""
    snapshot_path, source_path, actions_path, output_path = map(Path,
        (snapshot_path, source_path, actions_path, output_path))
    bundle_path = Path(bundle_path) if bundle_path is not None else output_path.with_suffix(".review.json")
    if output_path.suffix.lower() != ".xlsx" or bundle_path.suffix.lower() != ".json":
        raise BusinessReviewError("INVALID_OUTPUT_EXTENSION")
    inputs = {p.resolve() for p in (snapshot_path, source_path, actions_path)}
    if failures_path is not None:
        failures_path = Path(failures_path)
        inputs.add(failures_path.resolve())
    if (output_path.resolve() == bundle_path.resolve()
            or any(p.resolve() in inputs or p.exists() for p in (output_path, bundle_path))):
        raise BusinessReviewError("OUTPUT_MUST_BE_NEW")
    source = SourceInput.model_validate(_load(source_path))
    binding = SourceBinding(**source.binding.model_dump())
    document_path = Path(source.source_path)
    if not document_path.is_absolute():
        document_path = source_path.parent / document_path
    if document_path.resolve() in {output_path.resolve(), bundle_path.resolve()}:
        raise BusinessReviewError("OUTPUT_MUST_BE_NEW")
    digest = hashlib.sha256()
    with document_path.open("rb") as document:
        for chunk in iter(lambda: document.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != binding.source_sha256:
        raise BusinessReviewError("SOURCE_BYTES_MISMATCH")
    # Strict JSON load first also rejects duplicate keys in the canonical input.
    canonical_json = json.dumps(_load(snapshot_path), ensure_ascii=False, allow_nan=False)
    snapshot, canonical = snapshot_canonical(canonical_json)
    raw_actions = _load(actions_path)
    if not isinstance(raw_actions, list):
        raise BusinessReviewError("ACTION_ARRAY_REQUIRED")
    actions = tuple(ActionInput.model_validate(a).to_action() for a in raw_actions)
    form_ids = {form.form_id for form in canonical.logical_forms}
    if any(a.form_id not in form_ids for a in actions):
        raise BusinessReviewError("UNKNOWN_LOGICAL_FORM")
    results = tuple(apply_business_review(snapshot, binding, form.form_id,
                    (a for a in actions if a.form_id == form.form_id)) for form in canonical.logical_forms)
    raw_failures = _load(failures_path) if failures_path is not None else []
    if not isinstance(raw_failures, list):
        raise BusinessReviewError("FAILURE_ARRAY_REQUIRED")
    failures = tuple(ProcessingFailure(**item) for item in raw_failures)
    workbook = export_workbook(results, failures)
    workbook_hash = hashlib.sha256(workbook).hexdigest()
    bundle = json.dumps({"bundle_version": "review-export-v1", "workbook_sha256": workbook_hash,
        "canonical_snapshot_json": snapshot, "source_binding": asdict(binding),
        "results": [asdict(result) for result in results], "failures": [asdict(f) for f in failures]},
        ensure_ascii=False, indent=2, allow_nan=False,
        default=_json_time).encode("utf-8")
    created = []
    try:
        for path, content in ((output_path, workbook), (bundle_path, bundle)):
            # Never overwrite inputs, evidence or existing outputs, including a race.
            with path.open("xb") as output:
                created.append(path)
                output.write(content)
    except OSError:
        for path in created:
            path.unlink(missing_ok=True)
        raise BusinessReviewError("PRIVATE_OUTPUT_WRITE_FAILED") from None
    return {"status": "exported", "forms": len(results),
            "complete_records": sum(r.record.review_status == "complete" for r in results),
            "unresolved_records": sum(r.record.review_status == "unresolved" for r in results),
            "review_actions": sum(len(r.audit) for r in results), "processing_failures": len(failures),
            "workbook_sha256": workbook_hash}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local/private canonical review to XLSX; no extraction.")
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--actions", type=Path, required=True)
    parser.add_argument("--failures", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bundle", type=Path)
    args = parser.parse_args(argv)
    try:
        summary = run_operator(snapshot_path=args.snapshot, source_path=args.source, actions_path=args.actions,
                               failures_path=args.failures, output_path=args.output, bundle_path=args.bundle)
    except (BusinessReviewError, ExcelExportError, OSError, ValueError, TypeError):
        # No Pydantic input echo, filesystem path, source token or exception chain.
        print(json.dumps({"error": {"code": "REVIEW_EXPORT_FAILED"}}))
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
