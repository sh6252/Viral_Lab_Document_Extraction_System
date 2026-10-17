# Task 10I - authenticated review API boundary (design only)

Status: Task 10J implements the thin HTTP/application boundary with explicitly
injected fail-closed authentication/policy interfaces. No production authenticator,
identity provider, ACL/user store, artifact storage, UI, OCR/provider integration
or canonical merge/export is implemented. The Task 10I design below is retained
as the original proposal; the Task 10J implementation record follows it.

## Task 10I inspection findings (before Task 10J)

- `backend/main.py::create_app` mounts routers from `backend/api/routes/` under
  `/api/v1`. It constructs configuration/extraction dependencies explicitly and
  stores them in `app.state`; it does not construct a review repository/service.
- `backend/api/routes/extractions.py` uses an `APIRouter`, Pydantic models with
  `extra="forbid"`, and a structured `error` envelope. Its orchestrator helper
  reads the injected adapter from request application state. The current endpoint
  performs stateless extraction; generated document/form IDs are not access grants.
- No authentication middleware, principal dependency, identity provider, user DB,
  resource ACL or document/form ownership store is implemented. ADR-006 separates
  review from provider authority; production identity/roles remain unresolved in
  [architecture decisions](architecture_decisions.md). ProviderPolicy controls
  outbound AI access and is NOT authentication or review authorization.
- `ReviewPersistenceService` validates a caller-supplied `TrustedReviewContext`.
  It is an internal assertion, not proof of authentication. Both its current reads
  and writes require `can_review=True`; it has no read-only permission primitive.
- `ReviewSessionRepository.get` loads a complete session; the snapshot/assessment
  may include multiple forms. Existing reads are internal only. The immutable root
  already contains session/document/form identity, but no authorized scope-lookup
  operation or private runtime artifact resolver exists yet.
- The repository owns durable replay, actor conflict, revision/CAS, trusted receipt
  clock and atomic persistence. Those semantics and canonical/public extraction
  models must remain unchanged.

References: [workflow design](review_workflow_design.md),
[persistence design](review_persistence_design.md),
[business rules](business_rules.md). Current HEAD at design time:
`e1ded974a5325ce0ffa73452632ef7f0c5068ec2`, branch `feat/backend-foundation`.

## Authenticated principal and resource authorization

Propose a small internal `AuthenticatedPrincipal`, never a request body DTO:

- `issuer`, `subject`: stable identities obtained only after credential/session
  verification by a configured authenticator.
- Verified permissions/roles if the selected authenticator actually supplies them;
  absence means no grant. Broad scopes alone do not establish document ownership.
- Trusted request context: server-generated correlation ID and the trusted runtime
  clock/context needed for authorization. Do not retain raw credentials/tokens.

`require_authenticated_principal` is a future FastAPI dependency. Missing/invalid
authentication fails closed. Without a configured trusted authenticator it grants
no principal; no development header, anonymous reviewer or permissive fallback.
Do not accept `issuer`, `subject`, roles, access flags or trusted timestamps from
JSON/query parameters or an unverified `X-User-*` header. This design does not select
an identity provider, token type, login/session flow or user database. A deployment
must establish that trust boundary before exposing these operations.

Separate policy operations `read_review`, `submit_review_decision`, and
`read_source_artifact`. Each grant binds the verified principal AND the exact
`(session_id, document_id, form_id)` plus operation. Server policy resolves resource
membership from trusted data, not IDs asserted in a body or the ability to guess a
session ID. No grant transfers to another session, document, form or shared page.

Proposed server flow:

1. Authenticate before resolving/disclosing resource content.
2. Resolve ONLY immutable session scope metadata internally. A small future
   repository `get_scope(session_id)` can return session/document/form identity,
   without loading patient fields, snapshots or history. It is not a public route
   or authorization decision. There is no current ACL source to assume available.
3. Ask the injected authorization policy for the requested operation on that scope;
   absent policy or absent/ambiguous membership denies access. Do not invent grants.
