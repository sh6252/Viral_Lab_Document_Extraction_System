# Task 11A - end-to-end MVP gap analysis

Analysis date: 2026-10-17. Inspected baseline: `516ab5c481c2dcbdb954dbce8007147604f7087c`,
branch `feat/backend-foundation`. This is analysis/design only. No application,
configuration, dependency, database, prompt, scorer or fixture change is proposed
as already implemented. No provider call or OCR run was made for this analysis.

Update: Task 11B now implements the local review/apply/XLSX operator slice. The
original analysis below is retained as historical context; the implementation
record at the end states what is now available and what remains disconnected.

## Finding and decision

The implemented user flow ends at canonical extraction JSON. There is no connected
flow from an uploaded document to seven-field human correction, a reviewed business
record, an Excel workbook or download. The substantial review infrastructure is a
separate, explicitly injected OCR-candidate workflow for TWO numeric fields; it is
not a general business-record review application.

The highest-value next capability is **canonical result -> explicit seven-field
human review -> separate reviewed record -> safe Excel**. Implement that bounded
business slice before more authentication, OCR, persistence or job infrastructure.
Then connect the existing upload/extraction entry point to it with a small local
demo client and download boundary. Keep Tesseract out of the runtime demo.

The existing modular monolith, Gemini adapter and canonical contracts remain the
architecture. A supervised synchronous local demo is a limited use of the current
path, not a replacement for the approved PostgreSQL jobs/React deployment target.

## Evidence and documentation discrepancies

Code inspected: [application factory](../backend/main.py),
[extraction route](../backend/api/routes/extractions.py),
[configuration](../backend/core/config.py),
[registry](../backend/services/extraction/registry.py),
[Gemini adapter](../backend/services/extraction/gemini_adapter.py),
[canonical conversion](../backend/services/extraction/canonical.py),
[domain models](../backend/domain/models.py),
[workflow](../backend/services/extraction/review_workflow.py),
[persistence service](../backend/services/review_persistence.py),
[review API service](../backend/services/review_api.py),
[review artifacts](../backend/services/review_artifacts.py), routes, tests and
dependency declarations. Repository searches found no frontend application, Excel
exporter, download route, document store or runtime job worker.

Governing rules: [business rules](business_rules.md), especially BR-001 through
BR-004, and [architecture decisions](architecture_decisions.md), especially
ADR-001 and ADR-005 through ADR-010. Results come from
[Task 7](task7_baseline.md), [Task 8C](task8c_prompt_experiment.md) and preserved
private Task 8A/9A/9B/9C/10C reports. Private reports were read locally; patient
names, identifiers, source content and credentials are not copied here.

There are stale statements that must not be treated as current implementation:

- The README introduction still says database/review foundations are absent; its
  Task 10H/10J sections and code demonstrate implemented review persistence/API.
  General document/extraction storage and production authentication remain absent.
- `provider_integration.md` says audit persistence/review are unimplemented; that
  is now true only of the connected extraction/business workflow, not the isolated
  OCR review components. Historical result notes also describe earlier Git states.
- The ADRs describe target batch/job/storage/review/export components and a Docker
  Compose demo. Their presence in the plan is not evidence of implementation.
  This proposal defers those target capabilities where a single-document local
  demonstration does not need them; it does not supersede an ADR or claim the full
  originally planned MVP is complete. Public deployment and durable batch operation
  remain a larger scope.
- Older wording limiting export to approved records is explicitly superseded by
  BR-004/ADR-008. Unresolved forms must remain exportable with explicit statuses.

Only this analysis document is changed in Task 11A; the discrepancies above are
reported rather than silently editing the earlier evidence or architecture.

## A. Current real application flow

`IMPLEMENTED` means the bounded code path exists, not that it is production-ready
or achieves adequate extraction quality. `PARTIAL` identifies a missing connection
or essential scope. `EXPERIMENT-ONLY` is local evaluation work outside runtime.

```text
Local operator / Swagger / HTTP client
  -> POST /api/v1/extractions (one multipart file)
  -> size/signature checks -> DocumentInput + ExtractionRequest
  -> ExtractionOrchestrator -> selected adapter -> canonical schema validation
  -> HTTP ExtractionResult JSON OR structured processing error
  -> END

Separate, explicit internal provisioning only:
canonical result + trusted page/image bindings + screened OCR evidence
  -> ReviewWorkflowService -> ReviewPersistenceService -> PostgreSQL
  -> explicitly injected fail-closed review GET/POST
  -> separate OCR review outcome -> END (no business merge/export)
```

