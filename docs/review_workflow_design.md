# Task 10E - runtime review workflow boundary (design only)

Status: Task 10F implements the internal in-memory service and synthetic tests;
it is not wired into runtime. The original Task 10E design is retained below with
implementation details and validation results updated.
Scope: compose the existing offline components in memory, for `central_numbers`
and `commitment_numbers` only. No storage, database, API change, UI, OCR engine,
provider call, merge operation or Excel export is part of this task.

## Current components and authority

`backend/services/extraction/orchestrator.py::ExtractionOrchestrator.extract`
invokes its injected `ExtractionAdapter`, validates the canonical result schema
and returns `ExtractionResult`. It has no OCR/review coordination responsibilities.
Provider failures retain their existing exception/HTTP processing-failure behavior.

`backend/domain/models.py::ExtractionResult` is canonical business-shaped extraction
data, automatically produced by the provider path. Canonical schema validity is
not human verification or business approval. Its Pydantic models/lists are mutable,
so retaining a live reference inside a frozen dataclass would not isolate review.
It contains extraction/provider/form metadata but no document-to-image hash binding.

`backend/services/extraction/ocr_candidates.py` provides:

- `OCRCandidate`: immutable supporting evidence from a separately screened token,
  with explicit field assignment and document/form/page/image/engine/run provenance.
  `syntax_screened_unverified` is not proof of correct digits or human approval.
- `assess_ocr_candidates`: automated, pure comparison of canonical provider values
  and admitted candidates, using externally supplied provenance.
- `SupportingAssessment`: immutable automated advice, including source status,
  per-form/field comparison, candidate evidence and review recommendations. It is
  neither a replacement extraction nor a field-acceptance decision.

`backend/services/extraction/ocr_review.py` provides:

- `OCRReviewItem`: immutable candidate plus detached `ProviderValue` snapshots and
  optional decision. This is a supporting review work item, not a business record.
- `OCRReviewDecision`: explicit human-input artifact. Both field assignment and
  exact reviewed text must be confirmed to approve a candidate. Reviewer identity
  and time are declarations here, not authenticated by this pure module.
- `apply_review_decision`: returns a separate reviewed item; exact replay is
  idempotent and conflicting/subsequent decisions fail. No canonical mutation.

Implemented composition, available only to an explicit internal caller:

```text
existing orchestrator -> valid canonical result -------------------+
separately supplied screened OCR candidates + trusted provenance ---+
                                                                 |
                                      ReviewWorkflowService.prepare_review
                                                                 |
                                      ReviewSession + supporting assessment
                                                                 |
explicit human OCRReviewDecision -> apply_decision -> new ReviewSession
                                                    + ReviewOutcome
```

The application service sits AFTER extraction/canonical validation, outside both
the adapter and orchestrator. It consumes completed results; it does not invoke
extraction or OCR. The existing stateless HTTP endpoint stays unchanged. An eventual
runtime caller may invoke this service explicitly; no coordinator/framework is
included in the in-memory implementation.

## Implemented service interface

Methods of `ReviewWorkflowService`, not new public contracts:

```python
prepare_review(
    extraction_result, ocr_candidates, *, document_id, form_id,
    form_page_images, source_status, session_id, prepared_at,
) -> ReviewSession

apply_decision(
    review_session, review_decision, *, expected_revision, received_at,
) -> ReviewOutcome
```

All IDs and timezone-aware timestamps are explicit inputs; the pure service does
not generate time, read images or access storage. `source_status` uses existing
`succeeded`, `disabled`, `unavailable`, `failed` values. Candidate input is
materialized once; non-successful OCR source status requires no candidates.

One session belongs to exactly ONE document and ONE logical form. A canonical
result may contain multiple forms: validate its complete provenance and retain its
full snapshot, but expose actionable items only for the exact selected `form_id`.
Do not partition or rewrite the original result to manufacture matching IDs. Other
forms require separate sessions; no decision can affect them.