4. Load internally only after the grant, verify loaded identity still matches,
   and apply an explicit allowlisted projection. For writes, create
   `TrustedReviewContext(issuer, subject, document_id, form_id, can_review=True)`
   server-side ONLY after a submit grant, then call `ReviewPersistenceService`.
5. Reads require only a read grant. Do not turn a read-only grant into
   `can_review=True` to satisfy the current persistence wrapper. A thin future
   `ReviewAPIService` may use repository read operations after policy authorization;
   writes continue through the existing persistence wrapper. HTTP route handlers
   never access repository rows directly. This leaves current persistence/gate
   semantics intact and adds no writable authority to the read path.

Authorization must be checked for replay and artifact retrieval as well. Grants
are request-scoped, not cached indefinitely. Future revocation semantics belong to
the actual policy source; they cannot be guaranteed by this design alone.

## Minimal operations

All paths follow the existing `/api/v1` convention. There is no public session
creation endpoint, list/search endpoint, raw-history endpoint or reopen/merge API
in this slice. Session provisioning remains a separately trusted internal operation.

### GET `/api/v1/review-sessions/{session_id}`

Requires `read_review` for that session/document/form. Returns HTTP 200 with a
`ReviewSessionDTO`, not the internal session, row or codec JSON. The response is
for exactly the selected form; the application projects it before serialization.

Proposed fields:

- `session_id`, selected `document_id`, `form_id`, `revision`, `review_status`.
- `actionable_items`: tuple/list of `ReviewItemDTO` for this session only.
- `pending_issues`: supported-field names and safe reason codes without invented
  values. `outside_scope_issue_indicators`: the other field names and reason codes
  only, without names, patient IDs or unrelated raw business values.
- `ocr_source_status` and `workflow_scope="ocr_candidates"`, making clear that
  `completed` is candidate-workflow completion, not record approval/export readiness.
- Per-operation capability indicators derived from current grants, such as
  `can_submit_decision`, never accepted back as authority.

`ReviewItemDTO` contains only:

- `candidate_reference`, supported `field_name`.
- Conditional sensitive `ocr_value={raw, normalized}` and selected-field
  `provider_values=[{raw, normalized}]`, retaining exact strings.
- Current `review_state`, `field_assignment_status`, `value_verification_status`.
- `prior_decision`, if any: decision ID/status, exact reviewed value, correction
  flag and reason/note where the read grant permits review text. Human-entered
  reviewer reference may be shown as unverified metadata; never label it as the
  authenticated actor. Trusted principal issuer/subject are not returned.
- `evidence`: the source descriptor below. Do not forward the candidate's arbitrary
  assignment-reference string: it can contain private paths. An assignment kind
  may be exposed as a safe enum, but not its internal locator.

Only actionable items need full value display. Do not expose the complete
SupportingAssessment, other-form evidence, hidden candidates or all seven canonical
fields for convenience. Later requests for additional clinical context need a
separately approved projection/permission, not `.model_dump()` on internal objects.

### POST `/api/v1/review-sessions/{session_id}/decisions`

Requires BOTH selected-form review read and submit permissions, since the success
response contains review data. Read permission alone never authorizes POST.

`SubmitReviewDecisionDTO` fields:

- `decision_id`: caller-generated stable nonempty ID, unique within this session.
- `candidate_reference`: an existing actionable candidate reference.
- `expected_revision`: strict nonnegative integer; booleans/string coercion denied.
- `decision`: `approved | rejected | unresolved`.
- `field_assignment_status`, `value_verification_status`: existing verification
  literals `unverified | confirmed | rejected | unresolved`.
- `reviewed_value`: string or null; exact characters retained, no trimming,
  digit repair, case/transliteration or business normalization.
- `review_reason`: nonblank string; `reviewer_note`: optional nonblank string/null.
- `reviewer_reference`: optional nonblank human-entered string/null, never authority.
- `declared_reviewed_at`: timezone-aware ISO timestamp, explicitly UNTRUSTED human
  metadata. It does not replace the repository's trusted server receipt timestamp.

