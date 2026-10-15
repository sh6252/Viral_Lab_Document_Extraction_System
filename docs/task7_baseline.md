# Task 7 — first challenge-document baseline

Current integration note: the subsequent provider cleanup removed the active
OpenAI adapter, configuration, dependency declaration and CLI selection. Gemini
remains selectable through the shared registry. All OpenAI references, commands,
SDK versions and statements about installed/implemented support below are
**historical Task 7 evidence**, not current setup instructions. Private artifacts
and baseline results remain unchanged; old OpenAI launchers are preserved as
historical evidence and are no longer supported by the active application.


Status: measurement-only identity association and bounded transient retries are
implemented. All eight selected forms have been attempted across the preserved
runs: five scored, three failed after their allowed attempts. Successful-response
field accuracy is 16/32 (50%); end-to-end exact-match yield is 16/49 (32.65%).
The first cached form scores 4/7 after deterministic scoring-only ID association.
There is still no successful extraction baseline for all eight forms.
Do not interpret synthetic tests as challenge-document accuracy. No commit is authorized.

## Fixed selection and provenance

The user explicitly approved the following eight pages for OpenAI, confirmed them
as demo data without real PHI, and classified this selection as **development** data.
The original PDFs remain unchanged in `evaluation/fixtures/holdout`; their folder
location does not classify these selected copies as frozen holdout fixtures.
The remaining 40 pages must not be transmitted or used for tuning in this run.

- `התחייבויות - דוגמאות, חלק 1 (PDF).pdf`: pages **1, 10, 14, 17, 19, 24** (one-based).
- `התחייבויות - דוגמאות, חלק 2 (PDF).pdf`: pages **14, 20** (one-based).

Selection rationale, fixed before viewing any model output:

- S1/P1: printed Meuhedet commitment, central sticker, received stamp, leading zeros.
- S1/P10: printed Maccabi commitment with handwritten central number and date;
  conflicting visible identifier variants are retained as uncertainty.
- S1/P14: sideways Tzafon/Poriya hospital form, two central stickers, received stamp.
  The old visual survey incorrectly identified this page as Soroka.
- S1/P17: Rosenblatt laboratory form with handwriting and a faint, overlaid stamp.
- S1/P19: Rambam hospital form with patient sticker and handwritten central/date
  annotations; printed text explicitly links sample and commitment numbers.
- S1/P24: National Center of Forensic Medicine form with two central stickers,
  handwriting, competing printed dates and a received stamp.
- S2/P14: sideways Laniado hospital form with Leumit information and an unlabelled
  highlighted barcode; sender precedence and commitment association remain uncertain.
- S2/P20: sideways information-system screenshot with two commitment rows and an
  incomplete handwritten receipt date; no unambiguous originating sender.

All selected pages have no embedded text layer. Model input consists of single-page
PDF copies preserving the original raster and orientation. Rotated/enlarged preview
images were used only for manual annotation and are not model preprocessing.

Original SHA-256:

- Part 1, 26 pages: `f5eab18196a1007c1512a88d9ed6210e8244ec2c25b38faea9f10ae162c021b8`
- Part 2, 22 pages: `6aa58871b814e086c4f4a7a97ab6f06b1960e8d2f94a390d403ca732deb4a779`

## Ground truth and freeze

Private local artifacts live under the already ignored `evaluation/artifacts/task7/`:

- `manifest.json`: exact source pages, source/fixture/truth hashes, selection tags,
  dataset split and the conversation's data/provider approval.
- `fixtures/challenge-sN-pNN/`: unchanged-orientation page PDF and `ground_truth.json`.
- `previews/`: local inspection images.
- `prepare_fixtures.py`: initial manual annotation record; refuses to overwrite frozen PDFs.
- `run-001.json`: original live attempt record, preserved unchanged.
- `run-001.analysis.json`: interpretation of the quota failure and partial coverage.

There are **8 forms, 56 field annotations, 49 scorable fields** under the existing
scorer. Annotations consist of 46 verified-present, 3 verified-absent, 4 verified-
ambiguous, 1 verified-unreadable and 2 unverified fields. Uncertain fields are not
silently counted as absent or forced into exact-match accuracy. Annotations are
from one manual visual inspection, not laboratory adjudication; user/lab acceptance
remains necessary. They were frozen before any live extraction.

