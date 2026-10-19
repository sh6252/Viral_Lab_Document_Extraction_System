# Experimental Gemini Structured Outputs

This capability is opt-in and disabled by default. It supplies a generation
schema through `response_json_schema` without changing prompts, transport,
temperature, retries, scoring, domain contracts or the validation pipeline.
It does not establish model availability, extraction accuracy or approval for
external processing. No dependency installation or live provider call was used
to implement it.

## Configuration

`AppConfig.gemini_structured_outputs_experimental` defaults to `False`.
`AppConfig.from_env()` reads `GEMINI_STRUCTURED_OUTPUTS_EXPERIMENTAL`; only the
existing boolean parser's explicit true values (`true`, `1`, `yes`, `on`, ignoring
case/outer whitespace) enable it. The example environment file keeps it false.
Setting the experimental option alone grants no permission to send documents:
the existing outbound, PHI, provider allowlist, credential and request-level
authorization gates still apply before client construction.

The SDK request retains `response_mime_type="application/json"` and
`temperature=0`. Only enabled requests additionally carry `response_json_schema`.
`response_schema` remains unset. Disabling the option produces the original
schema-free request. This implementation does not edit or load a real `.env`.

## Contract and reproducibility

Schema version: `gemini-provider-output-v1`.

Schema SHA-256:
`cd7ae6b8238a806924bb134a5ad6c2fe6306fbf59883432532ab4b9faf533664`.

`backend.services.extraction.gemini_schema` exports the version,
`build_gemini_response_schema()` and `gemini_response_schema_sha256()`. The hash
identifies the exact schema dictionary serialized as UTF-8 JSON with sorted keys,
compact separators, `ensure_ascii=False` and `allow_nan=False`. It excludes the
version label and is not a hash of document content, credentials or Python source.
Each builder call returns an independent dictionary. A pinned hash regression
requires contract/dependency-driven changes to be reviewed and versioned.

The schema derives from `ProviderPayload.model_json_schema()` and its existing
shared definitions. It selects the prompt's `logical_forms` envelope, requires
at least one form, and imposes no one-form or one-value maximum. Backend-owned
extraction/provider metadata stays outside the model-generated payload. The
legacy single-`fields` parser path remains unchanged.

The seven field properties retain their existing optional/nullable definitions.
Every returned non-null field object requires `value_status` and `values`, and
each value requires string `raw`. Omitted arrays and null arrays are invalid;
explicit empty arrays are valid structurally. No array default is inserted.
All five value statuses, Hebrew/identifier strings, optional normalization,
evidence, notes and arbitrary signal metadata are retained. Closed contract
objects retain `additionalProperties=false`. Provider review metadata may be
`unreviewed`, `needs_review`, `needs_clarification`, or null; terminal human review
decisions are excluded from generation and still rejected by canonicalization.
Signals, including model confidence, never approve a record.

Schema titles/defaults are removed to reduce generation overhead. `minLength`
is enforced locally because it is absent from the installed SDK's documented
`response_json_schema` subset. Required keys, string types, nullability, array
items, evidence constraints and closed-object rules remain. This is a generation
projection, not a replacement for the authoritative contracts.

## Validation and failures

The existing order remains: provider-payload Pydantic validation, canonicalization,
then final result-schema validation. SDK `response.parsed` is always passed through
the same local validator. SDK 1.75.0's dictionary-schema parsing performs JSON
decoding without validating the dictionary against the supplied schema.

No missing arrays are filled, no numeric identifiers are coerced, no incomplete
results are salvaged, and no schema-free fallback occurs. Malformed JSON/payloads
remain nonretryable `INVALID_PROVIDER_RESPONSE`. Existing canonical validation
still checks status/value consistency, distinct multi-form IDs, conflicting
representations and forbidden approvals. Existing final schema validation remains
mandatory. Ordinary provider retry policy remains unchanged; an eligible retry
retains the same schema. Unsupported-schema HTTP 400 is the existing nonretryable
`INVALID_PROVIDER_REQUEST`, with safe allowlisted diagnostics and no automatic
schema removal, prompt alteration or resubmission.

