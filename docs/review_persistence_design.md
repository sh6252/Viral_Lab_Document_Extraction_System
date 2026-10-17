# Task 10G - durable review persistence boundary (design only)

Status: Task 10H implements the PostgreSQL persistence slice described below,
without authentication or public/runtime wiring. Task 10G's original design and
findings remain as historical context; implementation notes and validation follow.
The pure workflow and canonical contracts remain unchanged.

## Repository findings and governing decisions

- [ADR-001](architecture_decisions.md) selects PostgreSQL and a modular monolith;
  ADR-009 requires raw/normalized values, evidence, review state and attempt metadata.
  ADR-006 separates review authority from providers. The architecture log's
  `ReviewEvent` is append-only, and mutating endpoints are intended to use optimistic
  concurrency. These are approved targets, not implemented database components.
- Inspection of `backend/`, `tests/`, `pyproject.toml`, `.env.example` and existing
  documentation found no database engine/session factory, repository/unit-of-work,
  ORM entities, migration directory/tooling or durable document/extraction store.
  There is no SQLAlchemy, SQLModel, Alembic or PostgreSQL driver declaration.
  `backend/core/config.py` has no database configuration. Current extraction routes
  are stateless. Ignored evaluation artifacts are not a runtime artifact store.
- The architecture log proposes SQLAlchemy or SQLModel for future entities; it
  does not select an implemented ORM convention. Proposed future file names below
  are new boundaries, not claims that infrastructure already exists.
- `review_workflow.py` already owns preparation, input fingerprints, task selection,
  review application and replay validation. `ocr_review.py` owns the one-decision
  gate. Persistence must compose those public operations, not reproduce their
  approval rules or weaken unresolved/rejected handling.

References: [workflow design](review_workflow_design.md),
[candidate/review design](ocr_candidate_design.md),
[business rules](business_rules.md). The current baseline is commit
`2bc5492060899a2242f38d74156b403e7de0e6d0` on `feat/backend-foundation`.

## Aggregate and authority

One aggregate is ONE session for `(session_id, document_id, form_id, extraction_id)`.
Session ID is globally unique in this application's review store; a decision ID is
unique WITHIN that session, matching the existing service. The same decision ID in
another session is a different idempotency key, not an implicit cross-session replay.

The aggregate owns immutable preparation data, its selected-form item catalog,
accepted events and a replaceable derived current projection. Its full canonical
snapshot and assessment may include other forms, as required by Task 10F, but those
forms never gain actionable items in this session. Each needs its own session.

Separate three categories:

1. **Immutable preparation:** canonical text/hash/encoding, identity, fingerprint,
   versions, prepared time, complete form/page/image bindings, OCR source outcome,
   full assessment/evidence occurrences, initial items, pending field issues and
   outside-scope issues. Preserve multiplicity even when items share a reference.
2. **Derived current state:** revision, overall workflow status, last receipt time
   and item-to-event/state projection. These are caches of preparation plus history,
   never independent approval authority.
3. **Append-only history:** each accepted human action with content, provenance,
   trusted actor/receipt and previous/new revision/state. Rejected and unresolved
   actions remain events. Exact retries add no event.

Do not persist `ReviewOutcome` as another authoritative record. Derive it from the
loaded session; an approved OCR value is still separate human evidence. No write
here changes `ExtractionResult`, clears `needs_review`, merges values or enables
export. BR-004/ADR-008 continue to allow eventual unresolved form rows with explicit
review metadata; this design imposes no approved-rows-only export rule.

## Proposed PostgreSQL tables

Use three tables, with lossless preparation payloads and small indexed identity
columns. Avoid normalizing every OCR word/box/provider value into separate tables.
All IDs below are nonempty `text`, compatible with current caller-supplied strings;
do not impose UUID-only IDs or numeric identifier conversion. Hashes/references
are lowercase 64-character SHA-256 strings checked structurally.

### `review_sessions`

Purpose: aggregate root, immutable input anchor and efficient current summary.

- **Primary key:** `session_id`.
- **Immutable identity:** `document_id`, `form_id`, `extraction_id`,
  `input_fingerprint`, `workflow_version`, `assessment_version`, `review_version`,
  `storage_codec_version`, `prepared_at` (`timestamptz`) and its original timestamp
  representation in the lossless preparation payload.
