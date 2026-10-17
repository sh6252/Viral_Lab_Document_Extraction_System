# Task 10A — optional OCR candidates (design only)

Status: Task 10B implements pure offline candidate assessment; Task 10D adds a
separate offline human-review gate. Neither component is wired into runtime.
Runtime OCR integration remains proposed and unimplemented. Gemini remains the primary semantic extractor,
selected through the existing provider factory. OCR is optional, disabled by
default, and never an authority for a business value. Initial candidate scope is
only `central_numbers` and `commitment_numbers`. No names, patient IDs, receipt
dates or sender candidates are supported by this proposal.

## Current implementation and constraints

- `backend/domain/models.py`: `ExtractionResult` contains logical forms and
  `FieldResult` values, statuses, optional evidence/signals and review state.
  Identifiers are strings. These public models must remain unchanged.
- `backend/services/extraction/base.py`: `ExtractionAdapter.extract` returns the
  canonical result. An OCR candidate source should not implement this interface:
  it does not produce a semantic business extraction.
- `backend/services/extraction/orchestrator.py`: `ExtractionOrchestrator.extract`
  calls the injected adapter and validates the returned canonical schema. It has
  no provider-specific conditions, OCR, persistence or reconciliation today.
- `gemini_adapter.py`: policy, SDK transport, errors and parsing belong here.
  Candidate reconciliation does not belong inside the adapter.
- `canonical.py`: `sanitize_fields` checks present/value consistency, prevents
  provider approvals, and sets present fields to `unreviewed`, others to
  `needs_review`. It is not business validation or a normalization service.
  Reconciliation must happen AFTER this mapping, otherwise its review advice
  could be reset by provider sanitization.
- `backend/api/routes/extractions.py`: current endpoint is stateless and returns
  only `ExtractionResult` or a structured processing error. It does not persist
  supporting candidates or implement a human review API.
- There is no implemented general identifier normalizer, business acceptance
  policy, review service, UI or Excel exporter. The ADRs describe their target
  behavior; they are not existing integration points to assume are available.

`docs/business_rules.md` requires immutable raw values, leading zeros and original
punctuation; multiple values remain on one logical-form row; ambiguous associations
remain unresolved. It defines no mandatory identifier length, checksum or globally
numeric-only identifier grammar. Tests include alphanumeric commitments such as
`HT-00077` and `INVALID-DEMO-0007`. No stronger business rule is inferred here.

## Smallest integration boundary

Introduce a small provider-independent, pure candidate-assessment service in the
extraction service package. Inputs are an immutable canonical result snapshot,
document/form provenance and a batch of separately admitted OCR candidates.
Output is a supporting assessment (sidecar), not a replacement `ExtractionResult`.

Conceptual flow:

```text
validated source document -> existing orchestrator -> existing adapter
                                                   -> canonical schema validation
optional local OCR -> token admission/field association -> pure reconciliation
canonical result + supporting assessment -> future review application service
```

The insertion point is immediately after the existing orchestrator returns its
validated result and before eventual review/export decisions. Initially implement
and exercise only the pure service offline. No OCR call, orchestration hook or
storage framework is needed for that step.

When runtime integration is separately approved, an application-level coordinator
can call the existing orchestrator and optional candidate source. Keep the existing
`extract(...) -> ExtractionResult` API, adapter protocol and provider registry
unchanged. A candidate source is a distinct internal dependency, not another
semantic extractor and not a new provider registry. Do not add a framework,
background infrastructure or a combined extractor to make this possible.

## Internal structures implemented in Task 10B

Frozen Python dataclasses live in `backend/services/extraction/ocr_candidates.py`.
They are internal service values, not public Pydantic schema additions.

`OCRCandidate`:

- `field_name`: literal `central_numbers` or `commitment_numbers`.
- `raw_value`: immutable OCR token, retained exactly, including leading zeros.
- `normalized_value`: optional string; initially `None`, not a numeric conversion.
- Provenance fields: `source_engine`, `source_engine_version`, `source_run_id`,
  `source_image_sha256`. The version/run references identify external private
  artifact metadata (language-data/preprocessing versions); no artifact I/O occurs.
- `document_id`, `form_id`, `page_number`: exact provenance. Page numbers are
  explicitly ONE-based, unlike canonical evidence's zero-based page indices. Ambiguous form
  membership prevents creation of a field-specific candidate.
