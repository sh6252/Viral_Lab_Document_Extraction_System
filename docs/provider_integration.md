# Provider integration

The modular monolith uses a small provider registry and a shared extraction
contract. Gemini is the currently implemented live provider. The offline smoke
adapter remains available for synthetic evaluation.

## Boundaries

- `backend/services/extraction/base.py`: provider-independent `ExtractionAdapter`
  protocol and byte-oriented `DocumentInput`.
- `registry.py`: centralized adapter construction used by startup and tooling.
  `local_ocr`, `none` and an empty selection return no adapter; no local OCR engine
  is implemented. Unknown providers fail explicitly.
- `gemini_adapter.py`: SDK imports, document transport, response parsing, provider
  error classification and bounded adapter retries.
- `canonical.py`: shared candidate mapping, field consistency and prevention of
  provider approval. Empty form lists, conflicting payload shapes, duplicate IDs
  and multiple forms without IDs are rejected. A single form without an ID uses
  the request's logical form ID.
- `policy.py`: fail-closed outbound checks before provider client construction.
  Future adapters must follow the same contract and policy tests.
- `prompts.py`: shared extraction instructions and the current provider wrapper.

Select Gemini through `DEFAULT_EXTRACTION_ADAPTER=gemini`, with `GEMINI_API_KEY`
and `GEMINI_MODEL`. The repository default remains `local_ocr` and external access
is disabled until the server policy and request authorization explicitly allow it.
A key alone is insufficient. Startup does not load `.env` automatically.

The orchestrator, domain models, evaluation schemas, scorer and HTTP contracts
remain provider-independent. Add a future provider by implementing the protocol,
registering a builder and adding its configuration and tests. Keep provider choices
out of business logic. Direct PDF support is not guaranteed by the protocol;
provider-specific transport conversion belongs behind its adapter boundary.

## Verification and measurement

Run `python -m pytest -q`. Tests use synthetic data and fake clients, including
policy denial before client construction, upload limits, timeouts, error handling,
identifier preservation, multiple values, form isolation, canonical conversion,
HTTP contracts and registry selection. Shared contract tests remain in
`tests/test_provider_contracts.py`; registry extension uses a synthetic provider.
These tests do not establish live model accuracy.

The shared prompt version remains `he-commitment-v1`. Task 7's measurement harness
preserves canonical output, records model/prompt/pipeline versions, validates frozen
inputs and separately associates single-form/single-page fixtures for scoring.
Its bounded transient retries and successful-result reuse remain intact. The
existing scorer itself does not perform arbitrary form-ID association. Historical
measurements are preserved in `docs/task7_baseline.md` and ignored private artifacts;
old commands there describe past runs and may no longer be executable.

## Known limits

Live availability, quality, latency and quota require separately authorized
verification. Evidence is optional and preserved when returned, not guaranteed for
every field. Authentication, audit persistence, review, export and PostgreSQL jobs
remain unimplemented. Uploaded bytes are read and validated in memory before the
adapter authorizes outbound transmission. HTTP uses the existing `ExtractionResult`
contract rather than redefining the target architecture described in the ADRs.

No package installation or live provider calls are required by offline tests.

## Cleanup verification

After removing the retired provider integration, the complete offline suite passed
with **149 tests**, including shared canonical contracts and Task 7 measurements.
The targeted registry/smoke suite passed with **31 tests**; both `perfect` and
`needs_review` offline smoke CLI scenarios succeeded. `pip check`, Python syntax
and `git diff --check` passed. Dependency deprecation warnings remain (60 in the
full suite). No formatter or type checker is configured or installed for this run.

Startup was verified with only Gemini configuration and with imports of the
retired SDK blocked. A synthetic future provider can still be registered without
changing startup or business logic; unknown providers still fail explicitly.
The live baseline was not rerun. All private Task 7 artifacts, frozen inputs,
schemas, scorer, domain models, orchestrator, shared canonical conversion, shared
errors, provider policy and Gemini adapter retain their pre-cleanup hashes.
Measurement helper function bodies also remain unchanged; only baseline CLI
provider choices/default were updated. The shared prompt output/version is
unchanged; only unused retired-provider aliases were removed.

The local ignored `.env` lost only the retired provider's two variables and one
obsolete comment. Existing Gemini settings were preserved. The retired SDK may
still exist in the local virtual environment; it is no longer declared or imported
by the project. No uninstall, installation, external API call, staging or commit
was performed. Historical launchers remain evidence rather than supported commands.