- **Immutable extraction:** `canonical_snapshot_json text`,
  `canonical_snapshot_sha256`, `canonical_snapshot_encoding_version`.
  Keep the exact UTF-8 text hashed by the service; JSONB serialization must not
  replace it or become the source of its hash.
- **Immutable preparation:** `preparation_json text`, its SHA-256 and encoding
  version. A versioned codec retains complete provenance, full
  `SupportingAssessment`, OCR source outcome, evidence multiplicity, initial items,
  pending/outside-scope issues and any separately supplied attempt/artifact links.
  Validate JSON and types in the codec; this is not arbitrary unvalidated JSON.
- `ocr_source_status text`: immutable indexed/queryable projection of the existing
  four source statuses; must agree with the preparation payload.
- **Mutable derived columns:** `revision bigint` initially zero,
  `overall_review_status text`, `updated_at timestamptz`,
  `current_projection_jsonb`. Projection contains candidate-reference to accepted
  decision/state links and counts, not rewritten raw business values. It must
  reproduce preparation plus ordered events exactly.
- **Trusted metadata:** `created_received_at timestamptz`, creation principal
  issuer/subject and provenance-ingestion reference, supplied by trusted runtime.
  These are distinct from the caller-declared preparation time and workflow input
  fingerprint; adding them must not recalculate Task 10F fingerprints.
- **Foreign keys:** no FK to nonexistent `documents`, `logical_forms`, `extractions`
  or identity tables. Preserve their IDs plus immutable snapshot/source references
  now; add validated FKs when those approved domain stores actually exist. Do not
  fabricate document rows or create a second document store in this task.
- **Unique constraints/indexes:** primary key; unique
  `(session_id, document_id, form_id)` as the target for scoped child FKs;
  lookup index `(document_id, form_id, extraction_id)`; a status/updated-time index
  only for the eventual review queue. Do NOT make the content fingerprint globally
  unique: distinct session IDs may represent separately prepared attempts.
- **Checks/concurrency:** nonnegative revision, known current status/source values,
  nonblank IDs and hash structure. Immutable columns receive no ordinary UPDATE
  privilege. Updates of mutable columns use the expected revision predicate;
  CHECK constraints alone cannot verify cross-row event/projection consistency.

### `review_items`

Purpose: immutable selected-form candidate catalog and relational event target.
Store one row per DISTINCT candidate reference, including evidence-only candidates.
Keep all original occurrences in the session assessment payload.

- **Primary key:** `(session_id, candidate_reference)`; reference is produced by the
  existing `candidate_reference` function, never by matching numeric values.
- **Foreign key:** `(session_id, document_id, form_id)` references the scoped unique
  key on `review_sessions`, with restrictive deletion. Only the selected form's
  candidates enter this catalog; other-form evidence stays in full preparation.
- **Immutable columns:** `document_id`, `form_id`, supported `field_name`,
  `actionable boolean`, complete `candidate_json text`, detached
  `provider_values_jsonb`, and optional private source-artifact reference.
  The candidate text retains raw/normalized value, field-assignment evidence,
  image/page, OCR engine/version/run and optional box/coordinate system exactly.
- `actionable` is set solely from the existing prepared session: candidates in
  `review_items` of the pure session are true, other selected-form assessment
  candidates false. No task is created just because sources corroborate. Pending
  issues with no candidate are not invented rows.
- **Unique constraint:** `(session_id, candidate_reference, actionable)` supports
  an event FK that can target actionable items only. Its extra index is justified
  by that relational guard, not an additional candidate identity.
- **Indexes/checks:** primary/scoped unique indexes and the session FK index prefix
  are sufficient initially; CHECK supported field names, positive ONE-based page
  metadata if projected, valid reference syntax. Candidate text/reference equality,
  scope, provider snapshots and assignment details are verified by the codec and
  workflow, not inferred by SQL numeric/length heuristics.
- **Mutable columns:** none. Per-item current review state is derived in the root
  projection and events; do not maintain a second independently writable history.

### `review_events`

Purpose: authoritative append-only accepted decision and audit trail.