The task's `national_id`, `central_number(s)` and `commitment_number(s)` map to
the existing schema names `israeli_id`, `central_numbers`, `commitment_numbers`.
Identifier strings retain observed leading zeros, including deliberately nonstandard
demo lengths. Dates retain raw text and use an ISO normalized value only when the
full date is sufficiently readable. No sender synonym mapping or new normalization
rules were introduced.

## Environment and repeatable command

Python 3.14 environment. Installed local versions: OpenAI **2.54.0**, PyMuPDF
**1.28.2**, python-dotenv **1.2.4**. OpenAI satisfies the existing project requirement
`openai>=1.99,<3`; PyMuPDF is a local fixture-inspection tool. `pyproject.toml` was
not changed. Compatibility with the actual installed SDK is tested through
`httpx.MockTransport`; that exercises serialization/parsing without network access.

Before live use, configure `.env` locally (never paste a key into chat):

```dotenv
OPENAI_API_KEY=<local-key>
OPENAI_MODEL=gpt-5.6-sol
AI_OUTBOUND_ENABLED=true
ALLOW_EXTERNAL_PHI=true
APPROVED_AI_PROVIDERS=openai
DEFAULT_EXTRACTION_ADAPTER=openai
```

The prepared local launcher explicitly loads `.env` without overriding existing
process settings, then executes this exact measurement:

```powershell
.\.venv\Scripts\python.exe -B evaluation/artifacts/task7/run_openai.py
```

With the environment already configured, the equivalent direct command is:

```powershell
.\.venv\Scripts\python.exe -B -m backend.tools.extraction_baseline `
  --manifest evaluation/artifacts/task7/manifest.json `
  --output evaluation/artifacts/task7/run-001.json `
  --provider openai `
  --authorize-external-processing
```

The runner validates all frozen checksums and policy gates before provider creation.
It refuses an existing output path, checkpoints after each document, records safe
adapter attempt/retry observations and latency, and saves canonical extraction
output, not raw SDK response bodies. Provider form IDs and all business values stay
unchanged in that artifact. The later measurement-only association uses a deep
scoring copy for a single-form/single-page fixture, as detailed below. Failed
extraction remains a failure without a fabricated business record. Summary separates
successful-response accuracy from end-to-end accuracy over selected scorable fields.

## Measurement status and limits — original OpenAI attempt

Provider/model actually requested live: **OpenAI / `gpt-5.6-sol`**, as configured by
the user. The configured key was detected without being printed. The user then
explicitly authorized updating the local `.env` gates to enable outbound use,
allow the existing PHI policy boundary and approve only OpenAI. Repository defaults
and provider policy code are unchanged.

Exactly **one** adapter/API attempt was made, for **Part 1, page 1**
(`challenge-s1-p01`). That selected page PDF was transmitted to OpenAI. OpenAI
returned **HTTP 429, `insufficient_quota`**. The adapter classified it as a permanent
quota failure (`PROVIDER_RATE_LIMIT`, `retryable=false`), with **no retry**. The runner
stopped immediately; the remaining seven selected PDFs were not transmitted.

Total first-document latency was **8.633255 seconds**; the observed provider call
occupied **5.363183 seconds**. No canonical extraction result or usage data was
returned. Token usage, charge, OCR behavior and model reasoning cannot be inferred.

Coverage: **8 selected forms / 1 attempted / 0 scored / 7 not attempted**.
There are **49 selected scorable fields**, **7 on the attempted form**, and **0
actually scored fields**. Field accuracy, per-field/document extraction scores,
strongest/weakest fields, hallucinations and multi-value recall are **not available**.
They must not be reported as 0% accuracy or zero hallucinations.

The original run artifact contains a provisional `end_to_end_exact_match_rate=0.0`
from the first measurement-runner version. This is not a model accuracy result:
the run stopped early and seven pages were never attempted. The original artifact
was not edited. Its interpretation is recorded separately in `run-001.analysis.json`;
the runner now reports this aggregate as `null` for an incomplete selection and
records attempted-field coverage separately. A synthetic regression test covers it.

Error analysis: the only observed failure is **provider/account quota**, before
any extraction output. There is no evidence yet to attribute errors to handwriting,
rotation, stickers, field association, normalization or hallucination. All eight
ground-truth files and their pre-run frozen hashes remain unchanged.

Known measurement risks are documented before the run: arbitrary provider form IDs
may fail matching in the unchanged scorer; date formatting and sender synonyms may
produce exact-match differences; some selected fields are genuinely unconfirmed.
These are hypotheses, not observed model errors. Do not remap IDs or revise truth
after seeing output to improve the baseline. The scorer prefers normalized values
when supplied and does not expose an independent raw-preservation rate.