The declared timestamp is the one additional client metadata field needed to map
the existing `OCRReviewDecision.reviewed_at` deterministically. Generate it once
when preparing a human action and retain it unchanged for retries. Generating a new
internal reviewed time on every HTTP attempt would break durable replay, especially
for concurrent identical requests. It is not necessary to change the repository or
smuggle time into notes. A server-generated correlation ID is separate.

Example synthetic request shape:

```json
{
  "decision_id": "synthetic-decision-1",
  "candidate_reference": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "expected_revision": 0,
  "decision": "approved",
  "field_assignment_status": "confirmed",
  "value_verification_status": "confirmed",
  "reviewed_value": "000123",
  "review_reason": "Synthetic source verification",
  "reviewer_note": null,
  "reviewer_reference": null,
  "declared_reviewed_at": "2026-10-17T10:00:00+03:00"
}
```

Propose strict Pydantic request DTOs with `extra="forbid"`. The body cannot contain
trusted issuer/subject, `can_review`, receipt time, document/form/page identity,
source image hash, canonical values or arbitrary internal objects. The server
derives provenance from the authorized stored actionable item, verifies session
identity, and builds the internal decision. The path selects the session; a body
cannot move a candidate to another resource. Existing gate validation remains the
authority for check/approval semantics. Transport limits must not become invented
identifier-format restrictions.

Compatibility detail: internal `reviewer_reference` currently requires a nonblank
string. Map absent/null API metadata to a reserved internal marker
`review-api-v1:reference-omitted`. Reject that exact marker if explicitly supplied
as user text, and reverse it to null in API projections. All real supplied strings
remain exact. The marker is NOT an identity, a default reviewer or a business value.
This narrow adapter mapping avoids a persistence/codec semantic change and avoids
colliding an absent reference with an actual human-entered string.

Proposed `ReviewDecisionResponseDTO` (HTTP 200 for acceptance OR replay):

- `session_id`, `form_id`, current `revision`, `review_status`, `replay`.
- `item`: current projected state of the targeted item, with the same sensitive
  read filtering as GET; other items are not implicitly returned.
- `workflow_scope="ocr_candidates"`; no `business_approved` or export flag.

Return only after the repository transaction commits. On replay, the response uses
the latest authorized session state/revision, which may include subsequent actions
on other items; it need not repeat the original response bytes. Clients may GET
again to refresh the whole selected-form projection. No HTTP payload is the source
of trusted actor identity or trusted receipt time.

## DTO exposure and confidentiality

Use separate API request/response models; never subclass or return `ReviewSession`,
`ReviewOutcome`, `PersistedReviewEvent`, table mappings or tagged codec payloads.

- Scope IDs, revision/status, candidate references, field names, reason codes and
  capability flags are appropriate only after resource authorization; identifiers
  are not public merely because opaque.
- Raw/normalized OCR/provider text, reviewed text, notes and source descriptors are
  sensitive and conditional on the selected-form review read/source grants.
- Internal only: full canonical snapshot, fingerprints/codec/preparation JSON,
  complete multi-form assessment, raw history/envelopes, principal identity,
  DSNs, SQL errors, filesystem/storage locators and arbitrary evidence references.

Build DTOs from an explicit field allowlist. Test both recursive response keys and
values: removing top-level snapshot fields is insufficient if another-form text
is copied into a nested note/assessment. No other-form entity can contribute an
item/provider value/issue to the selected-form projection. Existing candidate
assignment is still a caller assertion; do not claim it proves source-image privacy.

Use `Cache-Control: no-store` for session/decision/source responses and errors that
can disclose resource state. Do not enable public caching or response/body logging.
If browser cookies are later chosen for authentication, CSRF protection for POST
is required; credential transport/CORS policy is a deployment decision, not selected
or implemented here.

## Source evidence retrieval

Choose authenticated backend streaming as the initial proposed model, avoiding
public URLs and signed-URL infrastructure. A future optional operation is:

GET `/api/v1/review-sessions/{session_id}/artifacts/{artifact_reference}`.

The session DTO's evidence descriptor contains only an opaque runtime-managed
`artifact_reference` (nullable until resolvable), ONE-based page number, image
SHA-256, optional bbox/coordinate-system and `availability`. Do not expose engine
filesystem paths, evaluation/artifacts references, permanent public URLs or DB blobs.
Preserve the declared coordinate/rotation conventions; a hash is not a locator.