- **Primary key:** `(session_id, decision_id)` (durable session-scoped idempotency).
- **Foreign keys:** session root, plus
  `(session_id, candidate_reference, actionable)` to `review_items`' matching unique
  key. An event's `actionable` column is constrained to true, preventing a decision
  for an evidence-only item. Use restrictive deletion, not cascading audit loss.
- **Immutable action columns:** `candidate_reference`, `event_type` initially only
  `decision_accepted`, versioned lossless `decision_json text`,
  `decision_content_fingerprint` and fingerprint codec version; decision status,
  independent assignment/value verification statuses, exact reviewed/corrected text
  (nullable), `human_correction_occurred`, reason/note, declared reviewer reference,
  declared review timestamp and original representation. Queryable projections
  must match the authoritative decoded decision and candidate.
- **Immutable trusted audit:** principal issuer/subject (stable, not display name),
  trusted server receipt timestamp, previous/new revisions and workflow states.
  The candidate FK plus immutable candidate retains original raw text, field,
  document/form/page/image and engine/run provenance without altering the source.
- **Unique constraints:** `(session_id, new_revision)` gives unambiguous event
  order; `(session_id, candidate_reference)` enforces the current ONE-action gate,
  including rejected/unresolved decisions. This is intentionally not a last-wins
  or upsert model. Future supersession requires an explicit new design/migration
  and workflow version, not bypass of this constraint.
- **Checks:** `new_revision = previous_revision + 1`, nonnegative previous revision,
  known decision/check/state values and valid hashes. Service/codec remain the
  authority for approval conditions and exact strings; do not add unsupported
  identifier format/length rules in SQL.
- **Indexes:** the PK supports replay, unique new-revision index supports ordered
  history, candidate unique index supports item lookup. Add principal/time indexes
  only if an actual authorized audit query needs them. No patient-text index.
- **Mutable columns:** none. Application role receives INSERT/SELECT, not UPDATE
  or DELETE. Restrict session/item writes similarly; use a separate migration/admin
  role. This prevents ordinary application rewrites, not privileged tampering or
  cryptographic proof of an audit trail.

## Codec, exactness and restart recovery

JSONB is suitable for derived summaries and raw/normalized provider strings, but
not for the authoritative byte-sensitive snapshot/candidate serialization.
For example candidate hashes include bounding-box numeric representations; do not
depend on a database's JSON number reformatting to reproduce Python serialization.
Versioned lossless text preserves the original canonical JSON, candidate JSON and
complete preparation/event data. Strings retain zeros, punctuation, whitespace
and Unicode; never use numeric database types for extracted identifier values.

The storage codec must round-trip existing frozen objects, tuples, optional values,
boxes and timezone-aware datetimes. It verifies all stored hashes/references and
known versions without changing workflow fingerprints. Unknown versions or hash/
projection mismatches fail closed with safe integrity errors; do not silently
upgrade data or repair an event. Future codec migrations must preserve evidence.

To load a full session after restart, reconstruct preparation via public
`ReviewWorkflowService.prepare_review`, then apply ordered accepted decisions via
its public `apply_decision`, supplying stored receipt times and sequential revisions.
Compare the result with the stored fingerprint, input data and current projection.
The original workflow already validates history; do not call or modify private
helpers or trust JSONB to bypass it. History is currently bounded by the number
of actionable items. Do not introduce generalized event-sourcing infrastructure.

A summary read uses root revision/state without replay. A full aggregate read
must use one consistent database snapshot (one statement or a read-only repeatable
snapshot transaction); independent read-committed reads could mix a root at revision
R and events at R+1. Never return that combination as a valid session.

## Durable create and decision idempotency

`create(prepared_session)` inserts revision-zero preparation and its immutable
catalog in ONE transaction. A session PK collision must compare fingerprint AND
immutable preparation/encoding, not replace the root. Identical creation retry
returns the existing CURRENT session, retaining decisions already accepted. Different
fingerprint, changed snapshot/evidence or inconsistent preparation is a conflict.
Callers must reuse their original explicit prepared time on creation retries;
a different preparation payload does not silently change that time.