`form_page_images` is the existing document-local mapping of ALL result form IDs
to ONE-based page numbers and lowercase image SHA-256 hashes. This complete mapping
is required by `assess_ocr_candidates`, even for a single selected form. The caller
must establish its association to the actual document and rendered images.
Canonical `page_index` is zero-based: conversion belongs to the trusted caller,
not implicit guessing here. No form/value similarity or ground truth is used.

### Preparation

1. Require a valid nonempty canonical result and exact selected form membership;
   reject duplicate form IDs, invalid provenance or inconsistent canonical fields.
   Reuse the existing schema validator; no schema change is proposed.
2. Serialize a detached snapshot with all canonical fields, including null values,
   using a versioned, deterministic JSON encoding. Store immutable text and its
   SHA-256, not a mutable `ExtractionResult` or nested dict. Validation/assessment
   uses a private reconstruction. Mutation of either the caller's result or a
   returned reconstruction must never affect the session.
   Encoding `canonical-json-v1` uses `model_dump(mode="json", warnings="error")`,
   sorted object keys, compact comma/colon separators, `ensure_ascii=False` and
   `allow_nan=False`, retaining all nulls and array ordering. SHA-256 hashes the
   UTF-8 encoding. This stabilizes serialization without changing string values.
3. Call `assess_ocr_candidates` with the complete snapshot, candidate batch,
   document/provenance mapping and source status. Keep its full immutable output;
   only the selected form's fields participate in this session's work/status.
4. For each selected-form `FieldAssessment` with `review_recommended=True`, create
   one `OCRReviewItem` per distinct `candidate_reference`, passing the field's
   immutable provider-value snapshots. Include all candidates in that field;
   do not select alternatives by apparent correctness or value length. Repeated
   identical candidate references share an item; every occurrence remains in the
   assessment. Different source/run/page/box evidence remains distinct.
5. Fields without a recommendation retain candidates as evidence only. In
   particular `corroborated` or `partial_support` alone creates no task and grants
   no approval. Existing `needs_review`/`needs_clarification` still creates tasks
   even if OCR corroborates.
6. A recommended field with NO candidate becomes a `pending_field_issue`, not a
   fabricated OCR item. Also retain a pending issue for a supported field that is
   omitted or non-present without candidate coverage, even if review metadata is
   absent. This conservative workflow check implements BR-002 without changing
   the assessment or canonical statuses. Such issues cannot be discharged through
   `OCRReviewDecision`; a future general field-review action is needed.

Preserve selected-form review flags on the other five business fields as explicit
`outside_scope_review_issues` in the outcome. They are not OCR tasks and do not
participate in this narrowly scoped status. Omitted/non-present fields must not be
silently treated as approved merely because they lack review metadata. The service
does not decide general field acceptance or clear pre-existing human approvals.

### Applying a decision

Validate the session identity/input fingerprint and select the candidate by its
stable reference within the session's actionable items. Evidence-only/nonexistent
references are not actionable. Validate document, selected form, page and image
hash against both item and immutable session provenance before application.

Use the existing `apply_review_decision`; require both checks for approval, preserve
raw/normalized provider and OCR text, exact reviewed text and correction flags.
Return a new session with the replaced immutable item, appended accepted decision,
receipt timestamp and incremented revision. Recompute status; never mutate inputs.
All validation is atomic: a failed decision changes nothing.

Decision IDs are unique across the session, not just within each item. An identical
previously accepted ID/payload is a replay: return the current unchanged session,
no new event/timestamp/revision. Check valid identity and exact replay BEFORE the
revision precondition, so a retry after success can safely carry an old revision.
Reusing an ID with different content is a conflict. A new decision for an already
decided item also fails under the current one-action gate. All other new decisions
require `expected_revision == session.revision`; stale writes fail explicitly.
This is an in-memory precondition, not a claim of durable concurrency protection.