The runtime artifact resolver must establish membership in the authorized session,
document, selected form, page and exact image version/hash. It rejects arbitrary
references/path traversal and requires `read_source_artifact` on every request,
including a cached client descriptor. It opens private storage internally, verifies
the expected image/version and streams only the approved content type. No redirects
to user-controlled URLs, generic path fetcher or raw storage credentials.

**Shared-page caveat:** a full image may visibly contain another logical form.
Selected-form review access alone is insufficient to stream it. Require permission
for all forms actually visible on that image, or a separately trusted privacy-safe
rendition with its own reference/hash/coordinate mapping. No crop/rendition feature
is invented here; if neither condition can be established, deny source retrieval.
An OCR bbox is not a privacy boundary. This avoids hiding another-form JSON while
leaking the same information through the source image.

Current runtime storage/ingestion and artifact bindings do not exist; catalog
artifact references are optional. Surface authorized evidence as unavailable when
not resolvable. Never substitute ignored evaluation files or assume that a claimed
hash proves source access. Missing source does not auto-approve a value or relax
human verification; the reviewer can leave it unresolved.

## Safe errors and anti-enumeration

Propose the existing outer shape, with review-specific allowlisted codes:

```json
{"error":{"code":"REVIEW_RESOURCE_NOT_FOUND","message":"Review resource unavailable.","retryable":false}}
```

No input echo, patient values, candidate text, driver exception or internal state
dump. Framework request validation must also be sanitized: default validation error
bodies can echo submitted values and must not be used unfiltered for review bodies.

- **401 `AUTHENTICATION_REQUIRED`:** absent/invalid authentication, same behavior
  before resource lookup, without confirming session existence. Appropriate challenge
  headers depend on the credential mechanism eventually selected.
- **404 `REVIEW_RESOURCE_NOT_FOUND`:** nonexistent session OR no scoped resource
  visibility, including cross-document/form access. Use the same envelope; do not
  distinguish denied vs missing IDs or expose the denied document. Avoid different
  diagnostic headers. This reduces enumeration; it does not promise identical
  timing against every side channel.
- **403 `REVIEW_OPERATION_FORBIDDEN`:** authenticated caller already permitted to
  view this exact resource, but lacks submit/source capability or cannot replay
  another actor's accepted action. Do not expose that actor's identity. If resource
  visibility is absent, use the masked 404 instead.
- **409 `REVIEW_STALE_REVISION`:** new action has an old revision; caller may fetch
  the authorized projection. Return no full snapshot or unfiltered conflict detail.
- **409 `REVIEW_DECISION_CONFLICT`:** same ID with different decision content,
  another action already exists for this candidate, or illegal one-action retry.
- **422 `INVALID_REVIEW_DECISION`:** invalid DTO, nonactionable candidate, missing
  reviewed value/checks for approval, invalid declared timestamp or other gate
  validation. Use generic errors without revealing non-session candidates.
- **500 `REVIEW_INTEGRITY_FAILURE`:** corruption/unknown stored version. Fail closed,
  no automatic repair, no fallback session or raw error text.
- **503 `REVIEW_SERVICE_UNAVAILABLE`:** DB/dependency unavailability or unresolved
  transaction outcome. Tell the client to preserve the same decision ID/content
  on retry; never infer rollback merely from a lost connection.
- **503 `REVIEW_SOURCE_UNAVAILABLE`:** authorized artifact binding/content absent
  or unverifiable. Unauthorized/nonmember references are masked as 404 instead.
- **200 replay:** success with `replay=true`, not an error or a second event.

Map internal errors explicitly; do not forward arbitrary `str(ValueError)` or
exception chains. An authenticated user who can read a resource may learn its
authorized current revision through GET; this is not permission to view history
actors, unrelated forms or storage information.

## Idempotency and audit mapping