| Step / question | Classification | Actual behavior and limit |
| --- | --- | --- |
| 1. Submit a document | IMPLEMENTED | `POST /api/v1/extractions`, multipart `file`; optional `allow_external_processing` (default false) and `locale` (default `he-IL`). Swagger `/docs` or an HTTP client is usable; no product UI or batch upload. |
| 2. Store the document | NOT IMPLEMENTED | Route reads bytes into `DocumentInput`; no application-owned document/artifact store, source hash record or later retrieval. Framework multipart uploads can spool temporarily; that is not durable/private application storage. |
| 3. Reach orchestrator | IMPLEMENTED | Route gets the injected adapter from `app.state`, creates request/document IDs, requests seven fields and calls `ExtractionOrchestrator.extract`; orchestrator calls adapter and validates canonical schema. `pages=[]`; no runtime page mapping/segmentation. |
| 4. Select provider | PARTIAL | Registry implements only Gemini. Default `local_ocr` selects NO adapter, so ordinary extraction is unavailable. Explicit Gemini configuration plus server/request policy gates and credentials are required. Default model is `gemini-2.5-flash`; historical challenge runs requested `gemini-3.8-flash`. Neither is evidence of the current process setting. `.env` is not automatically loaded. |
| 5. Use ExtractionResult | IMPLEMENTED | Returned directly as JSON. Canonical conversion preserves returned logical forms, string values and optional evidence; present fields become `unreviewed`, other supplied statuses `needs_review`. No human approval occurs. |
| 6. Persist extraction | PARTIAL | Upload path persists nothing. Review repository CAN retain a detached full canonical snapshot when an internal caller explicitly prepares/creates a session. It is not a runtime extraction-attempt/document store, and startup constructs no repository/engine. |
| 7. Automatically create ReviewSession | NOT IMPLEMENTED | No upload-to-session call or public session-creation endpoint. Internal prepare/create operations exist but need exact form/image provenance supplied separately. |
| 8. Produce OCR candidates at runtime | EXPERIMENT-ONLY | Task 9 scripts ran local OCR; Task 10B provides pure screening/assessment. No runtime OCR engine, candidate generator or automatic field assignment is wired in. |
| 9. Review an actual upload | PARTIAL | OCR-candidate GET/POST work with explicitly provisioned sessions and injected verified authenticator/policy/service. Default requests fail 401. There is no upload connection, source viewer/streaming route, UI or all-seven-field action. |
| 10. Turn reviewed evidence into final values | NOT IMPLEMENTED | Approved OCR values remain separate evidence. No explicit apply/merge operation or general reviewed business record exists. `completed` is only candidate-workflow completion. |
| 11. Export Excel | NOT IMPLEMENTED | No exporter/module, workbook contract implementation, spreadsheet library declaration or workbook tests. Scorer row metrics are evaluation functions, not an exporter. |
| 12. Download final Excel | NOT IMPLEMENTED | No export/download route, generated workbook or client download action. |

The synchronous provider call runs inside the current async extraction handler;
there is no background progress, cancellation or durable recovery. Signature/byte
checks are useful but do not fully parse PDFs/images, validate page counts or bound
multipart ingress before framework parsing. These limits matter for deployment;
they do not require rebuilding ingestion to demonstrate one supervised demo file.

`ExtractionResult` contains extraction/provider/form metadata but no top-level
`document_id` or source hash. The route-generated document ID is not a retrievable
resource, and a provider-returned form ID may differ from the requested ID. A demo
wrapper must retain source-to-extraction-to-form identity explicitly. Task 7's
single-page scoring-copy ID association is measurement tooling, not runtime
authorization, segmentation or permission to rewrite canonical form IDs.

## B. Requirement coverage

No business field currently completes the review-to-Excel flow. The figures below
are historical Task 7 exact matches conditional on five successful responses;
uncertain/unverified annotations are excluded. They are not product acceptance.

| Requirement | Current component | Works end-to-end? | Known limitation | Required for MVP? |
| --- | --- | --- | --- | --- |
| Scanned/printed commitment PDF | Upload validation + Gemini inline PDF input | PARTIAL: to JSON | Validated transport/schema, no guaranteed reading or complete-page association; three selected PDFs failed operationally. | Yes |
| Photographed/scanned JPEG or PNG | Upload signatures + Gemini image input | PARTIAL: transport to JSON | Synthetic transport tests; Task 9A returned no successful image extraction. Photo/handwriting quality is not established. | Yes, accepted types; demonstrate limitations honestly |
| Handwritten content | Gemini vision; unreadable/ambiguous statuses | PARTIAL | No reliable demonstrated patient-name recovery; no automatic preprocessing fix validated. | Yes, human correction/unresolved handling |
| `first_name` | `ExtractionFields.first_name` | No: extraction candidate only | 0/5; mostly unreadable, one wrong placeholder. | Yes |
| `last_name` | `ExtractionFields.last_name` | No: extraction candidate only | 0/5; same pattern, no observed systematic swapping. | Yes |
| `israeli_id` | String-valued canonical field | No: extraction candidate only | 3/4; wrong or missing values occur; no validated automatic acceptance policy. | Yes |
| `central_numbers` | Multi-value canonical field | No: extraction candidate only | 2/4 field matches; value recall 3/5; omission and substitution. | Yes |
| `sample_receipt_date` | Raw/optional normalized field values | No: extraction candidate only | 2/4; competing visit/printed/stamp dates; no runtime rule proving receipt role. | Yes |
| `sender` | Canonical string field | No: extraction candidate only | 4/5; payer/originator ambiguity and sender vocabulary remain unresolved. | Yes |
| `commitment_numbers` | Multi-value canonical field | No: extraction candidate only | 5/5 fields, but only three expected/predicted actual values; two matches are verified absence. | Yes |
| Preserve multiple identifiers | `FieldResult.values`, canonical conversion | PARTIAL: represented in JSON | No row serializer, human list correction or relationship review/export. Never expand a Cartesian product. | Yes |
| Flag uncertainty / allow review | Canonical value/review states; OCR gate/API | PARTIAL | Supplied non-present fields flagged; omitted fields can stay null. Present wrong values are unreviewed, not proven correct. OCR gate cannot resolve names/dates/IDs/sender or candidate-free issues. | Yes |
| Never guess missing values | Prompt v1 + candidate-only conversion | PARTIAL: explicit instruction | Cannot guarantee model compliance; the baseline contains wrong placeholders/substitutions. Schema validation is not value validation. | Yes |
| Structured Excel + download | No current component | No | Both workbook generation and delivery absent. | Yes |