For decisions the durable key is `(session_id, decision_id)`. Fingerprint the full
existing `OCRReviewDecision` content, including raw reviewed text, provenance,
reason/note and declared identity/time. Exclude `expected_revision`, retry receipt
time, trusted actor metadata and current projection: retries must not change the
content key or rewrite original attribution. Define and version timestamp encoding
so equality matches the existing Python decision equality (equal aware instants);
preserve the original timestamp spelling/offset separately. Compare the decoded
decision as well as the hash: fingerprinting is not substitute authorization or
permission to collapse distinct strings. Codec tests must prove equivalence to
the current gate, including nulls, offsets, zeros and punctuation.

An identical accepted decision returns the current stored session as replay,
even with an old expected revision; it adds no event, timestamp or revision.
Different decision content under the same ID conflicts. A new ID for an already
decided candidate conflicts. No automatic re-review, replacement, or transfer
between sessions is allowed. The same content under a new ID is not a replay.

## Atomic apply transaction and concurrent requests

Use one short PostgreSQL transaction per accepted action. Proposed initial
isolation is ordinary read committed with a row lock on the session root, combined
with the existing optimistic revision precondition. This serializes writes only
within that session; unrelated sessions do not block each other's review work.

1. Trusted runtime authenticates/authorizes document/form access before any state
   disclosure. Load the root with `FOR UPDATE`; missing or inaccessible IDs receive
   a safe error. All aggregate writers take this same parent lock.
2. Load that root's immutable inputs/items/events and verify/reconstruct the current
   session. Under the parent lock, other accepted writers cannot append to it.
3. Check `(session_id, decision_id)` and validate exact content/scope. On exact
   replay, return current state without writing, BEFORE checking expected revision.
   Authorization still applies; a replay is not an unauthenticated read shortcut.
4. For a new action, reject an already-decided candidate and require the supplied
   expected revision. Obtain a trusted server receipt time once for this action;
   never take it from the client. Supply it to the unchanged pure workflow service.
5. Append the accepted event from the returned outcome, with previous/new state
   and revisions and trusted attribution. Insert uniqueness is a final race guard,
   never `ON CONFLICT DO UPDATE` on an audit event.
6. Update only root revision/status/projection/time using
   `WHERE session_id = :id AND revision = :expected_revision`; require exactly one
   affected row. Persist the projection produced by the workflow, not a separately
   implemented state machine. Commit event and root together or roll back both.

The row lock protects the idempotency check from a concurrent identical request;
after the first commits, the next sees it as replay even with a stale revision.
Different new actions submitted against the same revision cannot both succeed:
one commits and the other observes a stale revision. If CAS affects zero rows or
a constraint fails, roll back before returning a safe conflict/integrity error.

Connection loss before commit leaves neither change; loss of the response after
commit is recovered by retrying the SAME decision ID/content. Lock timeout,
deadlock or serialization failure rolls back the entire transaction; do not invent
a successful decision or retry with a new ID. Any later retry policy must preserve
the same command. No providers/OCR are called or retried inside the transaction.

SQL constraints guard identity and duplicates, but do not alone prove a contiguous
audit chain, parent state agreement or authorized actor. Repository transaction,
pure workflow validation, restricted write access and integration tests provide
those invariants together. This proposal is not a claim of implemented durability.

## Authentication and artifact boundary

Future authenticated runtime supplies a trusted internal context containing the
principal issuer/subject, authorization scope and server clock. The persistence
application wrapper binds it to the event, independently of caller-entered
`reviewer_reference`. Preserve that declared reference as human input; never use
it as proof of identity. Reject missing trusted context, unauthorized scope or
attempts to forge trusted metadata. Replays require authorization and compatible
actor attribution; they cannot reassign the original event to a new actor.
No user table, role model, identity provider or authentication implementation is
selected here; production authentication remains an open architecture decision.

Persist private, stable artifact IDs/storage references plus content hashes for
source document, rendered page and OCR run. Link page number (ONE-based candidate
convention), original canonical zero-based indices where applicable, rotation,
pixel dimensions, box coordinate convention and transformation/preprocessing
version. Preserve OCR engine/version/run and actual artifact-output references;
do not fabricate missing metadata or convert boxes silently. Multiple forms on
one page retain separate assignments and candidate references.

