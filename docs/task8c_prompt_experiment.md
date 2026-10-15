# Task 8C ? controlled patient-name prompt experiment

Status: completed five-document attempt coverage, three scored responses and two
provider failures. No measured improvement in name accuracy. No commit authorized.
Closure: v2 was experimental and did not improve measured name accuracy.
The production/default shared prompt has been restored exactly to
`he-commitment-v1`. All experiment evidence is preserved; no further experiment
was run.

## Change isolation

Prompt version: `he-commitment-v1` -> `he-commitment-v2`, so the experiment is
explicitly distinguishable under the project's prompt-version tracking rule.
The only substantive prompt change is this one instruction:

> Extract the patient/examinee name literally in Hebrew from fields labeled '?? ????', '?? ?????', or '??' in the patient/insured details area, including handwritten text and identification stickers. Do not replace it with example names, English placeholders, transliteration, sender names, or clinician names. If the patient name cannot be read reliably, mark it unreadable rather than guessing.

All pre-existing instructions, including anti-guessing and status semantics, remain
identical. A rendered-prompt comparison passed after removing only the added line
and substituting the version label. The smoke metadata expectation was updated to
v2; no other test behavior or application logic changed.

Before prompt-file SHA-256: `f2d70e7bc222484d05ee52eeaf2298f0fe776d14ecedfae0f8dd7c44ac70b90d`.

After prompt-file SHA-256: `99652f14f13388e350b1ad21280da86ac4e818d3ec0a1ceee186f9b0b8fa9a30`.

Before rendered-prompt SHA-256: `205b2e94fb3d014b29cc89977a8cc09017753010ad07dfa38db6546e69073640`.

After rendered-prompt SHA-256: `8ae052d380f18ef614e9a7054c85c0b677018a5d216e4f865fc02188ca1f0ee9`.

Pre-edit hashes of Task 7 results and all tracked files were recorded in ignored
`evaluation/artifacts/task8c/preflight.json`. The original fixture PDFs and frozen
truth were reused, without rotation, cropping, preprocessing or annotation edits.
Provider/model: Gemini / gemini-3.8-flash. Existing adapter, orchestrator, scorer,
scoring-only single-form association and bounded transient retry policy were used.
Only S1/P1, P10, P17, P19 and P24 were submitted; the three earlier failed fixtures
were excluded. The private manifest is a subset of the unchanged Task 7 manifest.

## Per-document name comparison

Expected first/last name is **????? / ?????** for every selected form. An empty
value list means no returned raw or normalized value. `unreadable` entries require
review; present entries are unreviewed. No field is approved. Provider failures have
no field status or score and must not be counted as new abstentions.

| Fixture | BEFORE first: raw / normalized / status | BEFORE last: raw / normalized / status | AFTER first: raw / normalized / status | AFTER last: raw / normalized / status | Name exact matches before -> after |
| --- | --- | --- | --- | --- | --- |
| S1/P1 | Sample / Sample / present | Name / not supplied / present | none / none / unreadable | none / none / unreadable | 0/2 -> 0/2 |
| S1/P10 | none / none / unreadable | none / none / unreadable | provider failure | provider failure | 0/2 -> unavailable |
| S1/P17 | none / none / unreadable | none / none / unreadable | none / none / unreadable | none / none / unreadable | 0/2 -> 0/2 |
| S1/P19 | none / none / unreadable | none / none / unreadable | provider failure | provider failure | 0/2 -> unavailable |
| S1/P24 | none / none / unreadable | none / none / unreadable | ?????? / ?????? / present | none / none / unreadable | 0/2 -> 0/2 |

P1's two unsupported placeholders disappeared and became two review-required
abstentions. P24 introduced **one new false-positive first name**. That name
resembles the printed father's name near the visible label ?? ???, but no provider
field evidence was returned to establish that source definitively. It does not
match the frozen patient's first name. No new false-positive last name appeared.
Name review-required counts: P1 0 -> 2; P17 2 -> 2; P24 2 -> 1. P10/P19 had two
review-required names before and have no extraction afterward.