The implementation re-derives preparation from the stored canonical snapshot,
complete assessment evidence and provenance, then replays accepted history before
accepting an action. Derived items, issues, versions, hashes, fingerprint, state,
revision and receipt times must reproduce the stored session exactly. A changed
snapshot/evidence or inconsistent history fails with `INVALID_REVIEW_SESSION`.
This is consistency validation, not cryptographic authentication of a caller who
can fabricate every input. No global session-ID or cross-session replay guarantee
is possible without the future storage/authentication boundary.

## ReviewSession and ReviewOutcome

Use small frozen internal dataclasses and tuples, not public domain/schema changes.
The frozen `ReviewSession` contains:

- `session_id`, `document_id`, selected `form_id`, `extraction_id`.
- Immutable full `canonical_snapshot_json`, snapshot hash and encoding version.
- Immutable complete form/page/image provenance tuple; full `SupportingAssessment`.
- Sorted tuple of actionable `OCRReviewItem` objects (by candidate reference).
- `pending_field_issues`: selected supported field name and explicit reason codes
  for requirements with no candidate; no invented value or field approval.
- `outside_scope_review_issues`: selected-form review issues for the other five
  fields, excluded from candidate workflow completion but visible in the outcome.
- Accepted decisions with service receipt timestamps, in application order; items
  reference the same decisions. Derive their consistency rather than trusting two
  independent mutable lists. Exact replay adds nothing to either representation.
- `overall_review_status`, `revision` (starts at zero), `prepared_at`, `updated_at`.
- Workflow/assessment/review versions and deterministic `input_fingerprint` binding
  document/form, snapshot, sorted evidence occurrences including multiplicity,
  provenance, OCR source status and policy versions. No sensitive values in logs.

Caller-supplied `session_id` identifies an attempt, while the input fingerprint
binds its content (the fingerprint also includes `session_id`). The service does
not promise global uniqueness or find previous
sessions without storage. Repeating preparation with the same explicit inputs
must give equal output; changing inputs requires a new session, not evidence
appended to a previously reviewed session.

`ReviewOutcome` is a small separate return value containing the new/current session,
replay flag, approved candidate references with exact reviewed values, rejected
references, unverified/unresolved references, pending field issues and outside-scope
review issues. Lists are derived from the session. Approved values retain their
field and provenance; never union them into provider fields or infer identifier
pairings. This outcome is advice/audit data, not an export-ready business record.

Implementation names: immutable `FormPageImage` entries store flattened sorted
provenance; `FieldReviewIssue` stores a field and reason tuple;
`AcceptedReviewDecision` stores a decision plus receipt time. `ReviewOutcome`
exposes derived properties `approved_values` (tuple of `ApprovedReviewValue`),
`rejected_references`, `unresolved_references`, `unverified_references`,
`pending_field_issues` and `outside_scope_review_issues`. Each approved value
retains the full immutable candidate, its derived reference/field and exact
reviewed text. There is no separate mutable outcome list to become inconsistent.

## Overall states and transitions

Let `N` be actionable item count, `A` number with any explicit decision, `T` number
with a terminal decision (`approved` OR `rejected`), and `B` pending field issue
count. Evaluate in this order:

1. `no_review_required`: `N == 0` and `B == 0`.
2. `completed`: `N > 0`, `T == N` and `B == 0`.
3. `review_required`: work exists and `A == 0`.
4. `partially_reviewed`: all remaining cases with at least one decision but open
   work, including every unresolved decision or pending field issue.

Preparation yields `no_review_required` or `review_required`. A valid first action
can yield `partially_reviewed`, or directly `completed` for a single terminal item.
Further terminal actions can advance partial review to completed. Replays/errors
leave state/revision unchanged. No transition follows from time passing, provider
agreement or candidate order. No new tasks are added to an existing session.