There is no implemented runtime artifact storage to reuse today. These references
are a future boundary to the approved private storage adapter, not public URLs,
machine-specific file paths or authority to promote ignored evaluation directories
into production storage. Review tables store no raw image/PDF bytes. Hashes prove
binding only when trusted ingestion checks the actual bytes; the database cannot
prove image correctness from an asserted hash. Later image access must reauthorize
the document and resolve the exact referenced artifact/version.

## Sensitive data and lifecycle

Canonical snapshots, OCR raw/normalized/corrected values, evidence snippets,
provider values and reviewer notes can contain patient/business information.
Document/form/extraction IDs, hashes, image/run references and principal IDs are
also linkable sensitive metadata. Do not emit them, SQL bind values, snapshots,
candidate text or decision content into general logs/traces/error bodies. Limit
operational logs to safe event/error categories and appropriately controlled
correlation data; repository exceptions must redact driver parameter details.

Later persistence needs restricted DB/application access, protected backup access,
private artifact access and deployment-appropriate encryption/transport controls.
Those controls must be implemented/tested in their deployment scope, not assumed
from having PostgreSQL. Do not expose other forms merely because a session retains
a complete multi-form snapshot: future read APIs must enforce scope/projection.

`architecture_decisions.md` UD-007 leaves retention undefined. Do not invent a
duration, automatic purge or permanent-retention promise. Ordinary review never
deletes history; approved lifecycle/deletion procedures may later remove an
aggregate, its artifacts and backups according to laboratory/security decisions.
Restrictive FKs and no ordinary DELETE permission make such deletion deliberate,
not accidental cascade. A lifecycle design must reconcile audit requirements,
source-image availability, legal holds if applicable and lawful deletion; a
deleted image must not be silently replaced by another image under the same hash
reference. No purge or artifact cleanup is implemented here.

## Internal repository and application interface

Keep all DB/transaction concerns outside `review_workflow.py`. Proposed internal
interfaces (design only, no Protocol or repository files added now):

```text
ReviewSessionRepository
  create(prepared_session, trusted_creation_context) -> ReviewSession
  get(session_id) -> ReviewSession
  get_history(session_id) -> ordered immutable persisted events
  apply_decision_atomic(session_id, decision, expected_revision,
                        trusted_actor, transition) -> ReviewOutcome
```

`get` already means get-by-session-ID; do not add an identical second method.
Authorization belongs to a future `ReviewPersistenceService` wrapper; repository
methods accept only internal trusted context and are not endpoints. The wrapper
supplies a narrow pure transition callback using the existing workflow service.
Repository owns load/lock/idempotency/receipt/commit and invokes that callback with
the stored current session and trusted receipt time. It does not duplicate approval
or state rules. No generic unit-of-work framework is required.

The wrapper returns only after commit, never a speculative successful outcome.
`get_history` includes trusted metadata absent from pure `AcceptedReviewDecision`;
that extra storage envelope does not alter canonical/public contracts. Reads must
be authorized even when no write occurs. No authentication API wiring is included.

## Proposed Task 10H sequence and exact file touch points

ONE recommended implementation task: implement the PostgreSQL persistence slice
with codec, migration, atomic repository and synthetic PostgreSQL integration tests,
plus its internal workflow wrapper; no API/startup wiring, OCR, provider or merge.

Suggested minimal stack for that future task is SQLAlchemy with a PostgreSQL
driver and Alembic, consistent with the architecture log's ORM direction. These
are proposals, not dependencies installed/selected in Task 10G. Task 10H must
verify Python 3.14 compatibility and select compatible package versions before
installation; do not upgrade unrelated packages or introduce SQLite fallback.

Implementation order and proposed exact paths:

1. Schema/codec: NEW `backend/persistence/__init__.py`,
   `backend/persistence/db.py` (explicitly constructed connection/transaction
   boundary), `backend/persistence/review_models.py`,
   `backend/persistence/review_codec.py`. UPDATE `pyproject.toml` only for the
   justified future DB/migration/test dependencies and integration marker.
