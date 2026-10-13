# Architecture decision log - approved baseline

Status: approved architecture baseline, 2026-10-13. This document supersedes the initial proposed ADRs where they conflict with the approved business rules.

## Current decisions

ADR-001 ACCEPTED: Use a modular monolith: React + TypeScript frontend, FastAPI backend, PostgreSQL database, and a database-backed background worker. Avoid Redis, Celery, message brokers, and microservices for the MVP unless a concrete blocker appears.

ADR-002 ACCEPTED: The deployment architecture must be portable between cloud demonstration and future on-premises deployment. Cloud hosting and permission to transmit sensitive medical data to external AI providers are independent decisions.

ADR-003 ACCEPTED: Use a hybrid extraction architecture. The system supports local OCR and approved external vision providers through provider-independent extraction adapters.

ADR-004 ACCEPTED: External AI calls are denied by default. They can run only when explicitly enabled by configuration and allowed by provider policy.

ADR-005 ACCEPTED: Extraction adapters return candidate values, evidence, diagnostics, and structured errors. They do not make approval decisions.

ADR-006 ACCEPTED: Validation, field acceptance, review policy, and export eligibility are independent of the AI/OCR provider.

ADR-007 ACCEPTED: Logical forms are separate from extraction candidates and review state. One logical commitment form equals one Excel row.

ADR-008 ACCEPTED: Export all logical forms to Excel, including unresolved forms, with explicit record and field review statuses. Processing failures must be represented separately and must not be presented as extracted commitments.

ADR-009 ACCEPTED: Preserve raw values, normalized values, evidence, validation state, review state, provider metadata, and pipeline version for every extraction attempt.

ADR-010 ACCEPTED: Use evaluation-first development. Benchmark fixtures and deterministic metrics are implemented before the extraction pipeline is optimized.

## Superseded or clarified decisions

- The earlier "only approved records may enter final export" rule is superseded by BR-004. The export now includes all logical forms with review metadata. Approval affects status and downstream trust, not row inclusion.
- The earlier "one row per commitment" wording is clarified as one row per logical commitment form. Multiple central numbers and multiple commitment numbers are exported as multi-value fields on that row unless a future business decision changes this.
- Segmentation and logical form grouping remain explicit reviewable entities, but export cardinality is governed by logical forms, not by individual identifier counts.

## Module boundaries

### Frontend

- Batch upload
- Batch/document/job status
- Page image viewer with evidence overlays
- Logical form review
- Field correction and approval workflow
- Export request and download

### Backend API

- Authentication and authorization checks
- Typed REST contracts
- Document ingestion orchestration
- Review mutations and optimistic concurrency
- Export generation
- Evaluation command/API surfaces for local benchmarks

### Domain services

- Document ingestion
- Page rendering
- Logical form segmentation
- Extraction orchestration
- Candidate normalization
- Field validation
- Review policy
- Excel export
- Evaluation

### Worker

- PostgreSQL job claiming
- Stage execution
- Lease heartbeat
- Retry scheduling
- Crash recovery
- Idempotency enforcement

### Adapters

- Storage adapter for original files, rendered pages, thumbnails, and exports
- OCR adapter for local text extraction
- Extraction adapter for local OCR, external vision AI, or hybrid extraction
- Provider policy adapter for outbound AI permission decisions

## Domain model summary

### Batch

Upload group containing one or more documents.

Important fields:

- `id`
- `created_by`
- `status`
- `document_count`
- `logical_form_count`
- `failure_count`
- `created_at`
- `updated_at`

### Document

Uploaded source file.

Important fields:

- `id`
- `batch_id`
- `original_filename`
- `content_type`
- `sha256`
- `storage_uri`
- `processing_status`
- `created_at`

### DocumentPage

Rendered and inspectable page from a document.

Important fields:

- `id`
- `document_id`
- `page_index`
- `image_uri`
- `width`
- `height`
- `rotation_degrees`
- `ocr_artifact_uri`

### LogicalForm

Reviewable form unit. One logical commitment form equals one Excel row.

Important fields:

- `id`
- `document_id`
- `form_status`
- `review_status`
- `form_family`
- `sender_candidate`
- `version`
- `created_at`
- `updated_at`

### LogicalFormPage

Join table preserving page membership and order.

Important fields:

- `logical_form_id`
- `document_page_id`
- `position`
- `source`
- `review_status`

### FieldCandidate

Raw and normalized candidate value for one business field.

Important fields:

- `id`
- `logical_form_id`
- `field_name`
- `raw_value`
- `normalized_value`
- `value_status`
- `validation_status`
- `review_status`
- `provider_name`
- `pipeline_version`
- `created_at`

`field_name` values:

- `first_name`
- `last_name`
- `israeli_id`
- `central_numbers`
- `sample_receipt_date`
- `sender`
- `commitment_numbers`

`value_status` values:

- `present`
- `missing`
- `unreadable`
- `ambiguous`
- `not_processed`

### Evidence

Trace from candidate value to source material.

Important fields:

- `id`
- `field_candidate_id`
- `document_page_id`
- `source_kind`
- `bounding_box`
- `text_snippet`
- `image_uri`
- `barcode_payload`
- `notes`

`source_kind` values:

- `ocr_text`
- `vision_model`
- `barcode`
- `sticker`
- `stamp`
- `handwriting`
- `manual_review`

### ReviewEvent

Append-only audit event for user and system decisions.

Important fields:

- `id`
- `entity_type`
- `entity_id`
- `event_type`
- `actor`
- `previous_state`
- `new_state`
- `reason`
- `created_at`

### ProcessingJob

Durable worker job.

Important fields:

- `id`
- `job_type`
- `aggregate_type`
- `aggregate_id`
- `state`
- `priority`
- `run_after`
- `attempt_count`
- `max_attempts`
- `locked_by`
- `lease_expires_at`
- `idempotency_key`
- `input_hash`
- `pipeline_version`
- `last_error_code`
- `last_error_message_safe`
- `created_at`
- `updated_at`

### ProcessingFailure

A failed document, page, or form processing result that must be exported separately from commitment rows.

Important fields:

- `id`
- `batch_id`
- `document_id`
- `logical_form_id`
- `stage`
- `error_code`
- `safe_message`
- `retryable`
- `created_at`

## Extraction contracts

### ExtractionRequest

```json
{
  "request_id": "uuid",
  "logical_form_id": "uuid",
  "document_id": "uuid",
  "pages": [
    {
      "page_id": "uuid",
      "page_index": 0,
      "image_uri": "storage://rendered/page-0.png",
      "width": 2480,
      "height": 3508,
      "rotation_degrees": 0,
      "ocr_text_uri": "storage://ocr/page-0.json"
    }
  ],
  "fields": [
    "first_name",
    "last_name",
    "israeli_id",
    "central_numbers",
    "sample_receipt_date",
    "sender",
    "commitment_numbers"
  ],
  "locale": "he-IL",
  "pipeline_version": "string",
  "provider_policy": {
    "data_boundary": "local_only",
    "allow_external_phi": false
  }
}
```

### ExtractionResponse

```json
{
  "request_id": "uuid",
  "adapter_name": "local_ocr",
  "adapter_version": "string",
  "data_boundary": "local_only",
  "status": "succeeded",
  "candidates": [],
  "errors": [],
  "diagnostics": {
    "page_count": 1,
    "used_ocr": true,
    "used_external_ai": false
  }
}
```

`status` values:

- `succeeded`
- `partial`
- `failed`
- `blocked_by_policy`

### FieldCandidate contract

```json
{
  "field_name": "central_numbers",
  "raw_value": "string",
  "normalized_value": "string",
  "value_status": "present",
  "candidate_index": 0,
  "signals": {
    "ocr_confidence": 0.82,
    "model_confidence": null,
    "pattern_match": true
  },
  "evidence": [],
  "notes": "string"
}
```

Provider confidence may be stored in `signals`, but it is not an approval decision.

### Evidence contract