Rejection resolves the candidate's disposition, not the business field: all-rejected
items may complete this candidate review while canonical missing/conflicting values
remain unresolved. An `unresolved` action never completes its item, even if every
item has an action. The current gate cannot revise it; further clarification and
supersession require a separate future workflow preserving the original decision.
Pending issues without candidates likewise prevent completion in this service.

`no_review_required` and `completed` refer ONLY to this OCR-candidate workflow for
the selected form. Neither state approves the canonical result, clears any field's
`needs_review`, certifies completeness, nor authorizes business export. Other-field
issues and original canonical states remain explicitly visible in the outcome.

## Failure and unavailable-source semantics

- **No candidates, successful OCR:** record empty evidence/`no_support`; no invented
  disagreement or proof of absence. Preserve existing review flags and pending
  issues. With none in scope, status is `no_review_required` for this workflow only.
- **OCR disabled/unavailable:** retain distinct source status, zero candidates and
  provider output unchanged. This optional source alone creates no mandatory human
  task; include its status in the outcome. Do not call it a successful empty OCR run.
- **Provider succeeded, OCR failed:** same isolation; record `failed` separately,
  preserve canonical success and any existing review requirements. No OCR/provider
  retry is owned by this service; operational diagnostics remain separate from
  value disagreement. Non-success status plus candidates is invalid input.
- **Provider failed:** never invoke preparation with `None`, an exception, empty
  logical forms or a fabricated result. Existing processing failure behavior stays
  unchanged; OCR cannot turn it into a successful business extraction.
- **Rejected decision:** preserve candidate, reviewed text if supplied, reason and
  provenance; expose no approved value. Only its candidate task is terminal.
- **Unresolved decision:** retain open review work and no approved value. Do not
  silently reinterpret it as rejection or overwrite it with another decision.
- **Duplicate/conflicting decision:** exact accepted replay is a no-op; altered ID
  payload, subsequent candidate action or stale new action fails without mutation.
- **Document/form/page/image mismatch:** reject the whole input/action with a safe
  reason code, not partial application or automatic reassignment. Same numeric
  content is never identity evidence. No values or patient text in error messages.
- **Canonical/evidence changed:** old decisions stay bound to their original
  snapshot/fingerprint. Prepare a distinct session; no automatic transfer of
  decisions, review completion or approvals between snapshots/runs.

## Future persistence contract - no storage implementation

ADR-001 already selects PostgreSQL for the modular monolith; ADR-009 requires
preserved provenance and the architecture log describes append-only ReviewEvents
and optimistic concurrency. Retain that direction, but add no database connection,
tables, migrations, file store or repository interface in this task. Do not select
SQLite or a competing persistence system. Concrete schema belongs to later work.

A later persistence boundary must atomically retain:

- Stable session ID, document/form/extraction identity, input fingerprint, versions,
  revision and derived review state. Enforce unique session IDs and reject reuse
  for different immutable inputs.
- Immutable canonical snapshot, or an immutable retrievable reference PLUS hash;
  provider/model/prompt/pipeline metadata when present in upstream attempt metadata.
  The current `ExtractionResult` does not contain all those attempt attributes:
  the future caller must supply links to them, not invent fields or change its API.
- Complete source-image hash/page/form bindings, private image/artifact references,
  and coordinate/rotation conventions needed to inspect evidence correctly.
- All candidate occurrences and assignment evidence, OCR engine/version/run,
  raw/normalized tokens, boxes, source outcome, assessment and preparation reasons.
- Append-only accepted decisions including both checks, exact reviewed text,
  original raw values, correction flag, reason/note, decision IDs, authenticated
  reviewer binding and both declared review and trusted receipt timestamps.
- Per-event previous/new state and revision, decision content fingerprint for
  idempotency, and future explicit supersession/reopening links. Never delete a
  rejected or unresolved decision to make a later one look like the first.