Immediate prerequisite: restore quota and complete Task 7 using the same approved
selection and a new result path; do not overwrite `run-001.json`. The single
evidence-supported operational improvement proposed for Task 8 is a **synthetic
provider-readiness check before a document batch**, so an account/quota failure is
found before transmitting document data. No model/prompt quality improvement can
be prioritized from this failed attempt. No Task 8 implementation is included.

## Validation and repository state

- Final full suite: `python -B -m pytest -q -p no:cacheprovider` — **185 passed**,
  with 56 dependency deprecation warnings. Ten new synthetic measurement/SDK tests
  pass, including early quota termination and the incomplete-selection denominator.
- `python -B -m pip check` — no broken requirements.
- Default and needs-review offline smoke runs succeed; these are synthetic checks.
- Ground-truth schema validation, frozen selected-PDF/truth hashes and original
  source hashes pass for all eight selected pages.
- Python AST syntax and new-file whitespace checks pass. `git diff --check` has no
  tracked whitespace errors; Git reports only the existing LF/CRLF conversion warnings.
- An initial local preflight correctly stopped at `PROVIDER_NOT_AUTHORIZED` before
  authorization was configured. After the user authorized local policy changes,
  the real live request reached OpenAI and failed on quota as documented above.
- Branch remains `feat/backend-foundation`, HEAD remains
  `236124cc49d3813525cb4ee51b1d9ab862504ed1`. No staging or commit was performed.

Reviewable additions: `backend/tools/extraction_baseline.py`,
`tests/test_extraction_baseline.py`, and this report. The two user-provided source
PDFs remain untracked and unchanged. Private generated annotations, PDFs, previews
and local scripts are ignored; `.env` is ignored and must never be included in a
commit. No existing domain, provider, orchestrator, scorer, schema, prompt, or HTTP
contract was changed in Task 7. The ignored `.env` was updated only at the user's
explicit request; its model and secret key were preserved.

## Gemini switch and first attempt

At the user's explicit request, the existing Task 7 sample was authorized for
Gemini using a separate `evaluation/artifacts/task7/manifest.gemini.json`. The
original OpenAI manifest/run and all selected PDF/ground-truth hashes are preserved.
No architecture, adapter extraction logic, prompt, scorer or schema was changed.
OpenAI remains installed and implemented.

`.env` loaded successfully. The configured provider was `gemini`, model
`gemini-2.5-flash`; the key was present without being printed. `ProviderPolicy`
allowed the request with outbound access, PHI boundary and Gemini approval enabled.
The local measurement launcher constructs the existing `GeminiExtractionAdapter`
with `max_retries=0` and SDK `HttpRetryOptions(attempts=1)`. The existing
`ExtractionOrchestrator` executes the extraction. A factory injection point and
Gemini CLI choice were added only to the measurement tool, and its batch stop
conditions now include invalid requests and unavailable model/resources.

Exact command:

```powershell
.\.venv\Scripts\python.exe -B evaluation/artifacts/task7/run_gemini.py
```

Result artifact: `evaluation/artifacts/task7/run-gemini-001.json` (private/ignored).
Only **Part 1, page 1** was attempted/transmitted to Gemini. The provider returned
**HTTP 404, `NOT_FOUND`**, classified as `PROVIDER_RESOURCE_UNAVAILABLE` and
`retryable=false`: the configured resource/model was unavailable for the request.
The sanitized result does not establish whether model availability or endpoint
compatibility caused the 404; it does not establish a quota or invalid-key error.
No subsequent API/list-model/fallback requests were made. Seven selected pages
were not sent to Gemini.

One attempt, no retry. Total latency **4.255200 seconds**, observed provider-call
latency **3.555399 seconds**. **8 selected / 1 attempted / 0 scored / 7 unattempted**;
**49 selected scorable fields / 7 attempted scorable fields / 0 actually scored**.
Accuracy for each of the seven business fields, multi-value metrics, hallucinations
and provider review behavior are **not available**, not 0%. The seven pre-existing
uncertain ground-truth fields remain unchanged. There is no extraction output to
analyze for visual, normalization or attribution failures. Task 8 is not implemented,
and no commit is authorized.

Post-switch validation: **187 tests passed**, with 56 dependency deprecation
warnings; `pip check`, Python syntax/whitespace checks and `git diff --check` pass.
All source/sample/truth hashes and the original OpenAI run/manifest remain unchanged.