- `bbox`: optional immutable `(x, y, width, height)` tuple with finite values and
  positive dimensions; `bbox_coordinate_system` is required when a box is supplied.
  Image dimensions/transforms remain in referenced source artifacts. No coordinate
  conversion is performed or verified by this offline component. PyMuPDF text-layer
  boxes must not be described as direct Tesseract polygons.
- `field_assignment_evidence`: frozen `FieldAssignmentEvidence` with matching
  `field_name`, kind `label_relation` or `reviewed_region`, and a nonblank private
  artifact reference. Unlabeled barcodes, value/truth matches and nearby stickers
  alone are rejected. This validates the supplied structure, not the factual truth
  of a caller's label/region assertion; independent association remains essential.
- `validation_status`: `syntax_screened_unverified`. This means eligible for
  consideration, not verified as correct or approved for use.

Do not add confidence: the tested OCR interface does not expose it. Engine version
and input hash provide reproducibility, not correctness.

`FieldAssessment`:

- field and document/form identity;
- admitted candidates/evidence references;
- matched values, provider-only values and OCR-only values;
- state: `no_support`, `corroborated`, `partial_support`, `disagreement`,
  `ocr_only` or `unresolved_normalization`;
- `review_recommended` and machine-readable reason codes.

`ProviderValue` is a frozen raw/normalized snapshot, never a reference to a mutable
canonical `FieldValue`. `FieldAssessment.provider_values` preserves those snapshots,
provider value/review statuses and all OCR occurrences, using immutable tuples.

`SupportingAssessment` contains two field assessments PER logical form, provider extraction
ID, assessment/rule version and source outcome (`disabled`, `succeeded`, `failed`
or `unavailable`). OCR disabled/failed is not equivalent to an empty successful
OCR result. A provider failure stays a processing failure with no fabricated
canonical result; any candidate artifact remains untrusted supporting material.

The pure entry point is:

```python
assess_ocr_candidates(
    result, candidates, *, document_id, form_page_images, source_status="succeeded"
)
```

`form_page_images` maps each canonical form ID to its known one-based page numbers
and lowercase image SHA-256 digests. The canonical result contains no document ID
or form-page-image binding, so the caller must supply this trusted context. All form
IDs must match the result and each candidate's document/form/page/hash must match;
mismatches raise generic `ValueError` reason codes without leaking values. This is
provenance comparison, not filesystem hash verification or automatic form matching.
Source outcomes other than `succeeded` require an empty candidate batch. `None`, an
error object or an empty-form result is not a successful input. Duplicate form IDs
and inconsistent present/value combinations fail explicitly. Provider exceptions
remain the future coordinator's responsibility; no alternate extraction contract
or fallback canonical output is introduced here.

## Admission and conservative validation

Keep OCR artifacts separate from field-specific candidates. Rejecting a token
from candidate creation never destroys its raw OCR evidence. Record rejection
reason codes; do not log document text or candidate values in general logs.

Admission requires BOTH a token screen and independent field association:

1. Require the same document, logical form, source hash and valid page provenance.
   Never associate forms by similar numeric values or by ground truth. For multiple
   forms on a page, ambiguous membership leaves the token unassigned.
2. As a deliberately narrow initial OCR capability, consider only an entire,
   nonempty ASCII digit token (`[0-9]+`). This is a candidate-source capability
   filter, NOT a claim that alphanumeric/punctuated business identifiers are
   invalid. Such tokens remain in the artifact as unsupported/unassigned evidence.
3. Do not extract digit substrings from dates, merge tokens, strip punctuation,
   repair `O`/`0`, truncate, pad or remove leading zeros. Thus `2603204)` is not
   silently converted to `2603204`. No fabricated min/max length or checksum.
4. Require explicit relation to the intended number label/column or a reviewed
   field region. An unlabeled barcode, standalone number or merely nearby sticker
   is insufficient. Reject dates, quantities, patient/phone numbers and other
   known label contexts from these two candidate roles. Do not duplicate an
   ambiguous token into both fields. No date/ID-length heuristic substitutes for
   field context.
5. Digit spelling cannot detect a plausible misread such as `47028`. Even an
   admitted token remains unverified. No syntactic rule proves business validity.

For the next pure-service task, pass association evidence explicitly in synthetic
inputs. Automatic label/region association is not assumed implemented. In later
runtime work it must have a small independently tested rule set or human region
assignment; when context is insufficient, admit no field candidate. Neither
matching Gemini's value nor matching truth can supply missing association evidence.

## Normalization and deterministic reconciliation

