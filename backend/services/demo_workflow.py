"""Bounded, non-durable local demo runs. No file store, jobs, OCR or database."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import secrets
from threading import RLock
import time
from typing import Callable, get_args

from backend.api.demo_schemas import DemoReviewCommand
from backend.core.config import AppConfig
from backend.domain import ExtractionRequest, ProviderPolicySnapshot
from backend.domain.models import RequiredFieldName
from backend.services.business_review import (
    BusinessReviewAction, BusinessReviewError, BusinessReviewResult, SourceBinding,
    apply_business_review, snapshot_canonical,
)
from backend.services.excel_export import ProcessingFailure, export_workbook
from backend.services.extraction.base import DocumentInput, ExtractionAdapter
from backend.services.extraction.errors import ExtractionAdapterError, ProviderNotAuthorized, ProviderUnavailable
from backend.services.extraction.orchestrator import ExtractionOrchestrator
from backend.services.extraction.prompts import EXTRACTION_PROMPT_VERSION


class DemoError(ValueError):
    def __init__(self, code: str, status: int = 422):
        self.code, self.status = code, status
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class DemoRun:
    run_id: str
    review_token: str
    source: bytes
    mime_type: str
    source_sha256: str
    expires_at: float
    prompt_version: str
    pipeline_version: str
    status: str = "processing"
    extraction_id: str | None = None
    canonical_snapshot_json: str | None = None
    results: tuple[BusinessReviewResult, ...] = ()
    failure: ProcessingFailure | None = None


class DemoWorkflow:
    """Explicit instance owned by one local process; IDs are sensitive capabilities.

    Capacity counts processing runs too. No silent eviction, background task or
    module-global registry. Fixed TTL, source/result byte bounds and review bounds.
    """

    def __init__(self, config: AppConfig, adapter: ExtractionAdapter | None, *, max_runs: int = 8,
                 max_source_bytes: int = 64 * 1024 * 1024, max_snapshot_bytes: int = 4 * 1024 * 1024,
                 ttl_seconds: float = 1800, clock: Callable[[], float] = time.monotonic):
        self.config, self.adapter = config, adapter
        self.max_runs, self.max_source_bytes = max_runs, max_source_bytes
        self.max_snapshot_bytes, self.ttl_seconds, self.clock = max_snapshot_bytes, ttl_seconds, clock
        if max_runs < 1 or max_source_bytes < 1 or max_snapshot_bytes < 1 or ttl_seconds <= 0:
            raise ValueError("INVALID_DEMO_LIMITS")
        self._runs: dict[str, DemoRun] = {}
        self._lock = RLock()

    def _prune(self) -> None:
        now = self.clock()
        self._runs = {key: run for key, run in self._runs.items() if run.expires_at > now}

    def _get(self, run_id: str) -> DemoRun:
        self._prune()
        run = self._runs.get(run_id)
        if run is None:
            raise DemoError("DEMO_RUN_NOT_FOUND", 404)
        return run

    def _store_new(self, run: DemoRun) -> None:
        self._runs[run.run_id] = run

    def _store_completed(self, run: DemoRun) -> None:
        self._get(run.run_id)  # The bounded in-memory demo may expire during extraction.
        self._runs[run.run_id] = run

    def create(self, content: bytes, mime_type: str, allow_external_processing: bool, locale: str) -> dict:
        with self._lock:
            self._prune()
            if (len(self._runs) >= self.max_runs
                    or sum(len(r.source) for r in self._runs.values()) + len(content) > self.max_source_bytes):
                raise DemoError("DEMO_CAPACITY_REACHED", 503)
            run = DemoRun(secrets.token_urlsafe(32), secrets.token_urlsafe(32), content, mime_type,
                          sha256(content).hexdigest(), self.clock() + self.ttl_seconds,
                          EXTRACTION_PROMPT_VERSION, self.config.extraction_pipeline_version)
            self._store_new(run)
        started = self.clock()
        try:
            if allow_external_processing is not True:
                raise ProviderNotAuthorized("Explicit external-processing permission is required.")
            if self.adapter is None:
                raise ProviderUnavailable("No adapter configured.", retryable=False)
            request = ExtractionRequest(request_id=secrets.token_urlsafe(24),
                logical_form_id=secrets.token_urlsafe(24), document_id=run.run_id, pages=[],
                fields=list(get_args(RequiredFieldName)), locale=locale,
                pipeline_version=self.config.extraction_pipeline_version,
                provider_policy=ProviderPolicySnapshot(data_boundary="external_phi", allow_external_phi=True))
            # Existing adapter owns policy, SDK handling and canonical conversion.
            result = ExtractionOrchestrator(self.adapter).extract(request=request,
                         document=DocumentInput(content, mime_type))
            snapshot, detached = snapshot_canonical(result)
            if len(snapshot.encode("utf-8")) > self.max_snapshot_bytes or len(detached.logical_forms) > 32:
                raise DemoError("DEMO_RESULT_LIMIT", 422)
            binding = SourceBinding(run.run_id, run.source_sha256, detached.extraction_id,
                sha256(snapshot.encode("utf-8")).hexdigest(), "live", "live",
                EXTRACTION_PROMPT_VERSION, self.config.extraction_pipeline_version)
            reviews = tuple(replace(apply_business_review(snapshot, binding, form.form_id),
                                    canonical_snapshot_json=snapshot) for form in detached.logical_forms)
            completed = replace(run, status="succeeded", canonical_snapshot_json=snapshot, results=reviews,
                                extraction_id=detached.extraction_id)
        except Exception as error:
            # No provider body/message, filename or business content in public errors.
            known = {"PROVIDER_NOT_AUTHORIZED", "MISSING_PROVIDER_CREDENTIALS", "PROVIDER_RATE_LIMIT",
                     "PROVIDER_TIMEOUT", "PROVIDER_UNAVAILABLE", "PROVIDER_CONNECTION_ERROR",
                     "PROVIDER_RESOURCE_UNAVAILABLE", "INVALID_PROVIDER_RESPONSE", "EXTRACTION_VALIDATION_ERROR",
                     "INVALID_PROVIDER_REQUEST"}
            code = error.code if isinstance(error, ExtractionAdapterError) and error.code in known else "EXTRACTION_FAILED"
            failed = ProcessingFailure(run.run_id, run.run_id, code, "live", source_sha256=run.source_sha256,
                provider=getattr(self.adapter, "provider_name", None),
                model=self.config.provider_model(getattr(self.adapter, "provider_name", "")),
                retryable=error.retryable if isinstance(error, ExtractionAdapterError) else False,
                latency_seconds=max(0.0, self.clock() - started))
            completed = replace(run, status="failed", failure=failed)
        with self._lock:
            self._store_completed(completed)
            return self._project(completed, None)

    @staticmethod
    def _project(run: DemoRun, form_id: str | None) -> dict:
        selected = next((r for r in run.results if r.record.form_id == form_id), None) if form_id is not None else (
            run.results[0] if run.results else None)
        if form_id is not None and selected is None:
            raise DemoError("DEMO_FORM_NOT_FOUND", 404)
        result = {"run_id": run.run_id, "review_token": run.review_token, "status": run.status,
                  "data_origin": "live", "form_ids": [r.record.form_id for r in run.results],
                  "extraction_id": run.extraction_id, "prompt_version": run.prompt_version,
                  "pipeline_version": run.pipeline_version,
                  "provider": selected.record.provider if selected else (run.failure.provider if run.failure else None),
                  "model": selected.record.model if selected else (run.failure.model if run.failure else None),
                  "selected_form": None, "failure": None}
        if run.failure is not None:
            result["failure"] = {"code": run.failure.failure_code, "retryable": run.failure.retryable,
                                 "message": "Extraction failed; no business record was created."}
        if selected is not None:
            result["selected_form"] = {"form_id": selected.record.form_id, "revision": selected.record.revision,
                "review_status": selected.record.review_status,
                "fields": [{"field_name": f.field_name,
                    "provider_values": [{"raw": v.raw, "normalized": v.normalized} for v in f.original.values],
                    "provider_value_status": f.original.value_status, "provider_review_status": f.original.review_status,
                    "reviewed_values": list(f.reviewed_values), "review_status": f.review_status}
                    for f in selected.record.fields]}
        return result

    def get(self, run_id: str, form_id: str | None = None) -> dict:
        with self._lock:
            return self._project(self._get(run_id), form_id)

    def history(self, run_id: str, form_id: str | None = None) -> dict:
        with self._lock:
            run = self._get(run_id)
            self._project(run, form_id)  # Apply the same unknown-form check as get().
            return {"run_id": run_id, "forms": [
                {"form_id": result.record.form_id, "revision": result.record.revision,
                 "audit": [asdict(entry) for entry in result.audit]}
                for result in run.results if form_id is None or result.record.form_id == form_id]}

    def source(self, run_id: str) -> tuple[bytes, str]:
        with self._lock:
            run = self._get(run_id)
            return run.source, run.mime_type

    def review(self, run_id: str, form_id: str, command: DemoReviewCommand) -> dict:
        with self._lock:
            run = self._get(run_id)
            updated = self._reviewed_run(run, form_id, command)
            self._runs[run_id] = updated
            return self._project(updated, form_id)

    def _reviewed_run(self, run: DemoRun, form_id: str, command: DemoReviewCommand) -> DemoRun:
        """Shared validation; callers own the in-memory lock or database transaction."""
        if not secrets.compare_digest(run.review_token, command.review_token):
            raise DemoError("DEMO_RUN_BINDING_MISMATCH", 404)
        previous = next((r for r in run.results if r.record.form_id == form_id), None)
        if previous is None:
            raise DemoError("DEMO_FORM_NOT_FOUND", 404)
        if len({a.field_name for a in command.actions}) != len(command.actions):
            raise DemoError("INVALID_DEMO_REVIEW")
        # Provenance and original snapshots are taken ONLY from server-owned data.
        fields = {f.field_name: f for f in previous.record.fields}
        binding = previous.record.binding
        actions = tuple(BusinessReviewAction(binding.source_sha256, binding.extraction_id,
            binding.canonical_snapshot_sha256, form_id, item.field_name, item.decision_id,
            command.expected_revision + index, command.operator_declaration, command.declared_at,
            item.reason, fields[item.field_name].original, item.action, tuple(item.reviewed_values))
            for index, item in enumerate(command.actions))
        try:
            reviewed = apply_business_review(run.canonical_snapshot_json, binding, form_id, actions,
                                            command.expected_revision, previous=previous)
        except BusinessReviewError as error:
            conflict = str(error) in {"STALE_REVIEW_REVISION", "CONFLICTING_DECISION_REPLAY", "DUPLICATE_DECISION_ID"}
            raise DemoError("DEMO_REVIEW_CONFLICT" if conflict else "INVALID_DEMO_REVIEW", 409 if conflict else 422) from None
        if len(reviewed.audit) > 100:
            raise DemoError("DEMO_REVIEW_LIMIT", 422)
        reviewed = replace(reviewed, canonical_snapshot_json=run.canonical_snapshot_json)
        updated = replace(run, results=tuple(reviewed if r.record.form_id == form_id else r for r in run.results))
        # Count snapshots once, plus accumulated audit text, to bound corrections.
        if sum(len(repr(r.audit).encode("utf-8")) for r in updated.results) > self.max_snapshot_bytes:
            raise DemoError("DEMO_REVIEW_LIMIT", 422)
        return updated

    def export(self, run_id: str) -> bytes:
        with self._lock:
            run = self._get(run_id)
            if run.status == "processing":
                raise DemoError("DEMO_STILL_PROCESSING", 409)
        return export_workbook(run.results, (run.failure,) if run.failure else ())