```json
{
  "page_id": "uuid",
  "source_kind": "ocr_text",
  "bounding_box": {
    "x": 100,
    "y": 200,
    "width": 300,
    "height": 80
  },
  "text_snippet": "string",
  "image_uri": "storage://evidence/crop.png",
  "barcode_payload": null,
  "notes": "string"
}
```

### Structured error contract

```json
{
  "code": "EXTERNAL_AI_BLOCKED",
  "stage": "extraction",
  "retryable": false,
  "safe_message": "External AI adapter is disabled by provider policy.",
  "details": {
    "adapter_name": "external_vision",
    "data_boundary": "external_phi"
  }
}
```

Error requirements:

- `safe_message` must not contain patient data.
- Retryable infrastructure failures may be retried by the worker.
- Policy failures are fail-closed and non-retryable until configuration changes.
- Extraction errors produce structured failures; they must not create fabricated field values.

## Provider policy interface

Provider policy is checked before any extraction adapter can read PHI or make outbound calls.

Conceptual interface:

```text
ProviderPolicy.evaluate(request, adapter) -> ProviderDecision
```

`ProviderDecision` fields:

- `allowed`: boolean
- `reason_code`: string
- `data_boundary`: `local_only | external_no_phi | external_phi`
- `allow_external_phi`: boolean
- `requires_audit`: boolean
- `safe_message`: string

Fail-closed rules:

- External AI is denied when `AI_OUTBOUND_ENABLED` is absent or false.
- External PHI transmission is denied unless `ALLOW_EXTERNAL_PHI=true` and the selected provider has an approved policy entry.
- Missing provider policy denies execution.
- Unknown provider denies execution.
- Unknown data boundary denies execution.
- Policy denial returns `blocked_by_policy` with structured error `EXTERNAL_AI_BLOCKED`.
- Local OCR remains allowed when local dependencies and storage permissions are available.

Configuration examples:

- `AI_OUTBOUND_ENABLED=false`
- `ALLOW_EXTERNAL_PHI=false`
- `APPROVED_AI_PROVIDERS=`
- `DEFAULT_EXTRACTION_ADAPTER=local_ocr`

## Review state machines

### Field review state

```text
unreviewed -> approved_as_extracted
unreviewed -> corrected
unreviewed -> rejected
unreviewed -> acknowledged_blank
unreviewed -> needs_clarification
corrected -> approved_corrected
approved_as_extracted -> corrected
approved_corrected -> corrected
rejected -> corrected
acknowledged_blank -> corrected
```

### Logical form review state

```text
detected -> extracted
detected -> processing_failed
extracted -> needs_review
needs_review -> reviewed
needs_review -> rejected_as_non_commitment_form
reviewed -> reopened
reopened -> reviewed
```

### Export status

```text
not_exported -> export_ready
not_exported -> exportable_with_unresolved_fields
not_exported -> processing_failed
export_ready -> exported
exportable_with_unresolved_fields -> exported
processing_failed -> failure_exported
```

Export includes every logical form row and explicit statuses under BR-004. Processing failures are represented separately.

## API contract outline

- `POST /api/batches`: create batch, upload files, enqueue processing.
- `GET /api/batches/{batch_id}`: batch status, document counts, logical form counts, failure counts, export readiness.
- `GET /api/documents/{document_id}`: document metadata, pages, logical forms, processing state.
- `GET /api/pages/{page_id}/image`: authenticated page image access.
- `GET /api/logical-forms/{form_id}`: pages, candidates, evidence, review state, export status.
- `PATCH /api/logical-forms/{form_id}/pages`: reviewer updates page grouping with optimistic concurrency.
- `PATCH /api/fields/{field_candidate_id}/review`: approve, correct, reject, acknowledge blank, or request clarification.
- `POST /api/exports`: generate Excel for all logical forms in a batch plus separate processing failure representation.
- `GET /api/exports/{export_id}`: export status and download metadata.
- `GET /api/jobs/{job_id}`: durable job status with safe errors only.

Mutating endpoints must include optimistic concurrency through `expected_version` or `If-Match`. Stale writes return `409 Conflict` with the latest safe state.