## Metrics and denominator discipline

Original five successful baseline responses:
- first_name: 0/5; last_name: 0/5; combined: 0/10 (all 0%).
- Name abstentions: 8; wrong substitute values: 2; names needing review: 8.
- Total field accuracy: 16/32 = 50%.

New three successful responses:
- first_name: 0/3; last_name: 0/3; combined: 0/6 (all 0%).
- Name abstentions: 5; wrong substitute values: 1; names needing review: 5.
- Total field accuracy: 10/20 = 50%.

These response sets differ. Comparing 8 vs 5 abstentions directly would be
misleading. On the SAME three scored documents (P1, P17, P24):
- first_name 0/3 -> 0/3; last_name 0/3 -> 0/3; combined 0/6 -> 0/6.
- Name abstentions **4 -> 5**; wrong substitute values **2 -> 1**.
- Total field accuracy **11/20 (55%) -> 10/20 (50%)**.
- Missing expected values across all fields **9 -> 10**; false positives **3 -> 2**.

Per-field non-name exact matches on that paired subset:
- israeli_id: **2/3 -> 1/3**, regression on S1/P17 (previously correct, now missing
  with no value and needs_review).
- central_numbers: **2/3 -> 2/3**.
- sample_receipt_date: **1/2 -> 1/2**; the ambiguous annotation remains excluded.
- sender: **3/3 -> 3/3**.
- commitment_numbers: **3/3 -> 3/3**, including verified absence.

No other non-name metric regression was found on the paired scored subset. This
means score-level stability, not necessarily byte-identical raw output. Non-name
quality on P10/P19 cannot be compared because there is no new extraction. The
observed regression and provider failures cannot be causally attributed to prompt
wording from one sequential run of a nondeterministic external service.

## Provider outcomes

| Fixture | Attempts | Outcome | Total latency (s) |
| --- | --- | --- | --- |
| S1/P1 | 1 | succeeded | 40.706511 |
| S1/P10 | 3 | HTTP 504, 504, 503; failed | 134.062445 |
| S1/P17 | 1 | succeeded | 31.286531 |
| S1/P19 | 3 | timeout, timeout, HTTP 504; failed | 187.054926 |
| S1/P24 | 2 | HTTP 503 then success | 46.125016 |

Five attempted, three successful/scored, two failed, zero unattempted.
**10 provider attempts, 5 retries**; no adapter/SDK nested retries. Backoff remains
1 and then 2 seconds with at most two transient retries. No manual retry or
additional document followed the bounded run. Historical baseline files were not
merged with new results or overwritten.

## Validation and files

Full suite after the prompt edit: **149 passed**, 60 dependency deprecation
warnings. `pip check`, Python syntax and `git diff --check` passed. Frozen PDF/truth
hashes and all Task 7 result hashes were revalidated. Scorer, domain/contracts,
adapters, registry, normalization, policy and measurement/retry implementation
retain their pre-edit hashes. No dependencies were installed; no secrets printed.

Reviewable changes: `backend/services/extraction/prompts.py`,
`tests/test_extraction_smoke.py`, `docs/provider_integration.md`, and this report.
Ignored additions only under `evaluation/artifacts/task8c/`: preflight/version/hash
records, before/after rendered prompts, five-entry manifest, launcher, canonical
run result, analysis script and comparison JSON. `.env` was not modified.
Branch `feat/backend-foundation`, HEAD `1489f318d8569b290114316ff55a9954a12a998e`.
Two original source PDFs remain untracked; nothing staged or committed.

## Original recommendation and closure

The experiment recommended restoring v1 because v2 did not improve measured name
accuracy and a non-name regression was observed. That restoration was subsequently
authorized and is now complete. The historical measurements above are unchanged.