Normalization is not a proven cause of the name failures. Canonical mapping is
structural/status sanitation, not a complete business normalizer/validator.
`evaluation/scoring.py` prefers supplied normalized strings for comparison; it is
not a runtime normalization or approval service. Preserve raw text, optional
normalized text and reviewer-entered text independently; do not add field-specific
repairs or sender synonyms while closing integration gaps.

## C. What is sufficient, and what should stop receiving work

Sufficient foundations to reuse now: PDF/JPEG/PNG transport, seven-field canonical
contracts, provider-independent orchestrator/registry, policy-gated Gemini,
structured failures, immutable OCR evidence semantics, review idempotency/concurrency
patterns, real PostgreSQL review transactions and frozen evaluation tooling.
Checkpoint validation previously passed 473 tests; that is regression evidence,
not an extraction-quality or end-to-end export result.

Pause further OCR admission/reconciliation, source-descriptor refinements,
generalized event infrastructure, review-table refactoring, production identity/ACL
integration, queues, reopening/supersession, new providers and another prompt/image
optimization campaign until a useful workbook can be produced. Existing OCR review
work remains preserved and optional. Do not route seven-field corrections through
an OCR item just to reuse its persistence.

The demo blockers are the missing seven-field correction surface, explicit
review-to-business apply step, retained source/result identity, Excel generation
and delivery. Source readability and provider availability remain quality/operating
risks even after those connections exist. More infrastructure alone fixes neither.

## D. Shortest viable vertical slice

Bound the first demonstration to one operator, one file at a time, a supported
PDF/image with one known logical form, and already-approved demo data. Always retain
every returned form; unexpected/ambiguous multi-form output must be explicitly
handled or shown as a grouping failure, never silently reduced to the first form.
General multi-page segmentation/grouping remains later work.

```text
Choose/upload PDF or image + explicit external-processing permission
  -> existing Gemini/orchestrator -> immutable canonical snapshot
  -> inspect original source; show ALL seven provider fields and review states
  -> explicit per-field approve/correct/acknowledge blank/leave unresolved actions
  -> explicit apply -> separate ReviewedBusinessRecord for each logical form
  -> Excel: Forms + ProcessingFailures + ReviewAudit
  -> save/download; no new provider call during review or export
```

A local operator driver can prove the business slice first without new HTTP auth:
load canonical JSON, open the source locally and supply explicit field actions.
Saving a workbook locally is not an implemented browser download. The subsequent
thin local client should use React/TypeScript with existing FastAPI, display the
locally selected original PDF/image and offer seven-field review plus download.
No evidence overlay, server image renderer or OCR is necessary for that bounded
single-form demonstration. A local source preview is not an authenticated backend
artifact service; preserve the existing source-access restrictions.

| Missing connection | Existing component to reuse | Smallest new component / likely exact touch points |
| --- | --- | --- |
| Bind upload to its extraction and source | Existing `DocumentInput`, `ExtractionRequest`, canonical IDs | Later `backend/services/demo_workflow.py`: explicit per-run envelope with document/content hash, extraction snapshot/hash, actual form IDs, provider/model/prompt/pipeline and safe failure metadata. Update `backend/api/routes/extractions.py` only if adding an opt-in wrapper; keep the existing response contract. A private local operator bundle is sufficient for the first CLI slice, outside evaluation directories. |
| Review all seven fields | Canonical field/value/review literals, BR review actions | New `backend/services/business_review.py` and `tests/test_business_review.py`: immutable actions/projection, exact form/field scope, original value lists and explicit reviewed lists. Reuse rules and identity/revision patterns; do not widen `OCRReviewDecision` or change its gate. |
| Apply decisions into a business record | Canonical snapshot; optional separately verified `ReviewOutcome` | Same `business_review.py`: explicit pure apply operation and distinct record. Details below. No canonical or OCR workflow mutation. |
| Generate an actual workbook | Logical forms and reviewed records; existing failure envelopes | New `backend/services/excel_export.py`, `tests/test_excel_export.py`; declare one justified XLSX writer in `pyproject.toml` in the implementation task only. Use the contract below. No exporter exists to reuse. |
| Demonstrate review/export immediately | Existing canonical JSON from upload or preserved output | New `backend/tools/review_export.py`: local operator entry point for a bound source/snapshot plus explicit review actions; emit private workbook/audit bundle. Test with synthetic data in `tests/test_review_export.py`. Cached input is explicitly labeled historical, not a new extraction. |
| Connect upload/review/download for a browser user | Existing extraction route, new pure review/export capability | Later `frontend/package.json`, `frontend/src/App.tsx`, `frontend/src/api.ts`; `backend/api/routes/exports.py`, `backend/api/export_schemas.py`, `backend/services/demo_workflow.py`, `tests/test_export_api.py`; explicit registration in `backend/main.py`. Keep source/result binding server-controlled; do not trust a client-resubmitted canonical snapshot as a stored provider result. |
| Retain demo audit without pretending durable runtime exists | Snapshot/hashing patterns in workflow; existing private-storage architecture | First local operator bundle records source hash, immutable snapshot, explicit actions, outcome, export hash/version. A browser wrapper needs a scoped server-owned run association with explicit lifetime before accepting actions. Production document/business-review persistence is separate; do not insert generic actions into OCR tables or use ignored evaluation artifacts as the runtime store. |