Initially the only comparison transform is identity: compare exact raw strings.
No current approved normalization rule justifies stripping zeros or punctuation.
If a provider supplies a different `normalized` string, do not treat it as a proven
normalizer. Preserve both; an unsupported discrepancy is
`unresolved_normalization` and recommends review. Future lab-approved normalization
must be one shared explicit rule applied to both sources, versioned and evaluated.
The evaluation scorer itself is not changed or used as the reconciliation policy.

Reconcile per field and per logical form using deduplicated sets of comparison
values; preserve every original candidate/evidence occurrence. Never combine the
central-number and commitment-number sets or infer pairings between them.

- **Same value / equal sets:** retain Gemini values/statuses; mark matching OCR
  occurrences as corroboration only. Never approve a field or remove an existing
  review requirement. Two sources can agree on the same error.
- **OCR supports only a subset of Gemini values:** mark those matches as partial
  support. OCR silence on other values is not contradictory evidence and does
  not prove that Gemini's extra values are wrong or that the form is complete.
- **Admitted OCR value not in Gemini's set while Gemini has values:** retain all
  Gemini values, record OCR alternatives and recommend review (`disagreement`).
  A superset/partial overlap never causes union, replacement or row splitting.
- **Provider field missing/unreadable/ambiguous/not processed, OCR has a candidate:**
  keep the original field status and values; state `ocr_only` and recommend review.
  Never change it to `present` merely because OCR found digits.
- **No admitted OCR candidates:** leave provider behavior unchanged. Record
  `no_support` or source failure/disabled state; do not invent a disagreement.
- **Provider call fails:** preserve the existing exception/HTTP processing-failure
  semantics. Never turn OCR tokens into a successful business extraction or a
  fabricated commitment row. Candidate evidence may be retained for later manual
  handling only when the future application has storage for it.

Do not choose a winner using OCR counts, candidate order, longest token, provider
self-confidence, or apparent plausibility. Results must not depend on list order.

Task 10B sorts forms by ID, fields in supported-field order, comparison sets
lexicographically and candidate occurrences by their full value/provenance keys.
Duplicate values count once for set comparison, while every occurrence is retained.
Existing `needs_review`/`needs_clarification` adds `EXISTING_REVIEW_REQUIRED` even
when sources corroborate. New review reasons are `OCR_PROVIDER_DISAGREEMENT`,
`OCR_CANDIDATE_WITHOUT_PROVIDER_VALUE` and `UNSUPPORTED_NORMALIZATION`.
Different supplied normalized values take precedence as `unresolved_normalization`
when OCR support exists; raw matched/difference sets are still available. With no
OCR candidates, no new normalization advice is added. Nothing clears or changes
the canonical review state, including a human approval already present.

The pure service does not mutate even `review_status`. It returns advice in the
sidecar. A later review-policy integration may project a disagreement to the
existing `FieldResult.review_status="needs_review"` in a response/review snapshot,
AFTER provider mapping, while retaining the original provider result. It must never
alter `values` or `value_status`, auto-approve corroboration or silently replace an
existing human decision. Re-review of approved records needs an explicit audit event.

## Human review, API and eventual export

An eventual UI should display the extracted list, separate OCR candidate list,
source region, partial/disagreement state and review recommendation. A reviewer
may confirm the provider, reject the OCR candidate or explicitly select/correct a
reviewed value. Record actor, timestamp, old/new values and reason; keep raw provider
and OCR evidence immutable. Selection must not be disguised as provider output.

Do not embed OCR alternatives in `FieldResult.values`. Existing `signals` and
`evidence` can eventually carry compatible supporting metadata, but are not a
substitute for provenance, audit or storage. Avoid adding an undocumented candidate
API hidden inside an arbitrary `signals` dictionary.

The current stateless endpoint cannot durably expose this sidecar to a UI. A later
review resource/persistence task can expose candidates separately, consistent with
the ADR target, without changing the canonical extraction response. Until then,
use local supporting artifacts/offline analysis only; do not claim an implemented
review workflow or a production OCR feature.

There is no Excel exporter today. Preserve the approved target: one row per logical
form, multi-value fields and explicit unresolved review metadata; processing
failures separate. OCR candidates never enter business cells or create additional
rows before an explicit reviewed selection. Export column/layout decisions remain
outside this task; no current or target Excel behavior is changed here.

## Risks and safeguards

- Plausible false digits survive syntax screening: source/context and review are
  mandatory; source agreement is not independent proof of correctness.
- Numeric-only OCR admission intentionally misses alphanumeric identifiers;
  retain artifacts and describe unsupported scope rather than invent business rules.
- OCR coverage is incomplete: no negative evidence from silence, no inferred
  absence, no completeness claim for multi-value fields.