2. Migration: NEW `alembic.ini`, `migrations/env.py`,
   `migrations/script.py.mako`, `migrations/versions/0001_review_persistence.py`.
   Migration environment accepts an explicit test/deployment DSN; no credentials
   embedded in configuration. Fresh migration creates only this slice, without
   FKs to nonexistent document/user tables or unrelated schema work.
3. Repository: NEW `backend/persistence/review_repository.py`, implementing the
   atomic boundary, projections, restricted error mapping and consistent reads.
4. Tests: NEW `tests/test_review_codec.py`, `tests/integration/conftest.py` and
   `tests/integration/test_review_repository.py`. Use disposable dedicated PostgreSQL
   databases/schemas with synthetic values. Test parallel identical/conflicting
   requests, stale revisions, lost-response replay after a fresh connection,
   rollback between event/root writes, scope/actionable guards, append-only rights,
   lossless codecs/hashes and repeatable full-session reads. SQLite/in-memory
   substitutes cannot validate these PostgreSQL guarantees. Never reset a real
   application database; tests require an explicitly isolated test DSN.
5. Internal wrapper: NEW `backend/services/review_persistence.py` and
   `tests/test_review_persistence.py`, requiring trusted runtime context and
   injecting the unchanged pure workflow as the transition. This is not an
   authentication implementation or a public API.
6. UPDATE this design note with actual results and `README.md` with isolated
   migration/test setup. Public API/startup/configuration integration is a later
   separately scoped task, not part of this persistence slice.

Additive migration and rollback procedures must not drop accepted decisions from
an in-use database automatically. Establish backups/restoration and version
compatibility before deployment. Future reopening/supersession will need new event
types, explicit links and a revised one-action constraint/workflow version; it is
not implemented by Task 10H's initial repository or by modifying past events.

## Task 10G validation and limits

In Task 10G only this documentation file was created. Appropriate checks were UTF-8/newline/
whitespace validation, local link/path review, `git diff --check` and verifying
that tracked implementation/configuration/dependencies remain unchanged. No pytest
or DB test result is claimed for a design. No package install, database connection,
migration execution, provider/OCR call, authentication, staging or commit.

## Task 10H implementation

Implemented explicit PostgreSQL engine construction, SQLAlchemy Core table models,
static Alembic migration, lossless versioned codecs, the atomic repository and
internal persistence wrapper. There are no ORM entities requiring a session
framework: Core is sufficient for explicit aggregate transactions. New files are
under `backend/persistence/`, `backend/services/review_persistence.py`, `migrations/`
and the focused unit/integration tests; `README.md` and `pyproject.toml` are updated.
No existing extraction/candidate/review/workflow implementation is changed.

Selected packages verified by resolution/install on Python 3.14.2 Windows:
SQLAlchemy **2.0.55**, psycopg / psycopg-binary **3.3.6** and Alembic **1.20.0**.
The resolver selected CPython 3.14 Windows wheels for the compiled packages.
New transitives: greenlet **3.5.6**, Mako **1.4.3**, MarkupSafe **3.0.4**,
tzdata **2026.5**. Existing typing-extensions was reused; unrelated packages were
not upgraded. Compatibility constraints are declared in `pyproject.toml`; these
are the measured installed versions, not a complete lockfile.

Codec `review-storage-v1` uses tagged, deterministic JSON text with an explicit
allowlist of frozen dataclass types, tuple tags, exact numeric representations,
nulls and aware datetime ISO/zone/fold metadata. Unknown versions, duplicate JSON
keys, noncanonical serialization and invalid hashes/preparations fail closed.
Preparation decoding reconstructs and compares using public workflow operations;
accepted history is replayed through public `apply_decision` on load. Root/item/
event projections and hashes are verified before returning a session. No private
workflow helper or JSONB summary bypasses validation. Exact reviewed strings and
the original canonical snapshot text remain unchanged.

Decision content fingerprints canonicalize aware instants to UTC for ordinary
equivalent-offset retries, retaining the original representation in lossless text.
The existing Python decision equality remains authoritative for replay (including
Python datetime edge cases); incoming fingerprint differences alone never override
that equality. Stored fingerprints still must reproduce their own persisted
payload. This avoids silently changing the existing gate's replay semantics.