Client retains `decision_id` and decision content across retries. Identity content
includes candidate/checks/action, exact reviewed text, reason/note, optional human
reference and the declared reviewed time. Missing/null optional metadata has one
documented DTO meaning; strings are never stripped or normalized. Expected revision
is a concurrency precondition, NOT identity content. Authentication and request IDs
are not client-controlled decision fields.

The adapter deterministically builds the same internal decision from that content
and immutable server provenance. A retry may change expected revision yet still
replay; equal aware timestamp instants follow existing Python/repository equality.
A changed business/declared-metadata value under the same ID conflicts. A new
decision with a stale revision conflicts. Keep repository replay-before-revision
ordering and actor checks, including after process restarts and concurrent retries.
Do not pre-reject all stale HTTP requests before consulting durable idempotency.

Trusted audit identity comes ONLY from verified principal mapping; persisted
`reviewer_reference` and `declared_reviewed_at` remain declared metadata. Repository
generates the trusted receipt time. Existing events retain exact decision content,
raw candidate linkage, corrected text/flag, actor, receipt and state/revision chain.
Replays do not append events or reattribute the accepted event. Authorization
decides whether the caller may act now; actor attribution records who performed
the accepted action. Neither substitutes for the other.

Never log request/response bodies, raw tokens, notes, snapshots, candidate values,
private artifact locators, credentials or SQL parameters. Treat session/document/
form/candidate/hash/principal IDs as linkable data, not ordinary log fields.
Use a server-generated random correlation ID with safe operation/result codes;
validate/replace external request-ID headers rather than trusting arbitrary text.
Keep necessary security audit records access-controlled and separate from general
logs. Do not invent a retention duration; the existing retention decision is open.

## Exact future touch points and bounded implementation

Follow existing directory conventions rather than adding another route framework:

- NEW `backend/api/routes/reviews.py`: GET/POST and an optional source-stream route;
  thin dependency use, DTO serialization and safe status/error mapping only.
- NEW `backend/api/review_schemas.py`: strict independent DTOs, projection/error
  models, optional-reference mapping and declared timestamp validation.
- NEW `backend/api/dependencies.py`: injected authenticator/principal, policy and
  review service dependencies with fail-closed behavior; no built-in header auth.
- NEW `backend/core/review_access.py`: principal/grant values and small injectable
  authentication/authorization boundary interfaces, no identity-provider selection.
- NEW `backend/services/review_api.py`: scope authorization, allowlisted selected-
  form projection, deterministic internal decision construction and persistence
  invocation. Read-only paths must not construct writable trusted context.
- NEW `backend/services/review_artifacts.py`: private artifact-access interface and
  session/source membership checks. No evaluation-path resolver or storage backend
  may be installed as an implicit default.
- UPDATE `backend/persistence/review_repository.py` only for a narrow immutable
  scope-metadata lookup; no schema, transaction, codec or review semantics changes.
- UPDATE `backend/main.py` for explicit dependency injection/router registration
  and lifecycle ownership of the DB engine only in the approved wiring task. No
  implicit connection at module import; unavailable auth/policy grants no access.
- UPDATE `backend/core/config.py`, `.env.example` only if explicit runtime DB/auth
  configuration is introduced; placeholders only. Do not repurpose Gemini settings
  or provider-policy flags as review security controls.
- NEW `tests/test_review_api.py`, `tests/test_review_access.py`,
  `tests/test_review_artifacts.py`; extend `tests/integration/test_review_repository.py`
  for the scope lookup and restart/idempotency behavior through the adapter.
- UPDATE this design note and `README.md` with actual availability/limits only
  after separately authorized implementation. No domain/extraction/scorer changes.

Acceptance tests for that future work:

1. Authorized GET and POST expose exactly the selected session/form projection.
2. Missing/invalid authentication denies before content access; unconfigured auth
   and policy fail closed. Scope/role strings alone do not invent resource access.
3. Unknown and unauthorized resource responses are masked consistently; cross-
   document, cross-form and cross-session attempts fail without value/identity echo.
4. Read-only users can GET but cannot POST; submit capability does not imply source
   access. Replay still checks current authorization and actor compatibility.