## Updated Gemini model: gated run 002

The user updated `.env` to **`gemini-3.8-flash`** and requested testing only the
first form before continuing, with no retry after any provider error. `.env` was
loaded and `ProviderPolicy` allowed Gemini. No key was printed. All eight frozen
sample/ground-truth hashes were revalidated without changing annotations.

Exact command:

```powershell
.\.venv\Scripts\python.exe -B evaluation/artifacts/task7/run_gemini_002.py
```

The local measurement script uses the existing adapter/orchestrator and existing
one-shot client factory. It writes per-form subset manifests/runs under the ignored
`evaluation/artifacts/task7/run-gemini-002/` and checkpoints `baseline.json`. A
successful first result is retained in the full selection, not sent a second time.
Any failed result stops the run, including transient provider failures.

Observed outcomes:

- **Part 1/page 1**: canonical extraction succeeded; one attempt, no retry.
  Total latency **38.572229 s**, provider-call latency **38.051858 s**.
- **Part 1/page 10**: **HTTP 503 / `UNAVAILABLE`**, mapped to `PROVIDER_UNAVAILABLE`.
  Total latency **8.882906 s**, provider-call latency **8.230227 s**. Although the
  error category is marked retryable, this execution performed **no retry** and
  stopped immediately as requested.
- Remaining six forms were not transmitted in this run. There were exactly two
  provider calls, with two approved page PDFs transmitted to Gemini.

Coverage: **8 selected / 2 attempted / 1 scored / 1 provider failure / 6 unattempted**.
Only the first form's **7 fields** were actually scored; the full planned selection
contains 49 scorable fields. There is no complete eight-form accuracy result.

Unmodified scorer output for the first form: **0/7 exact matches, 7 missing values,
0 counted false-positive values**. Every field (`first_name`, `last_name`,
`israeli_id`, `central_numbers`, `sample_receipt_date`, `sender`,
`commitment_numbers`) has **0/1 exact matches**. Central/commitment recall is **0**;
precision and F1 are undefined. Logical-form count matches (one vs one), but
logical-form ID matching/detection recall is zero.

The primary reason is **form identity**, not established failure to read every
field: Gemini returned `form_id="1"`, while frozen truth uses
`challenge-s1-p01`. The unchanged scorer does not match these and treats all truth
fields as unpredicted. The exact canonical output and ground truth were not remapped
or edited to improve the score. The six `present` provider fields therefore must
not be described as independently validated by this raw score. Likewise the zero
false-positive count does **not** establish no hallucinations, because fields in
the unmatched provider form are not compared by this scorer.

Independent visible output status: `central_numbers` is `missing` with
`needs_review` and no values, even though the manual truth includes a central
sticker. The other six fields are `present` and `unreviewed`; no field was approved
by the adapter. The existing silent-accepted-error count is zero, with no accepted
fields and an undefined rate. The second document has no extraction output and is
kept as a processing failure, not a fabricated row.

Error categories observed: **form identity/page association**, **missing central
sticker value**, and **temporary provider unavailability**. No prompt, scorer,
ground truth, adapter extraction logic, domain contract or architecture was changed.
No fallback, additional API request, Task 8 implementation or commit followed the
503. Full suite: **187 passed**, 56 dependency warnings; `pip check` passed.

## Measurement-only corrections and resumed execution

At the user's request, only `backend/tools/extraction_baseline.py` and its tests
were changed, with documentation and ignored local launchers/artifacts. Extraction
adapters, prompts, domain models, schemas, ground truth and `evaluation/scoring.py`
remain unchanged.

### Deterministic association

`score_fixture` accepts only exactly one returned form for a frozen fixture whose
truth declares one logical form on one page. The scoring logical ID is taken from
the logical-form metadata in the ground-truth file whose SHA-256 is pinned by the
unchanged manifest. Extracted business values never participate in association.
Only the form ID in a deep scoring copy changes. `extraction_result` is preserved;
each document records `provider_form_id`, `scoring_logical_form_id`, the rule name,
the frozen truth hash and association status. Zero/multiple returned forms or a
multi-page fixture are identity/structure failures and are not forcibly scored.

For the cached first document, provider ID **`1`** is associated with scoring ID
**`challenge-s1-p01`**. Its score changes from **0/7** to **4/7 (57.14%)**, without
another API request or a changed value. First-document comparison:

- `first_name`: present but not an exact match; one missing expected value and one false positive.
- `last_name`: present but not an exact match; one missing expected value and one false positive.
- `israeli_id`: exact match, including observed leading zeros.
- `central_numbers`: missing, `needs_review`; no extracted value for the sticker.
- `sample_receipt_date`: exact match under the existing normalized-value comparison.
- `sender`: exact match.
- `commitment_numbers`: exact match; precision/recall/F1 are 1.0 for this one document.

There are three missing expected values and two counted false-positive values on
the first document. Missing-value counts include wrong substitutions, not only
blank fields. Six fields being present does not imply six correct fields.

### Bounded transient retries and resume

The Task 7 harness permits at most **two retries**, with delays of **1 s and 2 s**.
It retries only retryable timeout/connection errors and explicit transient HTTP
statuses (408, 500, 502, 503, 504). Quota/rate-limit, auth, invalid request, NOT_FOUND,
permanent and unknown errors are not retried. The existing local Gemini factory
keeps adapter retries at zero and SDK attempts at one, preventing nested retries.
Every measurement attempt records safe error diagnostics and latency. An exhausted
transient document remains failed while the batch proceeds to untouched documents;
a permanent account/configuration failure stops the batch.

Before reusing a success, the harness checks sample manifest hash, provider/model,
prompt/pipeline versions, source metadata, extraction ID and canonical schema. The
old artifact is immutable and its path/hash is recorded. No successful document is
resent. The first resumption command was:

```powershell
.\.venv\Scripts\python.exe -B evaluation/artifacts/task7/run_gemini_003.py
```

Part 1/page 1 was reused and rescored; page 10 succeeded in one new attempt.
Page 14 returned HTTP 504 / `DEADLINE_EXCEEDED` on all three allowed attempts,
with 1 s and 2 s backoff. Its total document latency was **188.334621 s**; it was
not submitted again. A continuation selects only the five untouched fixtures,
preserving both successes and the exhausted failure:

```powershell
.\.venv\Scripts\python.exe -B evaluation/artifacts/task7/run_gemini_004.py
```

The final consolidated artifact is `evaluation/artifacts/task7/baseline-gemini-final.json`.
Original per-run artifacts are retained and referenced by SHA-256. Final metrics
and coverage are recorded after execution below.

### Final observed baseline

Provider/model: **Gemini / `gemini-3.8-flash`**. `.env` was loaded without
overriding existing process settings, the configured key was present, and existing
`ProviderPolicy` gates allowed the approved demo selection. No secret was printed,
and `.env` was not changed during this continuation.

Coverage: **8 selected / 8 attempted / 5 scored / 3 provider failures /
0 unattempted**. Part 1/page 1 reused its original successful canonical output;
only the seven other selected page PDFs were transmitted in this continuation.
The remaining 40 source pages were not sent. All five successful results had one
form and were associated deterministically; there were zero identity/structure
failures. Their canonical provider form IDs and business values remain unchanged.

Successful-response accuracy is **16/32 = 50%** over the five scored forms.
Three uncertain/unverified annotations on those forms are excluded by the existing
scorer. Across the full planned selection, **16/49 = 32.65%** is end-to-end
exact-match yield, including provider failures in the denominator. It is not
conditional model accuracy. The 17 scorable fields on failed documents have no
extraction output and are not included in the successful-response field scores.

Per-field exact matches among successfully extracted forms (full-selection
end-to-end numerator/denominator in parentheses):

- `first_name`: **0/5, 0%** (0/8); 5 missing expected values, 1 false positive.
- `last_name`: **0/5, 0%** (0/8); 5 missing expected values, 1 false positive.
- `israeli_id`: **3/4, 75%** (3/7); 1 missing expected value, 1 false positive.
- `central_numbers`: **2/4, 50%** (2/7); 2 missing expected values, 1 false positive.
- `sample_receipt_date`: **2/4, 50%** (2/6); 2 missing expected values, 0 false positives.
- `sender`: **4/5, 80%** (4/6); 1 missing expected value, 1 false positive.
- `commitment_numbers`: **5/5, 100%** (5/7); 0 missing expected values, 0 false positives.

For multi-value fields on scored documents, sums of the unchanged scorer's value
counts give central-number precision **3/4 = 75%**, recall **3/5 = 60%**, F1
**66.67%**. Commitment-number precision/recall/F1 are **100%**, based on only
three expected/predicted values. The other two exact commitment-field matches are
verified absence, not additional successfully read numbers. These small counts do
not establish production accuracy or performance on the three failed forms.