## PostgreSQL worker design

Jobs are claimed with row-level locks and leases:

```sql
UPDATE processing_jobs
SET state = 'running',
    locked_by = :worker_id,
    lease_expires_at = now() + interval '60 seconds',
    attempt_count = attempt_count + 1,
    updated_at = now()
WHERE id IN (
  SELECT id
  FROM processing_jobs
  WHERE state = 'queued'
    AND run_after <= now()
  ORDER BY priority DESC, created_at ASC
  FOR UPDATE SKIP LOCKED
  LIMIT :limit
)
RETURNING *;
```

Worker rules:

- Workers heartbeat by extending `lease_expires_at`.
- Expired leases are eligible for requeue.
- Jobs use deterministic `idempotency_key` values and unique constraints to prevent duplicate side effects.
- Transient failures retry with exponential backoff and jitter.
- Non-retryable validation or policy failures become structured processing failures.
- Stage outputs are idempotent: re-running a stage must update or reuse existing outputs for the same `input_hash` and `pipeline_version`.
- No job may write patient data to logs or unsafe error messages.

## Evaluation-first development

Evaluation is the first implementation phase.

### Benchmark fixture contract

Each fixture is versioned and can evaluate local OCR, vision AI, and hybrid extraction results.

```json
{
  "schema_version": "1.0",
  "fixture_id": "string",
  "fixture_set": "dev",
  "source_document": {
    "filename": "string",
    "sha256": "string",
    "page_count": 1
  },
  "logical_forms": [
    {
      "form_id": "truth-form-1",
      "page_indices": [0],
      "fields": {
        "first_name": {
          "annotation_status": "verified",
          "value_status": "present",
          "values": [{ "raw": "string" }],
          "evidence": []
        },
        "last_name": {
          "annotation_status": "verified",
          "value_status": "present",
          "values": [{ "raw": "string" }],
          "evidence": []
        },
        "israeli_id": {
          "annotation_status": "verified",
          "value_status": "present",
          "values": [{ "raw": "string" }],
          "evidence": []
        },
        "central_numbers": {
          "annotation_status": "verified",
          "value_status": "present",
          "values": [{ "raw": "string" }],
          "evidence": []
        },
        "sample_receipt_date": {
          "annotation_status": "verified",
          "value_status": "present",
          "values": [{ "raw": "YYYY-MM-DD" }],
          "evidence": []
        },
        "sender": {
          "annotation_status": "verified",
          "value_status": "present",
          "values": [{ "raw": "string" }],
          "evidence": []
        },
        "commitment_numbers": {
          "annotation_status": "verified",
          "value_status": "present",
          "values": [{ "raw": "string" }],
          "evidence": []
        }
      },
      "notes": "string"
    }
  ]
}
```

`fixture_set` values:

- `dev`: used for implementation and adapter iteration.
- `holdout`: frozen, not used for tuning or prompt changes.

Ground-truth field `value_status` values:

- `present`
- `absent`
- `unknown`
- `not_annotated`
- `unreadable`
- `ambiguous`
- `not_applicable`

Use `absent` only for verified ground-truth absence. Use extraction-result `missing` when a system fails to extract a value. Unknown or unverified ground truth remains distinct from absence and is excluded from accuracy calculations until verified.

### Metrics

Evaluate each extraction mode separately:

- `local_ocr`
- `vision_ai`
- `hybrid`

Required deterministic metrics:

- Field exact match after normalization.
- Field raw-value preservation rate.
- Multi-value precision, recall, and F1 for central numbers.
- Multi-value precision, recall, and F1 for commitment numbers.
- Missing value rate: truth has value, system returns missing or blank.
- False positive rate: truth is missing/unreadable/not applicable, system returns a value.
- Ambiguity preservation rate: truth ambiguous and system marks ambiguous or needs review.
- Logical form detection recall.
- Excel row count accuracy: expected logical form count vs exported row count.
- Processing failure representation accuracy.
- Silently accepted error rate: wrong, ambiguous, unreadable, or missing field exported with an approved/clean status.
- Review burden: percentage of fields and forms requiring human review.