These are proposed file paths, not existing features or authorization to implement
them now. The browser wrapper must define its bounded local session/lifetime and
private access before implementation. Do not expose the currently unauthenticated
extraction endpoint on a network. The first local operator capability can avoid
that new HTTP boundary entirely; never disable the fail-closed review routes to
make a demo work. No public IdP/ACL integration is needed for that CLI capability.

## E. Minimum explicit review/apply contract

Use a small separate reviewed-record representation, not a new `ExtractionResult`
version and not an adapter modification. Support the same seven field names from
the beginning; initially implement one selected form per apply call and iterate
all forms for export.

Conceptual operation:

```text
apply_business_review(canonical_snapshot, scoped_field_actions,
                      source_binding, expected_review_revision)
    -> ReviewedBusinessRecord + immutable review audit
```

Minimum inputs and outputs:

- Bind source document hash, extraction ID/snapshot hash and exact form ID to each
  action and output. Reject foreign/stale snapshots, unknown fields, conflicting
  actions or scope mismatch atomically. Keep an explicit decision ID and actor/time/
  reason, original field state/value list and new field state/value list.
- Retain a detached canonical snapshot, including raw/normalized values and
  evidence. Immutable reviewed records refer back to it. Applying, exporting or
  mutating caller objects must never change the provider result or earlier audit.
- Actions cover approve-as-extracted, explicit correction plus approval, reject,
  acknowledge blank and leave unresolved/needs clarification. A correction without
  approval is still pending; it is not a final trusted field. Exact entered text
  is retained. Approval applies to the WHOLE reviewed value list, so multi-value
  additions/removals and zero values are explicit; no automatic union/deduplication.
- No action leaves the field unreviewed/unresolved. Non-present and omitted fields
  stay distinct and flagged. A present provider value may be displayed/exported as
  an unverified candidate with explicit status, never relabeled human-approved.
  Unapproved tentative corrections stay audit-only; they do not replace provider
  candidates in business cells. An acknowledged blank has an explicit action and
  blank cells; rejection alone is not proof of source absence.
- Do not auto-approve present fields: baseline errors include present/unreviewed
  names and IDs. With no validated field acceptance policy, offer human inspection
  of all seven fields, not only those already flagged `needs_review`.
- Approved OCR evidence is eligible only as a separately selected human input to a
  later explicit business action, bound to the same source/snapshot/form/field and
  exact reviewed text. Agreement, OCR availability or `completed` never triggers
  automatic import. The MVP path supplies no OCR evidence at all.
- Record completeness is derived from field review outcomes, not OCR session state.
  Unresolved records still export under BR-004. Preserve ambiguous identifier
  relationships without pairing/expanding rows. A processing failure has no reviewed
  commitment record; report it in the separate failure representation.
- Identical explicit action replay must not create another correction/event;
  conflicting IDs and stale new revisions fail. Reuse the established semantics,
  but do not claim the OCR repository stores these new general actions today.
  The first local bundle gives inspectable provenance, not durable concurrent or
  authenticated audit guarantees. Those require later storage/runtime integration.

For the local operator tool, actor identity is an explicit operator declaration,
not authenticated authority. A later HTTP action must derive actor/access from its
trusted boundary. Neither design may turn a user-entered name into a verified
principal or make spreadsheet approval depend on model confidence.

## F. OCR decision

**Exclude Tesseract from the MVP runtime. Preserve all experiments and candidate/
review components for later optional use.** This does not remove the adapter
architecture's ability to support hybrid extraction later.

Task 9B/9C used the existing MuPDF/PyMuPDF OCR path with the same Hebrew+English
language data. Across three selected difficult forms, 300 DPI left handwritten
names at **0/6**, patient IDs at **0/3**, exact central-number tokens at **2/4** and
complete verified receipt dates at **0/2**. One commitment token lost stray
punctuation and selected printed text improved; this is limited text recovery,
not canonical extraction accuracy or proof of correct field association.

Task 10C inspected 61 digit-bearing occurrences, admitted five manually assigned
candidates and rejected 56. **Four of five admitted candidates were correct; one
central-number candidate was wrong despite valid syntax and correct field role.**
Central candidate precision was 2/3; commitment precision 2/2. These tiny, manually
assigned samples establish neither automatic candidate-generation recall nor
production precision. All three corresponding Gemini calls had failed, so there
was no real paired canonical reconciliation; synthetic tests do not fill that gap.

Runtime OCR would require rendering, language assets, field assignment, provenance,
candidate review and error handling while still leaving the most difficult fields
to humans. Its measured benefit is too small for that demo cost. It is not a
provider-outage fallback that can safely manufacture an `ExtractionResult`.

## G. Gemini quality and availability are separate gaps

Task 7 used Gemini / `gemini-3.8-flash` on eight approved development fixtures:

- **Availability/coverage:** eight attempted, five successful/scored, three failed;
  the 5/8 observed completion fraction is not an estimated service SLA. S1/P14 and
  S2/P14 exhausted three HTTP 504 attempts each; S2/P20 exhausted 503/504/503.
  Five successful document latencies were approximately 26-39 seconds; exhausted
  failures were approximately 71-188 seconds including backoff.
- **Successful-response field accuracy:** **16/32 = 50%**; names 0/5 each, ID 3/4,
  central numbers 2/4, receipt date 2/4, sender 4/5 and commitments 5/5. Central
  value recall was 3/5. Commitment scores include two verified absences and only
  three expected numeric values. Sixteen missing expected values and five false
  positives were recorded; missing counts include wrong substitutions.
- **Full-selection exact-match yield:** **16/49 = 32.65%**, including failed
  documents in the planned denominator. The 17 fields on failures have no model
  output and no conditional accuracy score. The frozen uncertain annotations remain
  excluded. No field was approved; zero silent accepted errors with undefined
  accepted-error rate is not proof of safe unattended processing.
- **Names:** eight abstentions and two wrong placeholder values across ten fields;
  no observed swapping, normalization defect or wrong-form association explains the
  pattern. Optional evidence exists in the contract but name boxes/evidence were
  absent in preserved outputs. Human correction is essential for this demo.
- **Subsequent experiments:** Task 8C did not improve paired name accuracy (0/6)
  and reduced comparable overall field accuracy 11/20 -> 10/20; v1 was restored.
  Task 8A upright PDF still ended in 504 after three attempts. Task 9A initially
  attempted two PNGs with no extraction and stopped on 429; a later isolated upright
  S1/P14 PNG attempt also ended in 504 after 62.615297 seconds. None demonstrates an
  orientation/image reliability gain or isolates the cause of service errors.
- **Quota:** HTTP 429 / `RESOURCE_EXHAUSTED` was observed in Gemini experiments.
  A later user-reported project/key change was followed by the single-attempt 504
  recorded above, not a demonstrated quota resolution. Exact quota mechanism,
  billing state and current availability are not established here. Earlier OpenAI
  quota evidence belongs to a retired integration and says nothing about Gemini's
  present account. No key/configuration inspection or fresh call validates readiness.

These are small development samples, historical sequential calls and uncertain
manual annotations, not a representative holdout, causal experiment or production
accuracy benchmark. Provider outages cannot be reclassified as OCR/vision mistakes.

Minimum demo handling: show processing/in-progress state, preserve one successful
snapshot for review/export without resending, and display structured failures with
no fabricated business row. Stop on quota/auth/policy errors. Do not add a manual
retry loop, new provider or automatic OCR fallback. If live service fails, demonstrate
the review/export stage with an explicitly labeled preserved extraction and source
binding; report that the live extraction failed. Historical replay is not live
end-to-end success. A workbook can contain separately labeled replay rows and live
processing failures, with distinct attempt/source metadata.

Retry policies must not be conflated: the registry-built adapter currently defaults
to one adapter retry, classifies rate-limit errors as retryable, and does not
explicitly pin SDK attempts in its factory. Task 7's MEASUREMENT runner instead
disabled adapter retries, pinned one SDK attempt and allowed two selected transient
retries, never 429. A future local demo composition must explicitly set its one
retry owner/attempt budget and stop-on-429 behavior; do not claim the harness policy
already governs the HTTP route. Using a one-attempt injected demo client avoids
stacking retries; it does not require redesigning provider architecture. No retry
policy is changed in this analysis.

## H. Minimum Excel workbook contract (proposal)

There is no existing exporter. Exact schema remains UD-001; the following is a
concrete minimal proposal to adopt in the export implementation, not a claim of
laboratory approval. Export a real `.xlsx`, not a CSV renamed to XLSX.

**`Forms` sheet:** one row for EVERY logical form, including unresolved forms. First
seven columns, in order: `first_name`, `last_name`, `israeli_id`, `central_numbers`,
`sample_receipt_date`, `sender`, `commitment_numbers`.

Add `document_id`, `form_id`, `extraction_id`, `source_sha256`, `data_origin`
(`live` or explicitly labeled `historical_replay`), `record_review_status`,
`unresolved_fields`, `relationship_status`, `provider`, `model`, `prompt_version`,
`pipeline_version`, `review_revision`. For each business field add
`<field>_value_status`, `<field>_review_status`, `<field>_value_source`
(`provider_unverified`, `human_approved_provider`, `human_corrected`, `none`).
Do not infer missing attempt metadata from a current environment setting.

- Write identifiers and dates as explicit text, preserving Hebrew, punctuation and
  leading zeros. No numeric conversion, formula interpretation, locale date parsing
  or newly invented length/checksum rule. Use the exact approved reviewed text where
  one exists; otherwise provider raw candidate text may appear only with explicit
  unreviewed/unresolved status. Do not prefer normalized text silently.
- Represent `central_numbers` and `commitment_numbers` as compact JSON arrays of
  exact strings in a single text cell. This is reversible and avoids delimiter
  collisions, dropped values and Cartesian rows. Empty list is `[]`; the status
  columns distinguish unresolved empty, acknowledged blank and verified disposition.
  For unexpected multiple values in a normally scalar field, leave its main cell
  unresolved and retain all candidates in audit rather than selecting the first.
