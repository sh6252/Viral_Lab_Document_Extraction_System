"""PostgreSQL business aggregates: immutable extraction and transactional reviews."""

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from functools import wraps
from hashlib import sha256
import json
import logging
from typing import ParamSpec, TypeVar

from sqlalchemy import Connection, Engine, func, insert, select, text, update

from backend.persistence import business_codec as codec
from backend.persistence.business_models import documents, events, records
from backend.api.records_schemas import RecordsQuery
from backend.persistence.business_search import conditions
from backend.services.business_review import BusinessReviewResult, FIELDS, snapshot_canonical
from backend.services.demo_workflow import DemoError, DemoRun
from backend.services.excel_export import ProcessingFailure


Parameters = ParamSpec("Parameters")
Result = TypeVar("Result")


def _safe(method: Callable[Parameters, Result]) -> Callable[Parameters, Result]:
    @wraps(method)
    def invoke(*args: Parameters.args, **kwargs: Parameters.kwargs) -> Result:
        try:
            return method(*args, **kwargs)
        except DemoError:
            raise
        except Exception:
            # SQL exceptions can contain DSNs/parameters; never cross the API boundary.
            raise DemoError("BUSINESS_STORAGE_UNAVAILABLE", 503) from None
    return invoke


class BusinessRepository:
    def __init__(self, engine: Engine, *, capacity_limits: tuple[int, int] | None = None):
        if engine.dialect.name != "postgresql":
            raise ValueError("POSTGRESQL_REQUIRED")
        self.engine = engine
        if capacity_limits is not None and any(limit <= 0 for limit in capacity_limits):
            raise ValueError("POSITIVE_BUSINESS_CAPACITY_REQUIRED")
        self.capacity_limits = capacity_limits

    @_safe
    def check_ready(self) -> None:
        """Fail before any provider call if tables are absent or the DB is unavailable."""
        with self.engine.connect() as conn:
            for table in (documents, records, events):
                conn.execute(select(table).limit(0))

    @_safe
    def create(self, run: DemoRun) -> None:
        if (run.status != "processing" or run.results or run.failure is not None
                or run.canonical_snapshot_json is not None or run.extraction_id is not None
                or sha256(run.source).hexdigest() != run.source_sha256):
            raise DemoError("INVALID_BUSINESS_DOCUMENT", 422)
        with self.engine.begin() as conn:
            if self.capacity_limits is not None:
                # Serialize bounded admissions in this database, in the insert transaction.
                conn.execute(text("SELECT pg_advisory_xact_lock(817420261022)"))
                count, size = conn.execute(select(func.count(), func.coalesce(func.sum(
                    func.octet_length(documents.c.source_bytes)), 0))).one()
                if count >= self.capacity_limits[0] or size + len(run.source) > self.capacity_limits[1]:
                    # Admission limits are application policy, not a PostgreSQL/Neon error.
                    # Aggregates only: never exception text, source, hash, token or SQL parameters.
                    logging.getLogger("uvicorn.error").info(json.dumps({
                        "event": "business_capacity_rejected", "run_id": run.run_id,
                        "document_limit_reached": count >= self.capacity_limits[0],
                        "source_byte_limit_reached": size + len(run.source) > self.capacity_limits[1],
                        "document_count": int(count), "source_bytes": int(size),
                        "incoming_source_bytes": len(run.source),
                        "max_documents": self.capacity_limits[0], "max_source_bytes": self.capacity_limits[1],
                    }, separators=(",", ":")))
                    raise DemoError("DEMO_CAPACITY_REACHED", 503)
            conn.execute(insert(documents).values(document_id=run.run_id, review_token=run.review_token,
                source_bytes=run.source, source_sha256=run.source_sha256, mime_type=run.mime_type,
                status="processing", prompt_version=run.prompt_version, pipeline_version=run.pipeline_version,
                created_at=datetime.now(timezone.utc)))

    def _load(self, conn: Connection, run_id: str, *, write: bool = False) -> DemoRun:
        # All writers lock this parent first; a shared read lock gives one consistent
        # source/forms/events view even at PostgreSQL's default READ COMMITTED level.
        root = conn.execute(select(documents).where(documents.c.document_id == run_id)
                            .with_for_update(read=not write)).mappings().one_or_none()
        if root is None:
            raise DemoError("DEMO_RUN_NOT_FOUND", 404)
        source = bytes(root["source_bytes"])
        if sha256(source).hexdigest() != root["source_sha256"]:
            raise ValueError("SOURCE_INTEGRITY_FAILURE")
        loaded = []
        rows = conn.execute(select(records).where(records.c.document_id == run_id)
                            .order_by(records.c.ordinal)).mappings().all()
        for ordinal, row in enumerate(rows):
            if codec.digest(row["projection_json"]) != row["projection_sha256"]:
                raise ValueError("PROJECTION_INTEGRITY_FAILURE")
            result = codec.decode(row["projection_json"], BusinessReviewResult)
            binding = result.record.binding
            if (row["ordinal"] != ordinal or row["form_id"] != result.record.form_id
                    or row["revision"] != result.record.revision or row["review_status"] != result.record.review_status
                    or result.canonical_snapshot_json != root["canonical_snapshot_json"]
                    or binding.document_id != run_id or binding.source_sha256 != root["source_sha256"]
                    or binding.extraction_id != root["extraction_id"]
                    or binding.canonical_snapshot_sha256 != root["canonical_snapshot_sha256"]
                    or binding.prompt_version != root["prompt_version"]
                    or binding.pipeline_version != root["pipeline_version"]
                    or binding.data_origin != "live" or binding.snapshot_kind != "live"):
                raise ValueError("BUSINESS_RECORD_SCOPE_FAILURE")
            saved_events = conn.execute(select(events).where(events.c.document_id == run_id,
                events.c.form_id == row["form_id"]).order_by(events.c.revision)).mappings().all()
            if len(saved_events) != len(result.audit):
                raise ValueError("AUDIT_INTEGRITY_FAILURE")
            for saved, entry in zip(saved_events, result.audit, strict=True):
                exact = codec.encode(entry)
                if (saved["event_json"] != exact or saved["event_sha256"] != codec.digest(exact)
                        or saved["decision_id"] != entry.action.decision_id
                        or saved["field_name"] != entry.action.field_name
                        or saved["previous_revision"] != entry.previous_revision or saved["revision"] != entry.revision):
                    raise ValueError("AUDIT_INTEGRITY_FAILURE")
            loaded.append(result)
        failure = None
        if root["status"] == "succeeded":
            snapshot, canonical = snapshot_canonical(root["canonical_snapshot_json"])
            if (snapshot != root["canonical_snapshot_json"] or codec.digest(snapshot) != root["canonical_snapshot_sha256"]
                    or canonical.extraction_id != root["extraction_id"]
                    or [r.record.form_id for r in loaded] != [f.form_id for f in canonical.logical_forms]):
                raise ValueError("EXTRACTION_INTEGRITY_FAILURE")
        elif loaded:
            raise ValueError("FAILED_EXTRACTION_HAS_RECORDS")
        if root["status"] == "failed":
            if codec.digest(root["failure_json"]) != root["failure_sha256"]:
                raise ValueError("FAILURE_INTEGRITY_FAILURE")
            failure = codec.decode(root["failure_json"], ProcessingFailure)
            if (failure.document_id != run_id or failure.attempt_reference != run_id
                    or failure.source_sha256 != root["source_sha256"] or failure.data_origin != "live"):
                raise ValueError("FAILURE_SCOPE_FAILURE")
        return DemoRun(run_id, root["review_token"], source, root["mime_type"], root["source_sha256"],
            0, root["prompt_version"], root["pipeline_version"], root["status"], root["extraction_id"],
            root["canonical_snapshot_json"], tuple(loaded), failure)

    @_safe
    def get(self, run_id: str) -> DemoRun:
        with self.engine.begin() as conn:
            return self._load(conn, run_id)

    @_safe
    def list_records(self, query: RecordsQuery) -> dict:
        """One row per form; bounded projections only, never SELECT original bytes."""
        joined = records.join(documents, records.c.document_id == documents.c.document_id)
        filters = conditions(query)
        # Count, page, and audit validation see the same committed MVCC snapshot.
        with self.engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
            with conn.begin():
                total = conn.execute(select(func.count()).select_from(joined).where(*filters)).scalar_one()
                rows = conn.execute(select(records, documents.c.created_at, documents.c.source_sha256,
                    documents.c.canonical_snapshot_sha256, documents.c.extraction_id,
                    documents.c.prompt_version, documents.c.pipeline_version).select_from(joined).where(*filters)
                    .order_by(documents.c.created_at.desc(), records.c.document_id, records.c.ordinal)
                    .limit(query.limit).offset(query.offset)).mappings().all()
                items = []
                for row in rows:
                    if codec.digest(row["projection_json"]) != row["projection_sha256"]:
                        raise ValueError("PROJECTION_INTEGRITY_FAILURE")
                    result = codec.decode(row["projection_json"], BusinessReviewResult)
                    record, binding = result.record, result.record.binding
                    if (record.form_id != row["form_id"] or record.revision != row["revision"]
                            or record.review_status != row["review_status"] or binding.document_id != row["document_id"]
                            or binding.source_sha256 != row["source_sha256"]
                            or binding.canonical_snapshot_sha256 != row["canonical_snapshot_sha256"]
                            or binding.extraction_id != row["extraction_id"]
                            or binding.prompt_version != row["prompt_version"]
                            or binding.pipeline_version != row["pipeline_version"]
                            or binding.data_origin != "live" or binding.snapshot_kind != "live"):
                        raise ValueError("BUSINESS_RECORD_SCOPE_FAILURE")
                    audit = conn.execute(select(events).where(events.c.document_id == row["document_id"],
                        events.c.form_id == row["form_id"]).order_by(events.c.revision)).mappings().all()
                    if len(audit) != len(result.audit):
                        raise ValueError("AUDIT_INTEGRITY_FAILURE")
                    for saved, entry in zip(audit, result.audit, strict=True):
                        payload = codec.encode(entry)
                        if (saved["event_json"] != payload or saved["event_sha256"] != codec.digest(payload)
                                or saved["decision_id"] != entry.action.decision_id
                                or saved["field_name"] != entry.action.field_name
                                or saved["previous_revision"] != entry.previous_revision
                                or saved["revision"] != entry.revision):
                            raise ValueError("AUDIT_INTEGRITY_FAILURE")
                    summary = {field.field_name: (list(field.reviewed_values)
                        if field.review_status in {"approved_provider", "approved_corrected", "accepted_by_form"}
                        else [value.raw for value in field.original.values] if field.review_status == "unreviewed" else [])
                        for field in record.fields}
                    assert tuple(summary) == FIELDS
                    items.append({"run_id": row["document_id"], "document_id": row["document_id"],
                        "form_id": record.form_id, "revision": record.revision,
                        "review_status": "completed" if record.review_status == "complete" else "pending",
                        "finalized": result.finalized,
                        "summary": summary, "created_at": row["created_at"],
                        "updated_at": max((event["received_at"] for event in audit), default=row["created_at"])})
                return {"items": items, "total": total, "limit": query.limit, "offset": query.offset}

    @_safe
    def complete(self, run: DemoRun) -> None:
        with self.engine.begin() as conn:
            previous = self._load(conn, run.run_id, write=True)
            if (previous.status != "processing" or run.status not in {"succeeded", "failed"}
                    or replace(run, status="processing", extraction_id=None, canonical_snapshot_json=None,
                               results=(), failure=None, expires_at=0) != previous):
                raise DemoError("BUSINESS_EXTRACTION_CONFLICT", 409)
            snapshot_hash = codec.digest(run.canonical_snapshot_json) if run.canonical_snapshot_json is not None else None
            failure = codec.encode(run.failure) if run.failure is not None else None
            conn.execute(update(documents).where(documents.c.document_id == run.run_id).values(status=run.status,
                extraction_id=run.extraction_id, canonical_snapshot_json=run.canonical_snapshot_json,
                canonical_snapshot_sha256=snapshot_hash, failure_json=failure,
                failure_sha256=codec.digest(failure) if failure is not None else None))
            for ordinal, result in enumerate(run.results):
                projection = codec.encode(result)
                if result.audit or result.record.revision:
                    raise ValueError("EXTRACTION_CANNOT_APPROVE_FIELDS")
                conn.execute(insert(records).values(document_id=run.run_id, form_id=result.record.form_id,
                    ordinal=ordinal, revision=0, review_status=result.record.review_status,
                    projection_json=projection, projection_sha256=codec.digest(projection)))
            self._load(conn, run.run_id)  # Validate the entire new aggregate before commit.

    def _append_events(self, conn: Connection, before: BusinessReviewResult, after: BusinessReviewResult) -> None:
        for entry in after.audit[len(before.audit):]:
            payload = codec.encode(entry)
            conn.execute(insert(events).values(document_id=after.record.binding.document_id,
                form_id=after.record.form_id, decision_id=entry.action.decision_id, field_name=entry.action.field_name,
                previous_revision=entry.previous_revision, revision=entry.revision, event_json=payload,
                event_sha256=codec.digest(payload), received_at=datetime.now(timezone.utc)))

    @_safe
    def review_atomic(self, run_id: str, transition: Callable[[DemoRun], DemoRun]) -> DemoRun:
        return self._review_atomic(run_id, transition, finalize=False)

    @_safe
    def finalize_atomic(self, run_id: str, transition: Callable[[DemoRun], DemoRun]) -> DemoRun:
        return self._review_atomic(run_id, transition, finalize=True)

    def _review_atomic(self, run_id: str, transition: Callable[[DemoRun], DemoRun], *, finalize: bool) -> DemoRun:
        with self.engine.begin() as conn:
            before = self._load(conn, run_id, write=True)
            if finalize:
                definition = conn.execute(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = 'business_review_events'::regclass AND conname = 'ck_business_event_field'")).scalar_one_or_none()
                if definition is None or "__form__" not in definition or "field_name = ANY" not in definition:
                    raise DemoError("FORM_APPROVAL_MIGRATION_REQUIRED", 503)
            after = transition(before)
            if replace(after, results=before.results) != before or len(after.results) != len(before.results):
                raise ValueError("INVALID_BUSINESS_REVIEW_TRANSITION")
            for old, new in zip(before.results, after.results, strict=True):
                if old == new:  # Exact command replay: no new event, timestamp or revision.
                    continue
                if (old.record.form_id != new.record.form_id or new.record.revision <= old.record.revision
                        or new.audit[:len(old.audit)] != old.audit):
                    raise ValueError("INVALID_BUSINESS_REVIEW_TRANSITION")
                projection = codec.encode(new)  # Mandatory authoritative domain replay validation.
                self._append_events(conn, old, new)
                conn.execute(update(records).where(records.c.document_id == run_id,
                    records.c.form_id == old.record.form_id, records.c.revision == old.record.revision).values(
                        revision=new.record.revision, review_status=new.record.review_status,
                        projection_json=projection, projection_sha256=codec.digest(projection)))
            return self._load(conn, run_id)
