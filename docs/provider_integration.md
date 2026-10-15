# Provider integration — Task 6

The implementation extends the existing modular monolith with OpenAI and a small
provider registry. It does not implement the review/export work described as
**Day 6** in the architecture roadmap.

## Boundaries

- `backend/services/extraction/base.py`: provider-independent `ExtractionAdapter`
  protocol and byte-oriented `DocumentInput`.
- `registry.py`: the single provider construction map, used by startup and the
  smoke CLI. Selecting `local_ocr` currently yields no adapter; it does not run OCR.
- `gemini_adapter.py` and `openai_adapter.py`: SDK imports, document transport,
  response parsing, provider error classification and bounded retries.
- `canonical.py`: shared candidate mapping, field consistency and prevention of
  provider approval. Empty form lists, conflicting payload shapes, duplicate IDs
  and multiple forms without IDs are rejected instead of silently losing rows.
  A single form without an ID uses the request's logical form ID.
- `policy.py`: shared fail-closed outbound checks. Both implemented adapters check
  policy before constructing an SDK client or sending bytes. This is not a
  universal sandbox for arbitrary injected adapters; future adapters must follow
  the same contract and policy tests.

The orchestrator, domain models, evaluation schemas, scorer and HTTP contracts
are unchanged. Add a future provider by implementing the protocol, registering a
builder and adding its configuration and tests. Do not scatter provider conditions
through orchestration or domain code. PDF/image conversion, when required by a
future provider, belongs behind an appropriate adapter boundary; direct PDF
support is not guaranteed by the protocol.

## OpenAI request behavior

The adapter uses Responses API `input_file` with inline base64 for PDFs and
`input_image` for JPEG/PNG. It sends a neutral PDF filename, requests JSON Schema
output with `strict=False`, validates locally and preserves returned evidence.
Non-completed responses and refusals are rejected even if JSON is also present.
The SDK's internal retries are disabled; the adapter owns its bounded retry loop.
Known permanent quota failures are not retried. Missing SDKs return a safe,
non-retryable error. SDK imports remain lazy, allowing offline development.

Requests set `store=False`; this does not establish zero retention, regional
processing guarantees or approval for medical records. External access remains
disabled by default and requires both server-side and request-side authorization.
Error reports contain only generic messages and allowlisted diagnostic codes,
never SDK error bodies or document text. No automatic fallback sends a document
to a different provider.

Reference: [OpenAI file inputs](https://developers.openai.com/api/docs/guides/file-inputs).

## Evaluation and verification

Run `python -m pytest -q` for the full suite. Tests use synthetic data and fake
clients, including policy denial before client construction, upload limits,
timeouts, errors, refusal/incomplete responses, evidence, leading zeros,
multi-value fields, form isolation, factory selection and the HTTP/CLI paths.

`test_mock_development_evaluation_matches_gemini_baseline_without_mutating_truth`
compares Gemini and OpenAI canonical output using the existing labeled synthetic
development fixture. Field accuracy, multi-value recall, missing/false-positive
values, logical-form metrics and silent-error metrics agree for the controlled
responses. This tests contract parity, not live model accuracy. Ground truth and
the frozen holdout directory are unchanged; holdout currently contains no labeled
fixtures. A live quality comparison cannot be claimed from these tests.

The shared prompt version is `he-commitment-v1`; relative to the previous Gemini
prompt, only the version label changed, not extraction instructions. Smoke
reports record model, prompt and pipeline versions. The unchanged HTTP result
contract records model version only; durable attempt-level prompt/pipeline
metadata remains future persistence work. Stricter form validation intentionally
turns previously accepted ambiguous responses into explicit errors; its effect
on live provider failure rates has not been measured.

## Known limits and documentation differences

Live OpenAI SDK integration, actual PDF interpretation, quality, latency, quota
and deployment compatibility require a separately authorized verification. Offline
tests do not establish them. Structured output and direct PDF support depend on
the configured model. Evidence is optional and is preserved when returned, not
guaranteed for every field. Existing normalization and date prompt semantics were
not redesigned. Evaluation still requires stable form IDs and does not perform
automatic matching of arbitrary provider-generated IDs to ground-truth IDs.

`docs/architecture_decisions.md` describes the target `ExtractionResponse` with
candidates/status/errors. The current MVP API uses `ExtractionResult`; this task
preserves it and does not redefine the approved target. The documented policy
boundary before reading PHI is also broader than current behavior: uploads are
read/validated in memory before the adapters authorize outbound use. Authentication,
audit persistence, review, export and PostgreSQL jobs remain unimplemented.

No live provider calls or package installation are required by offline tests.
Run lint/type-check tools when available; their absence must be reported and must
not be represented as successful validation.