- Missing/unreadable/not-processed/omitted scalar fields have blank business cells
  with explicit status; ambiguity remains ambiguous. Never use synthetic zero,
  guessed dates or `N/A` as a business value. Approved blank and rejected candidate
  are different review dispositions, not extraction `absent` states.
- Keep all forms regardless of approval. Field/form review status is visible by
  default, not an optional hidden comment or a claim of export eligibility.

**`ProcessingFailures` sheet:** safe document/attempt reference, source hash if
known, provider/model, safe failure code, retryable category, attempt count, latency
and data origin where available. Failures do not create `Forms` rows with seven
blanks, guessed form IDs or invented extraction status. Empty output with no forms
must not be presented as successful extraction.

**`ReviewAudit` sheet:** source/snapshot/form/field binding, original raw and
normalized value lists/status, decision ID/action, reviewed exact list, actor
declaration or trusted actor reference as applicable, timestamp, reason, old/new
review state and revision. Retain OCR evidence only if deliberately referenced by
an explicit business decision in later scope. Audit is sensitive, private and
does not replace the immutable local input/action bundle or future durable store.

Spreadsheet safety is P0: every document/reviewer-derived string, including notes,
sheet data and source labels, is written as a literal text cell, with formulas,
automatic hyperlinks and external links disabled. Test `=`, `+`, `-`, `@`, leading
whitespace/control characters, Unicode and leading-zero values by reopening the
workbook and checking cell types/values, not only file existence. Retain exact raw
text in the bound audit even if the writer needs a safe display representation;
never silently truncate Excel-limit strings. Use a safe generated filename.

Download should generate/stream the workbook with the XLSX content type,
attachment disposition and `Cache-Control: no-store`, or save to an explicitly
chosen private path for the local operator tool. No public static exports folder,
patient filename in a URL, permanent public link or sensitive logging. A browser
wrapper must authorize access to its server-owned run, not accept arbitrary paths.

## I. Prioritized backlog

Estimates are rough focused engineer-days, including synthetic tests, not schedule
guarantees. No new infrastructure stack is required; dependency approval/version
selection for an XLSX writer belongs to implementation. Only these three priorities
are used.

### P0 - required for the end-to-end local/private demo

| Item | Why it matters | Estimated scope | Existing dependency |
| --- | --- | --- | --- |
| Seven-field review/apply plus safe XLSX operator slice | Turns existing JSON into an inspectable, corrected workbook; resolves the main missing business boundary. | 1-2 days; pure service, exporter, small operator command, synthetic tests. | Canonical contracts, BR-001..004, snapshot/review semantics; one XLSX writer to justify. |
| Connect upload/result/source identity to thin review/download client | Makes real PDF/image -> review -> downloaded workbook demonstrable without hand-editing internal objects. | 1-2 days; bounded one-operator React/FastAPI wrapper, private run association, local source preview, no broad UI framework. | Existing extraction route/provider; P0 review/export slice. |
| Explicit error/status and replay presentation | Prevents provider outage being shown as an empty success; permits an honest demo when quota/service fails. | 0.25-0.5 day; reuse safe errors, progress and no-repeat review/export; label historical replay. | Existing adapter error categories, preserved outputs/provenance; no new experiment. |
| Synthetic complete-flow acceptance and operator instructions | Proves seven-field correction, immutability, all-form row count, failure separation and safe download. | 0.5-1 day, integrated with the above; mocked provider plus workbook reopening. | Existing API/workflow tests and frozen evaluator; no required live call. |

A local operator CLI covers business proof before the browser connection, but does
not by itself satisfy the complete upload/review/download product demonstration.
Human inspection of all fields is acceptable for this demo; unattended accuracy
and validated automatic acceptance are not claimed. No production auth/ACL work is
P0 for the local CLI. A remotely accessible demo would change that constraint.

### P1 - valuable if time remains

| Item | Why it matters | Estimated scope | Existing dependency |
| --- | --- | --- | --- |
| Multiple uploaded files and reviewable form grouping | Improves realistic throughput and avoids mixing forms/pages. | 1-2 days; explicit memberships and grouping correction, no heuristic pairing. | Working single-file slice, canonical multi-form lists, source bindings. |
| Private run resume and durable business-review storage | Preserves work across restart instead of only a local audit bundle. | 1-2 days; separately approved PostgreSQL/general-action persistence, not OCR-table reuse by renaming. | Existing DB/codec/transaction patterns, new general review model. |
| Review usability and workbook readability | Faster side-by-side review, list editing, RTL display, column widths/freeze/header filters. | 0.5-1 day after functional slice; no evidence-overlay engine. | Thin React client, source preview and exporter. |
| Repeatable local packaging/demo launch | Reduces setup risk; explicit model/environment/policy and private output paths. | 0.5-1 day; document or compose existing components without changing defaults. | Approved stack and functioning slice; `.env` placeholders only. |

### P2 - production hardening / after hackathon

