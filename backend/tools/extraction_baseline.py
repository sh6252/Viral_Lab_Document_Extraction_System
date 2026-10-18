"""Measured live runs through the existing adapters; canonical output stays untouched.

Private fixtures, truth and results belong under git-ignored evaluation/artifacts.
This tool summarizes existing scorer metrics; it does not change scoring rules.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from backend.core.config import AppConfig
from backend.domain import ExtractionRequest, ExtractionResult, ProviderPolicySnapshot
from backend.services.extraction.base import DocumentInput, ExtractionAdapter
from backend.services.extraction.errors import ExtractionAdapterError
from backend.services.extraction.orchestrator import ExtractionOrchestrator
from backend.services.extraction.policy import ProviderPolicy
from backend.services.extraction.prompts import EXTRACTION_PROMPT_VERSION
from backend.services.extraction.registry import build_extraction_adapter, registered_provider_names
from backend.services.extraction.schema_validation import validate_extraction_result_schema
from evaluation.scoring import REQUIRED_FIELDS, score_extraction

ROOT = Path(__file__).resolve().parents[2]
PRIVATE_ROOT = ROOT / "evaluation" / "artifacts"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def checked_artifact_path(relative: str) -> Path:
    path = (ROOT / relative).resolve()
    if not path.is_relative_to(PRIVATE_ROOT.resolve()):
        raise ValueError("Baseline data must remain inside evaluation/artifacts.")
    return path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_entries(manifest: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any], bytes]]:
    """Validate and freeze inputs before constructing any provider client."""
    schema = load_json(ROOT / "evaluation" / "schemas" / "ground_truth.schema.json")
    validator = Draft202012Validator(schema)
    entries = manifest["entries"]
    if not entries:
        raise ValueError("Baseline requires selected fixtures.")
    seen: set[str] = set()
    prepared = []
    for entry in entries:
        fixture_id = entry["fixture_id"]
        if fixture_id in seen:
            raise ValueError("Duplicate baseline fixture identifier.")
        seen.add(fixture_id)
        document_path = checked_artifact_path(entry["document_path"])
        truth_path = checked_artifact_path(entry["ground_truth_path"])
        if sha256(document_path) != entry["document_sha256"]:
            raise ValueError("Selected PDF checksum changed after selection.")
        if sha256(truth_path) != entry["ground_truth_sha256"]:
            raise ValueError("Ground truth checksum changed after annotation freeze.")
        truth = load_json(truth_path)
        validator.validate(truth)
        if truth["fixture_id"] != fixture_id or truth["fixture_set"] != manifest["fixture_set"]:
            raise ValueError("Fixture identity or dataset split does not match the manifest.")
        if truth["source_document"]["sha256"] != entry["document_sha256"]:
            raise ValueError("Ground truth belongs to a different document.")
        if len(truth["logical_forms"]) != 1:
            raise ValueError("This baseline measures one manually selected form per PDF.")
        prepared.append((entry, truth, document_path.read_bytes()))
    return prepared


def measured_extraction(
    adapter: ExtractionAdapter, request: ExtractionRequest, document: DocumentInput
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Observe adapter attempts without changing requests, responses or retry logic."""
    attempts: list[dict[str, Any]] = []
    original_call: Callable[..., Any] | None = getattr(adapter, "_call_provider", None)

    def observed_call(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        attempt: dict[str, Any] = {"number": len(attempts) + 1}
        attempts.append(attempt)
        try:
            assert original_call is not None
            response = original_call(*args, **kwargs)
            attempt["status"] = "returned"
            return response
        except Exception as exc:
            attempt.update(status="error", exception_class=type(exc).__name__)
            raise
        finally:
            attempt["latency_seconds"] = round(time.perf_counter() - started, 6)

    if original_call is not None:
        setattr(adapter, "_call_provider", observed_call)
    started = time.perf_counter()
    try:
        result = ExtractionOrchestrator(adapter=adapter).extract(request=request, document=document)
        payload = result.model_dump(mode="json", exclude_none=True)
        record: dict[str, Any] = {"status": "succeeded", "error": None}
    except ExtractionAdapterError as exc:
        payload = None
        record = {"status": "failed", "error": {
            "code": exc.code, "message": exc.safe_message,
            "retryable": exc.retryable, "diagnostics": exc.safe_diagnostics(),
        }}
    finally:
        if original_call is not None:
            setattr(adapter, "_call_provider", original_call)
    record.update(
        latency_seconds=round(time.perf_counter() - started, 6),
        adapter_attempts=attempts,
        retry_observed=len(attempts) > 1 if original_call is not None else None,
    )
    return record, payload


def score_fixture(
    entry: dict[str, Any], truth: dict[str, Any], payload: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Associate only a single returned form with a frozen single-page fixture.

    prepare_entries verifies the manifest's truth hash before this function runs.
    Only form identity in a deep scoring copy changes; business values never do.
    """
    forms = payload.get("logical_forms", [])
    truth_forms = truth["logical_forms"]
    logical_id = truth_forms[0]["form_id"] if len(truth_forms) == 1 else None
    association: dict[str, Any] = {
        "rule": "single_form_single_page_frozen_fixture_v1",
        "provider_form_id": forms[0]["form_id"] if len(forms) == 1 else None,
        "scoring_logical_form_id": logical_id,
        "ground_truth_sha256": entry["ground_truth_sha256"],
        "returned_form_count": len(forms),
    }
    if (len(forms) != 1 or len(truth_forms) != 1
            or truth["source_document"]["page_count"] != 1
            or truth_forms[0]["page_indices"] != [0]):
        association.update(status="identity_structure_failure", reason="SINGLE_FORM_SINGLE_PAGE_REQUIRED")
        return association, None
    view = deepcopy(payload)
    view["logical_forms"][0]["form_id"] = logical_id
    association["status"] = "associated"
    return association, score_extraction(truth, view)


def retryable_transient(error: dict[str, Any] | None) -> bool:
    if not error or not error["retryable"]:
        return False
    diagnostics = error.get("diagnostics", {})
    status = diagnostics.get("http_status_code")
    if error["code"] == "PROVIDER_RATE_LIMIT" or status in {400, 401, 403, 404, 422, 429}:
        return False
    if diagnostics.get("provider_status") in {
        "RESOURCE_EXHAUSTED", "insufficient_quota", "quota_exceeded", "billing_hard_limit_reached"
    }:
        return False
    if error["code"] in {"PROVIDER_TIMEOUT", "PROVIDER_CONNECTION_ERROR"}:
        return True
    return error["code"] == "PROVIDER_UNAVAILABLE" and status in {408, 500, 502, 503, 504}


def measured_with_retries(
    adapter: ExtractionAdapter, request: ExtractionRequest, document: DocumentInput,
    *, max_retries: int = 0, sleep: Callable[[float], None] = time.sleep,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Measurement-level retries; caller supplies a one-shot adapter and SDK."""
    if not 0 <= max_retries <= 2:
        raise ValueError("Task 7 permits at most two transient retries.")
    started = time.perf_counter()
    attempts = []
    delays = []
    for index in range(max_retries + 1):
        record, payload = measured_extraction(adapter, request, document)
        attempts.append({"number": index + 1, "status": record["status"],
                         "error": deepcopy(record["error"]), "latency_seconds": record["latency_seconds"],
                         "adapter_attempts": deepcopy(record["adapter_attempts"])})
        if record["status"] == "succeeded" or not retryable_transient(record["error"]) or index == max_retries:
            break
        delay = float(2**index)
        delays.append(delay)
        sleep(delay)
    record.update(
        latency_seconds=round(time.perf_counter() - started, 6),
        measurement_attempts=attempts, retry_delays_seconds=delays,
        retry_observed=len(attempts) > 1 or record["retry_observed"],
        adapter_attempts=[attempt for item in attempts for attempt in item["adapter_attempts"]],
    )
    return record, payload


def reusable_records(
    path: Path, manifest_path: Path, prepared: list[tuple[dict[str, Any], dict[str, Any], bytes]],
    *, provider: str, model: str | None, pipeline_version: str,
) -> dict[str, dict[str, Any]]:
    previous = load_json(checked_artifact_path(str(path)))
    if (previous.get("manifest_sha256") != sha256(manifest_path)
            or previous.get("provider") != provider or previous.get("model") != model
            or previous.get("prompt_version") != EXTRACTION_PROMPT_VERSION
            or previous.get("pipeline_version") != pipeline_version):
        raise ValueError("Prior run provenance does not match the frozen sample and configuration.")
    entries = {entry["fixture_id"]: entry for entry, _, _ in prepared}
    reused: dict[str, dict[str, Any]] = {}
    for record in previous["documents"]:
        if record["status"] != "succeeded":
            continue
        fixture_id = record["fixture_id"]
        if fixture_id in reused or fixture_id not in entries or record["source"] != entries[fixture_id]["source"]:
            raise ValueError("Prior successful record has conflicting fixture provenance.")
        payload = record["extraction_result"]
        result = ExtractionResult.model_validate(payload)
        validate_extraction_result_schema(result)
        if (result.provider.name != provider or result.provider.version != model
                or result.extraction_id != f"task7-{fixture_id}"):
            raise ValueError("Prior extraction metadata does not match the selected fixture/provider.")
        copy = deepcopy(record)
        copy.update(reused=True, reused_from={"path": str(path), "sha256": sha256(path)})
        copy["previous_scoring"] = deepcopy(record.get("scoring"))
        reused[fixture_id] = copy
    return reused


def summarize_scores(scores: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate counts per document; never match forms across different documents."""
    count_names = ("evaluated_fields", "exact_matches", "missing_values", "false_positive_values",
                   "skipped_unverified_fields", "skipped_non_scorable_fields")

    def totals(items: list[dict[str, Any]]) -> dict[str, Any]:
        counts: dict[str, Any] = {name: sum(item[name] for item in items) for name in count_names}
        counts["exact_match_rate"] = (
            counts["exact_matches"] / counts["evaluated_fields"] if counts["evaluated_fields"] else None
        )
        return counts

    return {
        "scored_documents": len(scores),
        "overall": totals([score["overall"] for score in scores]),
        "fields": {
            field: totals([score["fields"][field] for score in scores])
            for field in REQUIRED_FIELDS
        },
        "logical_forms": {
            name: sum(score["logical_forms"][name] for score in scores)
            for name in ("expected_count", "predicted_count", "matched_count", "missing_count", "unexpected_count")
        },
        "silently_accepted_errors": sum(
            score["silent_errors"]["silently_accepted_errors"] for score in scores
        ),
        "multi_value_metrics": {
            score["fixture_id"]: {
                field: {key: score["fields"][field][key] for key in ("precision", "recall", "f1")}
                for field in ("central_numbers", "commitment_numbers")
            } for score in scores
        },
    }


def summarize_evaluation(
    prepared: list[tuple[dict[str, Any], dict[str, Any], bytes]],
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Add annotation coverage, availability and difficulty without changing scoring.

    Accuracy is conditional on scored results. End-to-end yield includes failures
    in the selected-field denominator, but stays undefined until all are attempted.
    Provider success counts new attempts only; reused results are reported apart.
    """
    selected = {entry["fixture_id"] for entry, _, _ in prepared}
    record_ids = [record["fixture_id"] for record in records]
    if len(record_ids) != len(set(record_ids)) or set(record_ids) - selected:
        raise ValueError("Conflicting evaluation record identifiers.")

    def group(entries: list[tuple[dict[str, Any], dict[str, Any], bytes]]) -> dict[str, Any]:
        ids = {entry["fixture_id"] for entry, _, _ in entries}
        observed = [record for record in records if record["fixture_id"] in ids]
        scores = [r["scoring"] for r in observed if r.get("scoring") is not None]
        result = summarize_scores(scores)
        categories = ("verified_present", "verified_absent", "uncertain", "unreadable", "other_excluded")
        by_field = {name: dict.fromkeys(categories, 0) for name in REQUIRED_FIELDS}
        for _, truth, _ in entries:
            for form in truth["logical_forms"]:
                for name in REQUIRED_FIELDS:
                    field = form["fields"][name]
                    status, annotation = field["value_status"], field["annotation_status"]
                    category = ("verified_present" if annotation == "verified" and status == "present" else
                                "verified_absent" if annotation == "verified" and status == "absent" else
                                "uncertain" if annotation == "unverified" and status == "ambiguous" else
                                "unreadable" if status == "unreadable" else "other_excluded")
                    by_field[name][category] += 1
        annotations = {key: sum(field[key] for field in by_field.values()) for key in categories}
        annotations["excluded_fields"] = sum(annotations[key] for key in ("uncertain", "unreadable", "other_excluded"))
        selected_fields = sum(score_extraction(truth, {})["overall"]["evaluated_fields"] for _, truth, _ in entries)
        fresh = [record for record in observed if not record.get("reused", False)]
        provider_succeeded = sum(r["status"] == "succeeded" for r in fresh)
        result.update(
            selected_documents=len(entries), attempted_documents=len(observed),
            successful_documents=sum(r["status"] == "succeeded" for r in observed),
            failed_documents=sum(r["status"] == "failed" for r in observed),
            unattempted_documents=len(entries) - len(observed),
            reused_documents=len(observed) - len(fresh), provider_attempted_documents=len(fresh),
            provider_success_rate=provider_succeeded / len(fresh) if fresh else None,
            identity_structure_failures=sum(r.get("measurement_status") == "identity_structure_failure" for r in observed),
            selected_scorable_fields=selected_fields,
            end_to_end_exact_match_rate=(result["overall"]["exact_matches"] / selected_fields
                                        if selected_fields and len(observed) == len(entries) else None),
            annotations=annotations, annotations_by_field=by_field,
        )
        return result

    summary = group(prepared)
    difficulties = sorted({entry.get("difficulty", "unspecified") for entry, _, _ in prepared})
    summary["by_difficulty"] = {
        difficulty: group([item for item in prepared if item[0].get("difficulty", "unspecified") == difficulty])
        for difficulty in difficulties
    }
    return summary


def run_baseline(
    manifest_path: Path, output_path: Path, *, provider: str, authorize: bool,
    adapter_factory: Callable[[AppConfig], ExtractionAdapter | None] | None = None,
    resume_from: Path | None = None,
    max_transient_retries: int = 0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if not authorize:
        raise ValueError("Live baseline requires explicit external-processing authorization.")
    manifest = load_json(checked_artifact_path(str(manifest_path)))
    if manifest.get("approved_demo_data") is not True or manifest.get("approved_provider") != provider:
        raise ValueError("Selected data and provider lack explicit demo approval.")
    prepared = prepare_entries(manifest)
    output_path = checked_artifact_path(str(output_path))
    if output_path.exists():
        raise ValueError("Use a new output path to preserve the previous run.")
    config = replace(AppConfig.from_env(), default_extraction_adapter=provider)
    policy = ProviderPolicySnapshot(data_boundary="external_phi", allow_external_phi=True)
    requests = [ExtractionRequest(
        request_id=f"task7-{entry['fixture_id']}", logical_form_id=truth["logical_forms"][0]["form_id"],
        document_id=entry["fixture_id"], pages=[], fields=list(REQUIRED_FIELDS),
        pipeline_version=config.extraction_pipeline_version, provider_policy=policy,
    ) for entry, truth, _ in prepared]
    for request in requests:
        ProviderPolicy(config).require_external_phi_allowed(
            request=request, provider_name=provider, api_key=config.provider_api_key(provider),
        )
    if not 0 <= max_transient_retries <= 2:
        raise ValueError("Task 7 permits at most two transient retries.")
    if max_transient_retries and adapter_factory is None:
        raise ValueError("Transient retries require an explicit one-shot adapter/SDK factory.")
    cached = reusable_records(
        resume_from, manifest_path, prepared, provider=provider, model=config.provider_model(provider),
        pipeline_version=config.extraction_pipeline_version,
    ) if resume_from is not None else {}
    adapter = None

    report: dict[str, Any] = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "manifest_sha256": sha256(manifest_path),
        "provider": provider, "model": config.provider_model(provider),
        "prompt_version": EXTRACTION_PROMPT_VERSION,
        "pipeline_version": config.extraction_pipeline_version,
        "fixture_set": manifest["fixture_set"], "documents": [],
        "measurement_version": "task7-association-transient-v1",
        "resume_from": str(resume_from) if resume_from else None,
    }
    selected_scorable_fields = sum(
        score_extraction(truth, {})["overall"]["evaluated_fields"] for _, truth, _ in prepared
    )
    attempted_scorable_fields = 0
    output_path.parent.mkdir(parents=True, exist_ok=True)
    for (entry, truth, content), request in zip(prepared, requests, strict=True):
        attempted_scorable_fields += score_extraction(truth, {})["overall"]["evaluated_fields"]
        if entry["fixture_id"] in cached:
            record = cached[entry["fixture_id"]]
            payload = record["extraction_result"]
        else:
            if adapter is None:
                adapter = (adapter_factory or build_extraction_adapter)(config)
                if adapter is None:
                    raise ValueError("No live provider adapter was configured.")
            record, payload = measured_with_retries(
                adapter, request, DocumentInput(content=content, mime_type="application/pdf", filename="document.pdf"),
                max_retries=max_transient_retries, sleep=sleep,
            )
            record["reused"] = False
        record.update(fixture_id=entry["fixture_id"], source=entry["source"], extraction_result=payload)
        record["scoring"] = None
        if payload is not None:
            association, record["scoring"] = score_fixture(entry, truth, payload)
            record["identity_association"] = association
            record["provider_form_id"] = association["provider_form_id"]
            record["scoring_logical_form_id"] = association["scoring_logical_form_id"]
            record["measurement_status"] = association["status"]
        record["review_required_fields"] = [
            {"form_id": form["form_id"], "field": name}
            for form in (payload or {}).get("logical_forms", [])
            for name in REQUIRED_FIELDS
            if form["fields"].get(name, {}).get("value_status") != "present"
            or form["fields"].get(name, {}).get("review_status") in {"needs_review", "needs_clarification"}
        ]
        report["documents"].append(record)
        report["summary"] = summarize_evaluation(prepared, report["documents"])
        report["summary"].update(
            selected_documents=len(prepared), attempted_documents=len(report["documents"]),
            failed_documents=sum(item["status"] == "failed" for item in report["documents"]),
            reused_documents=sum(item.get("reused", False) for item in report["documents"]),
            identity_structure_failures=sum(
                item.get("measurement_status") == "identity_structure_failure" for item in report["documents"]
            ),
            selected_scorable_fields=selected_scorable_fields,
            attempted_scorable_fields=attempted_scorable_fields,
            end_to_end_exact_match_rate=(
                report["summary"]["overall"]["exact_matches"] / selected_scorable_fields
                if selected_scorable_fields and len(report["documents"]) == len(prepared) else None
            ),
        )
        # Save after every attempt. Values remain in the ignored local artifact, never stdout.
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # A document that exhausted transient retries stays a failure; try the next
        # document without resending this one. Permanent account/config errors stop the batch.
        if record["status"] == "failed" and not retryable_transient(record["error"]):
            break
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure approved demo fixtures using the existing scorer.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--provider", choices=registered_provider_names(), default="gemini")
    parser.add_argument("--authorize-external-processing", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = run_baseline(
            args.manifest, args.output, provider=args.provider, authorize=args.authorize_external_processing
        )
    except ExtractionAdapterError as exc:
        print(json.dumps({"status": "blocked", "code": exc.code, "message": exc.safe_message}))
        return 2
    except ValueError:
        print(json.dumps({"status": "blocked", "code": "INVALID_BASELINE_INPUT_OR_AUTHORIZATION"}))
        return 2
    print(json.dumps({"provider": report["provider"], "model": report["model"], "summary": report["summary"]}))
    return 0 if report["summary"]["failed_documents"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