- Wrong document/form/field or rotated-box mapping can corrupt associations:
  require source hashes, page/form identity and explicit coordinate transforms.
- Missing OCR confidence cannot be replaced with a fabricated number or word count.
- Candidate text and boxes are sensitive: private storage, access control and
  retention policy are prerequisites for eventual runtime persistence/UI access.
- Optional OCR adds failure/latency/resource risks: later runtime integration needs
  local resource limits and a distinct source-failure state, preserving Gemini
  policy and error handling. Do not silently mask OCR failures as no candidates.
- UD-003/UD-005 in business rules leave authority precedence and automatic acceptance
  policy unresolved. This proposal resolves neither through an OCR shortcut.

## Exact future touch points and bounded next task

Task 10B implements the previously recommended pure, offline candidate-admission
and reconciliation component with synthetic tests. No Tesseract/runtime wiring.

Files for that task:

- NEW `backend/services/extraction/ocr_candidates.py`: frozen internal structures,
  explicit association inputs, admission and pure reconciliation functions.
- NEW `tests/test_ocr_candidates.py`: zero/leading-zero/raw preservation,
  unsupported alphanumeric tokens (not declared business-invalid), punctuation/date
  rejection, unassigned context, multiple forms/values, deduplication, exact/subset/
  superset/disjoint sets, unsupported normalization, source failure, candidate-only
  cases, order independence and immutable provider results/no auto-approval.
- UPDATE this design note with the implemented boundary and offline validation.

Later runtime integration, ONLY as a separately scoped task:

- NEW `backend/services/extraction/ocr_candidate_source.py`: local OCR boundary;
  engine-specific code stays here, not in canonical mapping or Gemini.
- NEW `backend/services/extraction/supporting_assessment.py`: application coordinator
  using existing orchestration plus optional candidate source and assessment store.
- UPDATE `backend/core/config.py`, `backend/main.py`, `.env.example`: optional
  disabled-by-default source construction, only when runtime wiring/storage exists.
- ADD focused coordinator/source tests. Connect a review resource/storage/export
  through a separate approved implementation; exact files cannot be named for
  components that do not yet exist. No new infrastructure is implied.

Kept unchanged in Task 10B: `ExtractionOrchestrator`,
`ExtractionAdapter`, Gemini adapter, canonical conversion, provider registry/policy,
domain models, API routes/contracts, prompts, normalizers/scorer, evaluation schemas,
ground truth and all experiment artifacts. No dependency changes required.

## Task 10A execution record

Only this design document was added. No implementation, OCR, provider request,
dependency installation or commit. Source/schema/business-rule inspection found no
existing numeric-length rule or implemented review/export service. Documentation
whitespace/diff checks are appropriate; runtime tests are not evidence for a design
that has not been implemented.

## Task 10B execution record

Added `backend/services/extraction/ocr_candidates.py` and
`tests/test_ocr_candidates.py`; updated only this design document. Screening occurs
on candidate construction and never repairs a token. All returned advice is
detached from canonical values; immutable typed evidence is required. No confidence
field, automatic field assignment, business identifier normalizer, runtime OCR,
provider wiring, persistence or review-state mutation was implemented.

Validation: targeted candidate tests **69 passed**; full offline pytest suite
**218 passed**, with 60 existing dependency deprecation warnings. `pip check`,
Python syntax/import checks and whitespace checks passed. All tracked files match
HEAD: Gemini, orchestration, canonical mapping, domain/API contracts, configuration,
prompts, schemas, scorer and dependency declarations were not modified. Frozen
fixture/truth hashes were revalidated. Ruff/mypy are not installed and were not run;
no tool dependencies were added. No network calls, OCR run, staging or commit.

ONE recommended next task: evaluate the pure component offline against preserved
OCR artifacts with explicitly reviewed field assignments, reporting both rejected
tokens and accepted-but-wrong numeric candidates before any runtime integration.

## Task 10D - offline human approval gate

The Task 10C example `47028` demonstrates that a numeric token with correct field
assignment can still contain wrong digits. Syntax screening and label evidence
are not value verification. `backend/services/extraction/ocr_review.py` therefore
adds a separate, pure review artifact for only `central_numbers` and
`commitment_numbers`; it does not change candidate admission or reconciliation.

### Immutable structures and independent checks

- `OCRReviewItem` retains the immutable candidate and optional tuple of detached
  `ProviderValue` snapshots. Its initial state and both check statuses are
  `unverified`; provider agreement never approves an item.