| Item | Why it matters | Estimated scope | Existing dependency |
| --- | --- | --- | --- |
| Production identity, scoped ACL, revocation and access-log controls | Required before real shared/network access; not a prerequisite for an operator-only local tool. | Multi-day, dependent on deployment/identity decisions. | Fail-closed review boundary; approved trusted policy/identity source. |
| Full private ingestion/artifact store and lifecycle | Secure source retrieval, integrity, retention/deletion and shared-page privacy. | Multi-day; deploy-specific controls/decisions. | Storage architecture, hashes, descriptor interface; no evaluation-directory promotion. |
| PostgreSQL durable jobs/batches and recovery | Reliable concurrent processing, leases/idempotency and controlled retry ownership. | Multi-day; follow ADR-001, no Redis/Celery. | Working business slice, future document/job tables; current review transactions are insufficient. |
| Wider independent evaluation and targeted extraction-quality work | Addresses names, IDs, receipt role, segmentation and availability with valid evidence. | Separately budgeted; dataset/lab adjudication and provider access dependent. | Frozen scorer/baselines, separated development/holdout; no tuning in this task. |
| Optional OCR/hybrid integration or alternate provider | Only useful if new paired evidence shows enough benefit to offset complexity. | Unestimated until justified; explicitly not a demo prerequisite. | Preserved Task 9/10 evidence/components; no automatic promotion or architecture rewrite. |
| Reopening/supersession, audit operations and deployment protections | Supports authenticated multi-user operation and lawful lifecycle/recovery. | Multi-day; separate approved workflow/storage versions. | Append-only review patterns, deployment/laboratory decisions. |

## J. Exactly one recommended next implementation task

**Task 11B - implement a local seven-field reviewed-record-to-Excel vertical slice.**

Inputs: a bound immutable canonical extraction snapshot from the current upload
response or explicitly labeled preserved run, its source document reference/hash
and explicit operator review actions. Outputs: one separate reviewed record per
logical form, a private inspectable audit bundle and a real `.xlsx` workbook.
Provide a small local operator command to exercise the capability immediately.
No new Gemini call is needed to implement or validate this slice.

Exact initial touch points: NEW `backend/services/business_review.py`,
`backend/services/excel_export.py`, `backend/tools/review_export.py`,
`tests/test_business_review.py`, `tests/test_excel_export.py`,
`tests/test_review_export.py`; UPDATE `pyproject.toml` only for one justified XLSX
writer, `README.md` for local usage, and this analysis with actual completion limits.
Do not modify canonical models, provider adapter/prompt, scorer, ground truth,
OCR gate, review persistence or authentication to complete this task.

Acceptance: all seven fields supported; original canonical bytes/values unchanged;
explicit whole-list correction/approval/blank/unresolved semantics; wrong-scope and
stale/conflicting actions rejected; all forms exported once; failures separate;
source/provider/human provenance preserved; safe exact text and multi-values verified
by reopening XLSX. Run focused and full offline tests plus dependency/syntax/diff
checks when implemented. No assertion of automatic acceptance or live quality.

This task has the highest hackathon value because it converts already available
extractions into the missing business deliverable and makes manual recovery of
weak names/IDs possible. It can be tested independently of unstable provider access
and does not spend another task on infrastructure. Browser upload/review/download
connection remains the subsequent P0 connection, not an excuse to call the CLI a
completed web product. No implementation task is started by this analysis.

## Task 11A validation and Git state

Only `docs/mvp_gap_analysis.md` is created. Reviewed source paths/local links,
UTF-8/newline/whitespace, credential markers and `git diff --check`; tracked code,
tests, dependencies/configuration and prior evidence remain unchanged. No pytest
or dependency/runtime test is claimed for this design-only change. The preceding
checkpoint's 473 passing tests are historical, not re-executed in Task 11A.

Branch/HEAD remain as recorded above. This document remains untracked alongside
the two pre-existing source challenge PDFs. Nothing staged, committed or pushed.
`.env` and private evaluation artifacts remain ignored. No database operation,
package installation, OCR run, external AI/API call or further experiment occurred.

## Task 11B implementation and remaining MVP gap

Implemented the local/private business slice, without HTTP/UI wiring:

```text
existing canonical snapshot + explicit source binding + seven-field human actions
  -> detached canonical snapshot and scope/revision/replay validation
  -> immutable reviewed business records + append-preserving action audit
  -> Forms / ProcessingFailures / ReviewAudit XLSX + private result JSON bundle
```

`backend/services/business_review.py` provides frozen `BusinessReviewAction`,
`ReviewedField`, `ReviewedBusinessRecord`, `BusinessReviewAuditEntry` and
`BusinessReviewResult`, plus `SourceBinding` and detached original-field/value
snapshots. All seven fields are supported. The pure apply operation handles one
selected form; the operator iterates every actual canonical form without changing
provider IDs. Results retain full immutable canonical JSON and its deterministic
UTF-8 hash; raw/normalized values, optional evidence and nulls remain available.

Actions: whole-list `approve_as_extracted`, explicit `correct_and_approve`,
`acknowledge_blank`, `reject`, `leave_unresolved`. Whole raw lists, ordering,
punctuation, zeros, Unicode and duplicates are retained. Corrections require a
nonempty list; scalar approvals require exactly one value. Blank acknowledgment
is separate from missing/unreadable/rejected. Tentative unresolved corrections
stay audit-only. Record `complete` requires a terminal human disposition on ALL
seven fields, not successful OCR state or a model confidence threshold. Rejected,
unreviewed and unresolved fields keep the record unresolved but still exportable.
Identifier pairing is not inferred; relationship status remains unresolved.