Migration `0001_review_persistence` contains fixed PostgreSQL DDL and explicit
constraints/FKs, plus triggers rejecting event/item mutation, session deletion,
preparation mutation and non-incrementing root revisions. Downgrade refuses
deletion. No FKs target nonexistent document/user tables. The review queue index
is not added because there is no queue query yet. Database application-role grants
remain deployment work; triggers are tested guards, not protection from a privileged
operator disabling them. Tests simulate privileged corruption only inside disposable
schemas to verify load-time integrity errors.

Repository `create` uses insert-do-nothing ONLY for root creation conflicts, then
compares immutable preparation and returns the existing current session on an
identical retry. Audit events are never upserted. `get`/`get_history` use repeatable
read; apply locks the parent in one transaction, checks replay before revision,
calls the unchanged workflow, inserts an event and CAS-updates the root. Either
both writes commit or neither does. Exact replay preserves original receipt/actor;
attempted actor reassignment conflicts. Receipt time comes from the repository's
trusted clock, not the decision payload. Exceptions suppress SQL parameters/chains.

`ReviewPersistenceService` requires internal `TrustedReviewContext`, validates its
review permission and document/form scope and injects the pure transition. This
context is a trusted caller assertion, not authentication. Reads are internal;
there are no new endpoints or runtime entry points. Artifact references remain
optional until private ingestion/storage exists; no document bytes or image blobs
are added. Cross-session supersession, retention/deletion and public field-level
review are still unimplemented.

Integration tests use an explicitly configured loopback PostgreSQL database with
`review_test_` name and disposable acknowledgement, then unique per-test schemas.
The existing local Docker PostgreSQL image was used without touching application
databases or existing containers. Generated test credentials are private/ignored
and never part of configuration examples or Git. The test harness, not an
application command, owns removal of its generated schemas.

Final validation on local **PostgreSQL 16.11**:

- Codec/wrapper unit tests: **29 passed**; disposable-DSN safety tests: **7 passed**.
- PostgreSQL migration/repository tests: **33 passed**, without skips. Each test
  upgraded a fresh isolated schema; concurrency, reconnect, rollback, append-only
  guards, trusted attribution, exact strings and privileged corruption were tested.
- Combined persistence/workflow/candidate/review checks: **254 passed**.
- Full regression with the explicitly configured isolated PostgreSQL DSN:
  **403 passed**, 60 existing dependency deprecation warnings.
- The first full invocation encountered 10 Windows temp-directory permission setup
  errors (393 tests passed). Repeating with a newly generated `--basetemp` under
  ignored artifacts passed all 403; existing tests/business code were not changed.
- `pip check`, AST/import checks, changed-file whitespace/credential-marker checks
  and `git diff --check` passed. No formatter/type checker is configured; none was
  installed or claimed run.
- Before/after SHA-256 snapshots of **73 protected files** matched exactly,
  including existing extraction/workflow/candidate/review code/tests, adapters,
  public contracts, prompts, scorer, truth, source/fixture PDFs and API/configuration.
  Only README/dependency declarations were excluded as authorized tracked changes.

Files created in Task 10H: `alembic.ini`; `backend/persistence/__init__.py`, `db.py`,
`review_codec.py`, `review_models.py`, `review_repository.py`;
`backend/services/review_persistence.py`; `migrations/env.py`, `script.py.mako`,
`versions/0001_review_persistence.py`; `tests/integration/__init__.py`, `conftest.py`,
`test_review_repository.py`; `tests/test_review_codec.py`,
`tests/test_review_database_safety.py`, `tests/test_review_persistence.py`.
Updated: `README.md`, `pyproject.toml` and this pre-existing untracked design note.

Branch stays `feat/backend-foundation`; HEAD stays
`2bc5492060899a2242f38d74156b403e7de0e6d0`. No staging, commit or push. Source PDFs
remain untracked/unchanged; `.env` and private experiment artifacts remain ignored.
The disposable test container and generated credentials are removed after testing;
future integration runs need a newly configured dedicated test database. No
application database or pre-existing container was changed.

ONE recommended next task: design the authenticated runtime/API boundary for loading
sessions and submitting decisions, including trusted principal binding and private
source access, before adding any public wiring. No such integration is implemented
or started in Task 10H.