5. Multi-form snapshots/assessment/nested values never leak through DTOs; shared-
   page source access denies unless all visible content is authorized.
6. Unknown request fields cannot forge issuer/subject, permissions, provenance or
   trusted receipt time. Human references and declared times remain untrusted.
7. Exact replay, stale-revision replay, simultaneous identical requests, changed
   payload conflicts, new stale actions and lost-response retry match repository
   behavior without new events or timestamp/actor rewriting.
8. Declared timestamps/optional-reference mapping are deterministic and injective
   for supported API meanings; exact zeros/punctuation/Unicode survive. Missing
   metadata never becomes authenticated identity.
9. DTOs exclude every internal snapshot/codec/principal/storage field recursively;
   validation, integrity and DB errors contain no submitted values or SQL details.
10. Artifact requests enforce exact membership/hash/page/scope, reject traversal/
    foreign references and handle unavailable storage without reading private
    evaluation files or public fallback URLs.
11. Responses use no-store and no sensitive body logging. No API operation calls
    OCR/providers, alters canonical values, exports data or reopens decided items.

ONE recommended next implementation task: implement the thin DTO/projection and
authorization adapter with explicitly injected fail-closed authentication/policy
interfaces and synthetic route tests. Keep production identity-provider selection,
private storage implementation and public deployment blocked until their trust
boundaries are explicitly configured and validated. Do not add UI or merge/export.

## Task 10I validation and limits

Only this document is added. UTF-8/newline/whitespace validation, local links and
`git diff --check` passed. Git reports no tracked or staged changes, so existing
implementation/configuration/dependencies are unchanged. The document remains
untracked alongside the two pre-existing source PDFs; branch and HEAD stay as
recorded above. No pytest, PostgreSQL connection, package install, authentication implementation,
provider/OCR call, runtime wiring, staging or commit is performed for this design.

## Task 10J implementation record

Implemented `AuthenticatedPrincipal`, `ReviewResourceScope`, async `Authenticator`
and synchronous `AuthorizationPolicy` interfaces in `backend/core/review_access.py`.
There is no default authenticator or policy grant. Only literal `True` from the
injected policy grants an operation; scopes/roles never auto-grant ownership.
Dependencies read explicit `app.state` injections, verify adapter output and attach
a server-generated correlation ID. They do not parse unverified identity headers,
reuse ProviderPolicy or open a DB. Principal values remain internal declarations
from a verified adapter; this task does not implement credential verification.

`backend/main.py::create_app` accepts explicit optional review service/authenticator/
policy arguments and registers the review router. All are absent by default, so
ordinary review requests return 401 before sensitive access. With authentication
but no policy, visibility is denied; missing application service returns a safe
503 after dependencies. Existing extraction construction/routes are unchanged.

`backend/persistence/review_repository.py::get_scope` selects ONLY session ID,
document ID and form ID. It does not load/reconstruct canonical/assessment/history
or item values. Integration tests capture SQL and prove this column boundary.
An AST comparison against HEAD, excluding this new method/import, confirms existing
repository transaction/idempotency implementation is unchanged. No migration,
codec, workflow, canonical model, prompt, scorer or OCR logic was modified.

Implemented strict independent DTOs in `backend/api/review_schemas.py`:
`ReviewSessionDTO`, `ReviewItemDTO`, `ReviewEvidenceDTO`, `ReviewIssueDTO`,
`PriorDecisionDTO`, `SubmitReviewDecisionDTO`, `ReviewDecisionResponseDTO` and
`ReviewValueDTO`. Request fields forbid extras, boolean/string revision coercion,
numeric/naive timestamps and explicit reserved-reference input. Strings retain
their exact content. Omitted/null human reference maps to the documented marker
and maps back to null in projections. No internal model is used as a public DTO.

`backend/services/review_api.py` first authenticates via its principal input, resolves
scope, checks read permission, and only then loads internally. Reads use no writable
context. POST additionally checks submit, obtains provenance only from the stored
actionable candidate, creates trusted actor context from the verified principal,
and calls the existing persistence service. It never pre-rejects stale replay at
the HTTP layer. Output is projected only after the repository call returns committed
results. Other-form items or mismatched loaded identity fail as integrity errors.
Issue reasons are allowlisted; arbitrary assignment locators are never projected.