Decision IDs/revisions are per form. Duplicated IDs within one input batch fail;
identical previously accepted actions replay before revision checks. Conflicting
replays, stale new actions, changed originals and cross-source/snapshot/form input
fail atomically. Later explicit actions may resolve a rejected/unresolved business
field while preserving its earlier audit entry; the OCR one-action gate is unchanged.
Stored result projections are reconstructed/validated before continuation/export.
These are consistency checks, not authenticated authority or durable concurrency.

`backend/services/excel_export.py` uses **XlsxWriter 3.2.9** (declared
`>=3.2.9,<4`), the one newly installed package with no transitives/unrelated upgrades.
Python 3.14 support was checked in official metadata before dependency edits and
installation; actual tests ran on Python 3.14.2. Workbooks are reopened through
standard-library ZIP/XML inspection, not a second spreadsheet package.

The exact three-sheet/column contract and private JSON input/action formats are
documented in the [README](../README.md#local-seven-field-review-and-excel-export-task-11b).
Every form appears exactly once; an incomplete/duplicate supplied form set fails.
Failures appear only in their own sheet, never fabricated commitment rows. Multi-
value fields are exact JSON arrays in one text cell; no Cartesian product or
normalized substitution occurs. In accordance with Task 11B, unresolved/rejected/
unreviewed business cells are blank (multi-value `[]`); this supersedes this
analysis's earlier optional unverified-candidate display proposal. Original data
remains in the mandatory private result bundle and in every explicit action's audit.

Every workbook cell is written as literal text with formula/URL/number detection
disabled. Formula-like and whitespace-prefixed strings remain exact without added
display prefixes or executable formulas/hyperlinks/external links. Unsafe XML
controls/surrogates and values exceeding 32,767 UTF-16 units fail before output,
including JSON/audit cells; no truncation. Tests reopen the actual workbook and
inspect cell types, values, relationships, headers, counts, escaping and arrays.

`backend/tools/review_export.py` validates actual source bytes/hash plus canonical
snapshot/extraction/form binding and strict action JSON, then emits a new workbook
and private `.review.json` bundle with its SHA-256. Source metadata explicitly pairs
`preserved` with `historical_replay`, or `live` with `live`; no origin is inferred.
These labels/operator identities are declarations, not authenticated provenance.
Output paths must be explicit and new; inputs/existing outputs are never overwritten.
Ordinary write failure rolls back only files created by that invocation. A process
crash can leave a partial output pair; no durable transaction is claimed. Summaries
contain counts/hash only; errors never echo patient/reviewer text, paths or SQL.

Validation:

- New business-review/export/operator tests: **105 passed**. Full regression:
  **578 passed**, **298 existing dependency deprecation warnings**, with all existing
  PostgreSQL integration tests executed against a disposable isolated local DB.
  Existing canonical/extraction/provider and OCR/review/workflow/API tests are
  included in that full run. No DB skips or real provider/OCR invocations.
- A real module CLI invocation on synthetic private files produced two form rows:
  one complete, one unresolved, seven audit actions and a matching workbook hash.
- Offline preserved-data check: five successful Task 7 outputs exported unresolved,
  plus three separate processing failures, zero human actions/approvals. Original
  provider scores stayed **16/32** and canonical objects/baseline bytes unchanged.
  This checks preservation, not new extraction/review accuracy. Scoring used the
  existing canonical `exclude_none=True` transport serialization after reconstructing
  the null-preserving internal snapshot; neither scorer nor normalization changed.
- `pip check`, syntax/import, changed-file whitespace/credential-marker and
  `git diff --check` checks passed. No configured formatter/linter/type checker
  exists in `pyproject.toml`; none was installed or claimed run.
- Before/after SHA-256 checks matched **117 protected files**, including Gemini,
  orchestrator, canonical/domain contracts, prompt, scorer, frozen ground truth,
  source/fixture PDFs, OCR review logic, PostgreSQL persistence and review API/auth.
  Only the authorized README/dependency edits were excluded from that snapshot.
- Temporary PostgreSQL test container and generated credential file were removed.
  Private validation evidence/test outputs live only under the explicitly chosen,
  ignored `evaluation/artifacts/task11b/`; the operator has no default path there.

Project files created: `backend/services/business_review.py`,
`backend/services/excel_export.py`, `backend/tools/review_export.py`,
`tests/test_business_review.py`, `tests/test_excel_export.py`,
`tests/test_review_export.py`. Updated: `README.md`, `pyproject.toml` and this
pre-existing untracked analysis. No other application files, public contracts,
schemas, existing tests, configuration or migrations changed.

Branch remains `feat/backend-foundation`, HEAD
`516ab5c481c2dcbdb954dbce8007147604f7087c`. No staging/commit/push. Two original
challenge PDFs remain untracked/unchanged; `.env` and private artifacts stay ignored.

Remaining gap: extraction/upload still returns JSON independently. No browser
upload/review/download, durable business-review store, production authentication,
runtime OCR, segmentation or automatic field acceptance was added. The operator
must inspect and bind its own source; output privacy depends on its chosen local
directory/access controls. Existing review API defaults remain fail-closed.

ONE recommended next task: implement a bounded local upload -> seven-field review
-> XLSX download wrapper over this business slice, with server-owned source/result
binding and explicit local access/lifetime controls. Reuse existing extraction;
do not add OCR, production IdP/ACL infrastructure or business-review persistence to
that task merely to connect the demo. No such wrapper is started here.