- `OCRReviewDecision` records an explicit `approved`, `rejected` or `unresolved`
  action, with independent `field_assignment_status` and
  `value_verification_status` (`unverified`, `confirmed`, `rejected`, `unresolved`).
  The checks attest to the exact `reviewed_value`, including any human correction.
- Approval requires both checks to be `confirmed` and a nonblank reviewed string.
  An attempted approval with either check unconfirmed raises an error. Wrong
  digits with confirmed assignment must be explicitly rejected, not approved.
  A rejected check cannot be recorded as merely unresolved. An unclear assignment
  permits unresolved/rejected decisions, but never approval.
- `approved_value` is exposed only for approved items. A tentative string on a
  rejected/unresolved item remains audit data and is never exposed as approved.
  Even approval here performs no business write, canonical update or export.

### Exact values, provenance and replay

No stripping, numeric conversion, punctuation removal, length/checksum rule or
automatic repair is applied to reviewed text. Explicit corrections may include
characters excluded by OCR's narrower numeric admission screen. The original
candidate/raw text remains intact; a different reviewed string sets
`human_correction_occurred`. On rejected/unresolved decisions that flag describes
an entered correction only, not approval of it. Unchanged reviewed text preserves
leading zeros and does not consume a candidate's optional normalized value.

`candidate_reference` hashes a deterministic serialization of every candidate
attribute, including engine/version/run, raw/normalized text, field assignment
evidence, document/form/page, source image hash and optional box/coordinate space.
Every decision repeats document/form/page/image provenance and the candidate
reference; application to a different scope fails explicitly. The artifact retains
both the candidate and decision, thus preserving engine/run/raw/field provenance.
It also records a decision ID, required reason, caller-supplied reviewer reference
and timezone-aware review time, plus an optional reviewer note. `to_json()` emits
stable internal audit JSON tagged `ocr-review-v1`; it is not a public API schema.

`apply_review_decision` returns a new item without mutating its input. Identical
replays return the existing item. A changed payload under the same decision ID, or
a different subsequent decision, raises `REVIEW_DECISION_CONFLICT`: this bounded
component accepts one action per item and never silently overwrites review history.
Re-review/supersession and persistence require a separately designed workflow.

These are caller-supplied human attestations. The pure component cannot prove that
a person inspected an image, authenticate a reviewer or establish that a supplied
image hash corresponds to an accessible source. Later runtime integration must
provide those boundaries; no authentication/storage infrastructure is added here.
Audit JSON contains sensitive text and must remain private if eventually stored.

### Isolation and synthetic examples

No `ExtractionResult` is accepted or modified by this gate. Provider snapshots,
candidate validation status, canonical values, `needs_review`, API contracts and
Excel behavior remain unchanged. No OCR, image access, provider request, runtime
wiring, UI, dependency, prompt or scoring change is included.

Synthetic tests illustrate (not claim real human approval of source documents):

- `47028`, assignment confirmed, source digits rejected: retain raw candidate and
  rejection reason; `approved_value=None`; canonical provider value `947328` stays
  untouched.
- `944963`, assignment and digits confirmed by an explicit decision:
  `approved_value="944963"`; raw remains `944963`; canonical review state remains
  untouched.
- A human-entered correction retains both strings exactly, including leading
  zeros, punctuation or other reviewed characters, with a correction flag.

New files: `backend/services/extraction/ocr_review.py`, `tests/test_ocr_review.py`.
Only this existing design document is updated. Task 10B code and tests are retained
unchanged.

Validation: **44 review tests passed**, **69 candidate tests passed** (combined
targeted run: **113 passed**); full offline suite: **262 passed**, with 60 existing
dependency deprecation warnings. `pip check`, syntax/import validation, changed-file
whitespace checks and `git diff --check` passed. A before/after SHA-256 snapshot
of 69 protected files matched exactly: tracked application/test/documentation
files, dependency/runtime configuration, unchanged candidate code/tests, frozen
ground truth and source/fixture PDFs. The updated design note is excluded from
that snapshot. There are no tracked/staged changes; the new modules/tests/design
note remain untracked alongside the pre-existing source PDFs. `.env` and private
evaluation artifacts remain ignored. No live calls, OCR runs, staging or commit.
No formatter/type checker is configured in `pyproject.toml`; no Ruff/mypy check or
dependency installation was performed. This is a tested offline decision gate,
not a production review workflow.

ONE recommended next task: design the persistence and explicit re-review audit
workflow for these artifacts, including source-image access and authenticated
reviewer binding, before any runtime integration or business export.