Acceptance priority for MVP: silently accepted errors must be surfaced and counted. A lower automation rate is acceptable; hidden wrong approvals are not.

## Seven-day implementation sequence

### Day 1 - Evaluation and contracts

Dependencies: approved architecture and business rules.

Tasks:

- Create versioned benchmark fixture schema.
- Add dev and holdout fixture directories.
- Implement deterministic evaluator skeleton.
- Define extraction and evidence contracts in backend types.

Acceptance criteria:

- A sample fixture can be evaluated against a stub extraction response.
- Metrics include exact match, false positives, missing values, row count, and silently accepted errors.

### Day 2 - Backend domain and migrations

Dependencies: Day 1 contracts.

Tasks:

- Add FastAPI project skeleton.
- Add SQLAlchemy or SQLModel entities for batches, documents, pages, logical forms, field candidates, evidence, jobs, failures, and review events.
- Add migrations.

Acceptance criteria:

- Database can be created from scratch.
- Unit tests verify core relationships and enum constraints.

### Day 3 - Ingestion and storage

Dependencies: Day 2 domain model.

Tasks:

- Implement batch upload.
- Store original files through storage adapter.
- Render PDF/image pages.
- Persist document pages and enqueue processing jobs.

Acceptance criteria:

- A batch upload creates documents, pages, and jobs.
- Re-upload of identical content is detected by checksum and handled deterministically.

### Day 4 - PostgreSQL worker and local OCR path

Dependencies: Day 3 jobs and pages.

Tasks:

- Implement job claiming with `FOR UPDATE SKIP LOCKED`.
- Implement leases, heartbeat, retry backoff, crash recovery, and idempotency keys.
- Implement local OCR adapter placeholder or baseline.

Acceptance criteria:

- Multiple worker processes can run without double-processing the same job.
- Expired jobs are recoverable.
- Policy-denied jobs fail closed with structured errors.

### Day 5 - Extraction orchestration and validation policy

Dependencies: Day 4 worker and adapters.

Tasks:

- Wire `ExtractionRequest` and `ExtractionResponse`.
- Persist candidates and evidence.
- Implement provider policy checks.
- Implement field-specific validation policy skeleton.

Acceptance criteria:

- Extraction adapter output creates candidates and evidence without approving fields.
- External AI remains denied by default.
- Validation status is provider-independent.

### Day 6 - Review workflow and Excel export

Dependencies: Day 5 candidates and validation.

Tasks:

- Implement review APIs with optimistic concurrency and audit events.
- Implement review UI for field statuses and corrections.
- Implement Excel export for all logical forms plus separate processing failures.

Acceptance criteria:

- Review actions produce audit events.
- Stale update attempts return conflict.
- Export contains all logical forms and review metadata.

### Day 7 - End-to-end demo and benchmark report

Dependencies: Days 1-6.

Tasks:

- Run demo batch through upload, processing, review, and export.
- Run evaluator for local OCR, vision AI when explicitly enabled, and hybrid when configured.
- Fix critical integration defects.
- Prepare demo script and known limitations.

Acceptance criteria:

- End-to-end demo works on Docker Compose.
- Benchmark report separates dev and holdout fixture results.
- No outbound AI calls occur unless explicitly configured.

## Unresolved decisions

UD-001. Final Excel schema: exact columns, sheet names, status labels, and formatting.

UD-002. Sender controlled vocabulary and unknown-sender policy.

UD-003. Field-specific automatic acceptance policies and thresholds.

UD-004. External AI provider approval, including provider, region, retention, logging, and contractual controls.

UD-005. Duplicate/re-upload behavior beyond exact checksum matching.

UD-006. Authentication and role model for production users.

UD-007. Long-term retention policy for original documents, rendered pages, OCR artifacts, review history, and exported Excel files.

## Anti-goals for MVP

- No microservices.
- No Redis or Celery.
- No hard dependency on cloud AI.
- No hidden model-based approval decisions.
- No patient data in logs.
- No claim of production compliance based only on the demo benchmark.