`backend/api/routes/reviews.py` implements only the GET and POST designed above.
A review-specific `APIRoute` authenticates before body parsing, catches validation
errors without echoing inputs, maps only safe errors and sets no-store on success
and handled errors. It does not change global extraction validation/error handlers.
Unknown failures return a generic unavailable response, not raw exception strings.
The application adds no request/response-body or SQL logging. Deployment-level
ASGI/proxy access logs need separate redaction/disablement before public rollout;
this task does not claim control over external logging infrastructure.

`backend/services/review_artifacts.py` is an optional descriptor resolver boundary,
not a storage/streaming backend. Default/unresolved/failed resolver returns
unavailable evidence with no artifact locator or box. An explicit source grant and
trusted binding must match document/form/page/hash; only opaque references and
safe coordinate labels are admitted. Bindings with more than the selected visible
form are refused. No partial-page renderer or shared-page authorization resolver
is added. There is no artifact HTTP route or private file reader, even when an
injected synthetic resolver returns an available descriptor.

Tests are synthetic and include recursively checking keys AND known hidden values,
read-before-load ordering, read-only behavior, cross-resource masking, forged actor/
provenance fields, deterministic request mapping, replay with stale preconditions,
conflicts, safe validation/DB/integrity errors, source capabilities and unchanged
canonical data. A real PostgreSQL test exercises the API adapter through committed
decisions and process-style reconnect/replay with trusted attribution.

Files created: `backend/core/review_access.py`, `backend/api/dependencies.py`,
`backend/api/review_schemas.py`, `backend/api/routes/reviews.py`,
`backend/services/review_api.py`, `backend/services/review_artifacts.py`,
`tests/test_review_access.py`, `tests/test_review_api.py`, `tests/test_review_artifacts.py`.
Updated: `backend/main.py`, `backend/persistence/review_repository.py`,
`tests/integration/test_review_repository.py`, `README.md` and this existing design
note. No dependency/configuration/.env changes or package installations.

Validation:

- New access/API/artifact unit tests: **67 passed**. PostgreSQL repository tests:
  **36 passed**, including three new scope/API reconnect tests, without DB skips.
- Combined persistence/workflow/candidate/review and API checks: **324 passed**.
- Full regression with the explicitly configured disposable PostgreSQL DSN:
  **473 passed**, with **298 dependency deprecation warnings** (more FastAPI app/
  route instances exercise existing deprecated dependency behavior).
- `pip check`, Python AST/import checks, changed-file whitespace/credential-marker
  checks and `git diff --check` passed. No formatter/type checker is configured or
  newly installed; none is claimed run.
- Before/after SHA-256 snapshots of **88 protected files** match exactly, including
  extraction routes/domain models, Gemini/orchestrator/canonical conversion,
  prompt/scorer/truth, candidate/review/workflow/OCR logic, persistence service,
  codec/models, migration 0001, dependency/configuration files and source PDFs.
- Repository AST comparison verifies only the new scope import/read method was
  added; existing transaction/replay/write semantics are unchanged. No live provider
  or OCR call, source-file read, package install, schema migration change, merge,
  export, UI, staging or commit is included.

The temporary local PostgreSQL container and generated credentials are removed
after testing. Pytest used uniquely generated temporary directories under ignored
artifacts; these are test outputs, not paths accessible through the review API.
Ordinary integration runs still require an explicit safe test DSN/acknowledgement.
Branch remains `feat/backend-foundation`, HEAD remains
`e1ded974a5325ce0ffa73452632ef7f0c5068ec2`. Existing source PDFs remain untracked;
`.env` and private artifacts stay ignored. Task 10J changes remain uncommitted.

ONE recommended next task: define and approve the concrete deployment authentication
and resource-authorization policy adapter contract, including revocation and
access-log handling, before connecting any real identity/ACL provider or exposing
these routes publicly. No such adapter, UI or canonical merge/export is started here.