## Compatibility and evaluation limits

Offline SDK conversion was checked against installed `google-genai` 1.75.0.
`response_schema=ProviderPayload` cannot handle open-ended `signals` on this SDK's
Gemini API conversion path. `response_json_schema` forwards the proposed schema
without that conversion. The service's acceptance/enforcement of this schema for
`gemini-3.8-flash` is unverified. JSON Schema references, nullable unions and arbitrary
signal metadata require separate authorized feasibility verification. Local SDK
serialization is not proof of server support. No dependency/model change or
silent compatibility downgrade is part of this capability.

Structured generation may change content, omissions, usage and timing. It must
be a separately identified experiment condition; record the effective option,
schema version/hash, SDK version, model, prompt/pipeline versions and source/input
hashes. Existing result contracts and baseline-report formats are unchanged, so
an experiment's own manifest must record the new schema metadata. Historical
failures remain failures and do not become zero-percent extraction accuracy or
successful results after an offline schema replay.

All saved experiments remain untouched. Their frozen source/revision checks are
expected to block after this authorized source change; do not edit old hashes or
execution markers to make them pass under the new code.

## Minimal separately authorized live feasibility experiment

Prepare a new independent synthetic fixture with two logical forms, multiple
string identifiers including leading zeros and Hebrew, and an explicitly missing
field. Freeze the PDF, prompt, evidence and synthetic truth before any call. Use
the same B-style prompt/evidence in both conditions, with no prompt or OCR changes.

Run two independently authorized conditions, option off and option on, holding
model, credential binding, PDF/prompt bytes, temperature, timeout and retry policy
constant. Record schema version/hash and actual generation configuration; preserve
every response and failure. Stop explicitly on schema rejection. Do not retry or
reset historical B1 arms. No live run is authorized by this document.

Acceptance: the server accepts the request, required arrays/strings are present,
all local validation passes and multiple forms/values/warnings are retained.
Report contract validity, end-to-end yield, usage and latency separately from
field accuracy. Two calls establish feasibility only; they cannot estimate a
compliance rate or demonstrate an accuracy improvement. Model variability and
execution order remain confounders. The frozen prompt already contains JSON
examples; the SDK's advice against duplicating schemas in prompts is a limitation
to assess separately, not a reason to change the prompt in this experiment.

## Offline verification

Focused tests: `tests/test_gemini_schema.py` and
`tests/test_gemini_structured_outputs.py`. These use synthetic payloads, fake
clients and local SDK serialization, never provider calls. They cover the original
missing-array failure in both parsed/text response paths, contract preservation,
schema hashing, opt-in/default parity, policy denial, unchanged validation and
retry/no-fallback behavior.

Validation on 2026-10-19 with SDK 1.75.0 and Pydantic 2.14.1:

- Initial focused run: 122 passed; six further cases were added before regression.
- Final offline run of `tests`, frozen B1 003 paired tests and frozen C1 evidence
  tests: **829 passed, 36 skipped, zero failures**, in 41.54 seconds. All 128 new
  schema/adapter cases passed. The 36 real PostgreSQL integration cases were
  deliberately skipped: no disposable database connection was authorized for
  this offline task.
- The suite blocked DNS and outbound socket connections. Only Python's internal
  Windows socketpair loopback connection for async wakeup pipes was allowed.
  Provider calls used fake clients; no live SDK client/provider call was made.
- Mocked labeled synthetic evaluation produced identical canonical results and
  scores with the option off/on (7/7 exact fields). This verifies local parity,
  not extraction-model quality. A live model-quality comparison remains pending.
- Python syntax/compilation, whitespace checks and `git diff --check` passed.
  Ruff, Black, mypy and Pyright were unavailable; no dependencies were installed.
- Hashes matched for all 7,151 readable saved-artifact files, all fixture files,
  real `.env`, prompts and scorer, and all 33 pre-existing changed/untracked user
  files. Eight historical scratch directories were inaccessible to the audit;
  none were modified, deleted or used by tests.

Private JUnit results are under
`evaluation/reports/gemini-structured-outputs-001/junit.xml` (ignored, not committed).