Observed discrepancies: **16 missing expected values and 5 false-positive values**.
Eleven scorable fields have no extracted value; five have a wrong/incomplete value
set. The first form's two names differ, page 10's sender differs, page 19's central
number differs, and page 24's Israeli ID differs. False positives include
substitutions and do not by themselves prove a value was fabricated rather than
misread or attributed incorrectly. No adjustment to truth, sender synonyms or
normalization was made after observing these discrepancies.

There are **14 fields explicitly marked `needs_review`**, distributed across all
five successful forms: page 1 (1), page 10 (3), page 17 (4), page 19 (2), page 24
(4). Two of these correctly empty commitment fields are marked missing/review,
despite verified absence in truth. Of two uncertain verified annotations observed
on successful forms, one was flagged for review and one was returned present and
unreviewed; an additional unverified annotation is excluded. None of the fields
was approved. The silent-accepted-error count is zero with an undefined accepted
error rate; that is not proof of safe unattended processing. The three processing
failures require separate operational handling and have no fabricated rows.

Attempts and document latency in this continuation:

- S1/P1: cached; no new call (historical latency 38.572229 s).
- S1/P10: 1 attempt, success; **31.324216 s**.
- S1/P14: 3 attempts, HTTP **504 / `DEADLINE_EXCEEDED`** each; **188.334621 s**, failed.
- S1/P17: 1 attempt, success; **26.078121 s**.
- S1/P19: HTTP **503 / `UNAVAILABLE`**, then success on attempt 2; **28.239750 s**.
- S1/P24: 1 attempt, success; **26.729115 s**.
- S2/P14: 3 attempts, HTTP **504 / `DEADLINE_EXCEEDED`** each; **185.994840 s**, failed.
- S2/P20: 3 attempts, HTTP **503, 504, 503** (`UNAVAILABLE`, `DEADLINE_EXCEEDED`,
  `UNAVAILABLE`); **71.428293 s**, failed.

Total: **14 new provider attempts, 7 retries**, and **558.128956 s** summed new
document latency including backoff. This excludes the reused first result and the
earlier historical attempts described above. Each document received at most three
attempts in this continuation; successful documents and the exhausted S1/P14 were
not resent. No quota/auth/permanent failure was retried. No further live requests
followed the final bounded batch.

### Final validation and changed files

- Full suite after the final harness change:
  `python -B -m pytest -q -p no:cacheprovider` — **203 passed**, 56 dependency
  deprecation warnings. Targeted harness suite: **28 passed**. Tests mock external
  calls and cover scoring-copy immutability, value-independent identity association,
  zero/multiple forms, multi-page rejection, bounded backoff, permanent/quota
  no-retry behavior, safe cache reuse and continuation after transient exhaustion.
- `python -B -m pip check` passed. No packages were installed. No formatter,
  linter or static type checker is configured in `pyproject.toml`; standalone
  Ruff/mypy are not installed, so those checks were not run.
- Frozen manifest, all eight PDF/truth hashes and ground-truth schemas, both
  original source PDF hashes, previous run-002 artifact, original OpenAI
  manifest/run, and all recorded core-file hashes passed verification.
- The cached first canonical payload equals its original run-002 payload exactly.
  The consolidated artifact has eight unique fixture IDs. Results and redacted
  analysis remain ignored/private under `evaluation/artifacts/task7/`.
- Final Python AST syntax, changed-file trailing-whitespace and credential-marker
  checks passed; `git diff --check` passed. These are limited static checks, not
  a substitute for an unavailable linter or type checker.
- Reviewable files changed: `backend/tools/extraction_baseline.py`,
  `tests/test_extraction_baseline.py`, `docs/task7_baseline.md`. Private additions:
  `run_gemini_003.py`, `run_gemini_004.py`, `analyze_run_003.py`, the preflight proof,
  the five-entry continuation manifest, immutable run-003/run-004 results and
  analyses, and `baseline-gemini-final.json` with its redacted analysis.
- Branch: `feat/backend-foundation`; HEAD remains
  `236124cc49d3813525cb4ee51b1d9ab862504ed1`. The three reviewable files remain
  untracked alongside the two pre-existing source PDFs. No tracked changes,
  staging, commit, push, merge or rebase. OpenAI remains implemented and installed.

The measurement correction is complete and the approved selection has full attempt
coverage. Three provider failures still prevent a fully successful eight-form
baseline. Any subsequent rerun or extraction-quality change needs a separately
authorized scope; Task 8 was not implemented.
