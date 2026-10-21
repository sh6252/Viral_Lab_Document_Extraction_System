# Business rules - approved baseline

Status: approved architecture baseline, 2026-10-13; export cardinality and laboratory identifier semantics clarified by the laboratory on 2026-10-20. The clarification supersedes the previous one-Excel-row-per-logical-form rule.

## Required business fields

The system extracts and exports the following seven business fields for every logical commitment form:

1. First name
2. Last name
3. Israeli ID
4. Central number(s)
5. Sample receipt date
6. Sender
7. Commitment number(s)

Identifier-like values, including Israeli IDs, central numbers, and commitment numbers, are strings. Preserve leading zeros, punctuation, and original formatting in raw values.

## Approved rules

BR-001. A logical form remains the persisted/reviewable unit. Export one Excel row per exact distinct reviewed commitment number in that form, preserving first-occurrence order. Repeat all other fields and all central numbers on each row. If no reviewed commitment is present, export one blank-commitment row with its actual review statuses. Never form a Cartesian product or deduplicate across forms.

BR-002. Missing, unreadable, and ambiguous values must never be guessed. They require review, and may remain blank after explicit human acknowledgment.

BR-003. Automatic field acceptance must follow a validated, field-specific risk policy. Model self-confidence is insufficient.

BR-004. Export all logical forms, including unresolved forms, to Excel with clearly marked record and field review statuses. Preserve the seven required business fields and add review metadata. Processing failures must be represented separately without pretending they are extracted commitments.

BR-005. Every document received with a laboratory sample is a commitment document; this does not establish the role of every number. Commitment labels include "מספר התחייבות", contextual "דרישה", and contextual "דגימה" specifically on Leumit forms. Examination/test codes are not commitments. Central numbers are internal laboratory sample identifiers, including "מרכזי" stickers and handwritten "מספר מרכזי". Receipt dates come from below the central identifier or the relevant "התקבל" stamp, not an unrelated document date. Conflicting/unsupported roles or dates remain ambiguous for review.

## Logical form and export semantics

- A logical form is the reviewable/persisted document unit; export expansion does not create new records or review revisions.
- A logical form may contain zero, one, or many central numbers.
- A logical form may contain zero, one, or many commitment numbers.
- Central numbers remain a compact JSON array on every Excel row; `commitment_numbers` contains exactly one literal text identifier per expanded row, or a blank cell.
- Exact duplicate reviewed commitments collapse only in derived output rows, retaining their first occurrence. No trimming or numeric/normalized comparison is used: `0001`, `1` and ` 0001` stay distinct. Full raw/reviewed lists and audit history retain all occurrences. Numbers repeated in different forms remain separate rows; no global uniqueness is asserted.
- Ambiguous relationships between central numbers and commitment numbers are preserved as unresolved relationship metadata. The system must not create cartesian-product rows or infer pairings without evidence or human review.
- Unresolved relationships do not prevent export under BR-004. They must be marked in review metadata so downstream users can distinguish verified, corrected, ambiguous, missing, unreadable, and processing-failed values.

## Review and correction rules

- Raw extracted candidates are immutable. Corrections create reviewed values and audit events; they do not overwrite source evidence.
- A field can be approved as extracted, corrected then approved, rejected as not applicable/incorrect, or acknowledged blank.
- A logical form can be exported with unresolved or blank fields only when those statuses are explicit in the exported review metadata.
- Review actions must record actor, timestamp, previous state, new state, and reason/comment when supplied.
- Processing failures are exported in a separate failure/status sheet or section, not as commitment rows with fabricated fields.

## Validation principles

- Validation is independent of the AI/OCR provider.
- Field-specific validation policy decides whether a field may be automatically accepted.
- Model self-confidence, OCR confidence, or provider confidence can be recorded as evidence signals, but cannot alone approve a field.
- Values that fail validation remain candidates and require review.
- Missing, unreadable, ambiguous, and not-processed are distinct states.

## Unresolved decisions

UD-001. Exact Excel column order, names, formatting, and whether failure/status records are represented as a separate sheet or appended section.

UD-002. Controlled sender list and whether an unknown sender should be blank, free text, or selected from an "unknown" value.

UD-003. Authority precedence when conflicting patient names, dates, senders, central numbers, or commitment numbers appear on the same logical form.

UD-004. Duplicate and re-upload behavior for identical files or near-duplicate logical forms.

UD-005. Final policy for automatic field acceptance thresholds per field.

UD-006. Approval status for any external AI provider handling real medical records, including region, retention, and audit requirements.