Future writes need compare-and-swap on revision, uniqueness constraints and an
atomic session/event update. Identical replay must not create duplicate audit
events even across process restarts. The in-memory proposal alone cannot guarantee
that. Authentication must bind the real caller to reviewer identity; an arbitrary
`reviewer_reference` from a request is not sufficient authority. Enforce document
access, retention/deletion policy and private source access at that future boundary.

## Authority and future merge boundary

Canonical provider output remains unchanged. Assessment is automated supporting
evidence; human decisions are separate evidence. Agreement is not approval; human
approval of an OCR value still performs no canonical replacement. There is no
automatic union, overwrite, normalization, blank filling or field-status update.

An eventual explicit merge/apply operation would need its own approved contract,
authority/re-review rules and audit before producing reviewed business values.
It is NOT designed or implemented here. BR-004/ADR-008 still require eventual
export of all logical forms with explicit unresolved statuses; this design does
not reinstate the superseded rule that only approved rows can be exported. It
only prevents supporting OCR tokens becoming business cell values implicitly.

## Task 10F implementation scope and validation

Files created/updated in Task 10F:

- NEW `backend/services/extraction/review_workflow.py`: frozen session/outcome
  structures, prepare/apply operations, snapshot isolation, scoped task selection,
  status derivation, identity/revision/idempotency validation. Compose existing
  assessment and review functions instead of duplicating their rules.
- NEW `tests/test_review_workflow.py`: synthetic tests for all source outcomes,
  required/evidence-only/no-candidate task selection, all transitions including
  rejected/unresolved cases, scope mismatches, multiple forms on a page, duplicate
  evidence, ID conflicts, stale revisions, replay and full nested snapshot isolation.
- UPDATE this document with actual implementation and validation results.

No change required to existing candidate/review modules, adapters, orchestrator,
domain models, prompts, scorer, ground truth, API routes, main/config, dependencies
or Excel behavior. No new provider/OCR invocation or global in-memory registry.

Acceptance: preserve exact canonical snapshot bytes/values/statuses before and after review;
create items only for required supported-field candidates; expose uncovered issues;
never use an unresolved/rejected candidate as approved; ensure deterministic
preparation/replays and atomic errors; prove multi-document/form isolation. Run
targeted workflow/candidate/review tests, full offline pytest, dependency/syntax
and whitespace checks in the implementation task.

Validation: workflow tests **72 passed**; combined workflow/candidate/review tests
**185 passed**; full offline pytest suite: **334 passed**, with 60 existing
dependency deprecation warnings. `pip check`, Python AST/import validation,
changed-file whitespace checks and `git diff --check` passed. Before/after SHA-256
snapshots of **72 protected
files** matched exactly, including existing candidate/review code and tests,
orchestrator/adapters/canonical conversion, public models, prompts, scorer,
ground truth, source/fixture PDFs, API/configuration and dependency declarations.
No formatter or type checker is configured in `pyproject.toml`; no tools or other
dependencies were added.

Git: branch `feat/backend-foundation`, HEAD
`6023c7596c562dd23d9a5a4a907333520c3774c7`. Task 10F adds two untracked Python
files and updates this already untracked design note. Existing untracked Task
10B/10D code/tests, the OCR design note and two source PDFs are preserved. No
tracked/staged changes, staging or commit; `.env` and private artifacts stay ignored.

The service has no document/artifact I/O or persistence. Reusing the existing
canonical schema validator reads the packaged JSON schema locally; it does not
load documents, OCR artifacts or external services. No clock, random ID generator,
global session registry, provider call, OCR run or retry is introduced. Reviewer
identity, source associations and timestamps remain caller declarations; durable
concurrency, authentication, reopening unresolved actions and cross-session identity
enforcement are not implemented. Workflow completion never approves a business
record and the current one-action review gate remains unchanged.

ONE recommended next task: design the append-only persistence and authenticated
session/decision boundary, including durable idempotency and explicit supersession
of unresolved actions, before any runtime/API wiring. Do not implement it as part
of Task 10F.
