# Document analysis — Virology Laboratory IDP

Status: initial visual survey, 2026-10-13. **Not a verified transcription or ground-truth dataset.**

## Authoritative requirements (challenge PDF)

- Input: scanned PDF, phone photo, image, handwritten/printed documents; single and batch upload.
- Segment into logical forms, identify form/sender, extract: first name, last name, ID, central number(s), sample receipt date, sender, commitment number(s).
- Central numbers can be multiple; frequently sticker beside 'מרכזי' or handwriting.
- Receipt date: stamp 'התקבל' or handwriting underneath central number.
- Commitment numbers can appear as 'דרישה' or, for Leumit, 'דגימה'; can be multiple.
- Output Excel: one row per commitment, column per field; uncertain fields highlighted, missing fields blank (never guessed); manual correction before export.
- Supplier enumeration includes four HMOs and hospitals. Keep configurable rather than hard-code as complete.
- Demo: batch of 30; target narrative ~4 min including corrections; benchmark 10–15 ground-truthed forms. These are objectives, not measured throughput/accuracy.

## Visual classification of 48 example pages

Each row is a *page*, not necessarily a logical commitment. Classification is a tentative visual impression. High-level cues only, NOT extracted patient values. Confirm against full-resolution images and with the lab.

| Set | Page(s) | Tentative family / visual cues | Risks requiring tests |
|---|---|---|---|
| 1 | 1 | Meuhedet authorization | Different authorization vs internal barcode number; date stamp |
| 1 | 2, 3, 4, 7, 11, 25 | Lab information system screenshots / printouts, several sideways | Rotation, distinguish header fields, several numeric identifiers |
| 1 | 5–6 | Clalit commitment and laboratory document | Distinguish payer / laboratory vs referring institution, handwritten annotations |
| 1 | 8 | Lab request list | Sparse/partial fields, missing values |
| 1 | 9 | Lab results/requests sheet with many barcodes | More than one barcode; do not convert barcode count into commitment count |
| 1 | 10 | Maccabi authorization | Various printed dates and identifiers; multiline services |
| 1 | 12 | Clalit laboratory request sheet | Stamps and handwritten date |
| 1 | 13 | Sheba medical center referral | Referring institution and authorization identifier are distinct |
| 1 | 14 | Soroka lab request, sideways | Rotation, central ID and receipt stamp |
| 1 | 15–16 | Rambam laboratory referrals | Similar template, different patient/test details, prevent accidental deduplication |
| 1 | 17–18, 21 | Rosenblatt medical lab handwritten referral | Handwriting, stamps, clinically specific numbers |
| 1 | 19 | Rambam referral with sticker | Sticker could obscure or supplement printed fields |
| 1 | 20 | Assuta medical authorization | Multiple account/invoice/reference numbers |
| 1 | 22–23 | Similar sideways referral pages | Potential duplication/near-duplication — do not silently collapse |
| 1 | 24 | Public-health laboratory testing form | Checkboxes, handwritten values, multiple barcodes |
| 1 | 26 | Referral form with sticker/handwritten fields | Poor contrast and mixed text |
| 2 | 1 | Lab outside-test request | Sticker vs printed barcode, handwritten test names |
| 2 | 2 | Clalit external lab request | Several stamps/dates and barcode sticker |
| 2 | 3 | Rambam external lab request | Several sample tests; number in label vs other IDs |
| 2 | 4–5 | Sheba external lab request, same template | Similar layout does not prove same commitment |
| 2 | 6–7 | Barzilai requests, similar template | Handwritten dates and multiple IDs; don't auto-merge |
| 2 | 8–9 | Laboratory referrals with barcode labels | Visibly scaled-down content; dates and barcode variants |
| 2 | 10–11 | Clalit out-of-lab referral forms | Handwritten details, stamps |
| 2 | 12–13 | Sheba medical center test invitation | May have multiple requested tests, not necessarily multiple commitments |
| 2 | 14–15 | Leumit forms, rotated | Orientation, form-specific terminology |
| 2 | 16–17 | Clalit lab requests | Similar template, receipt-date stamps |
| 2 | 18–19 | External laboratory requests / HMO logos | Multiple sender logos and handwritten fields |
| 2 | 20–22 | Lab information system screenshots, rotated | Potential multiple internal numbers; screenshot vs form |

## Confirmed design stress cases visible in supplied samples

1. Pages rotated 90 degrees (e.g. set 1 pages 3, 11, 14, 22, 23, 25; set 2 pages 14, 15, 20–22).
2. Repeated similar-looking templates (set 1 pages 15–16; set 2 pages 4–5, 6–7, 16–17).
3. Stickers and barcodes often overlap printed fields; some numbers are internal barcode IDs.
4. Handwriting and printed Hebrew and English coexist.
5. Receipt stamp, referral date, printing date and sample collection date may coexist.
6. Many inputs are screenshots/printouts rather than payer-issued commitment forms.

## Unresolved business questions (do not invent rules)

- What is a *commitment* when a page is merely a lab request/report without an authorization number?
- Which document defines the sender if both medical center and HMO logos appear?
- In a document with M central IDs and N commitment IDs, what is the allowed association and export format?
- Is a received stamp date authoritative if two received stamps appear?
- Can receipt date refer to multiple samples/visits on the same logical document?
- Are two visually similar pages distinct records, duplicates, or continuations?
- What exact Excel column order, format, and multi-value encoding does the laboratory require?
- Is an unknown sender allowed as null or must a valid sender be selected from the controlled list?
- Are remote AI APIs permitted for real patient data? Who approves the provider, retention and transfer region?

## Annotation protocol for next step

For each of 10–15 representative logical *forms*, create expert-adjudicated ground truth: source file, page indices, form boundary, field value(s), per-field source page/bounding region, reason when absent/ambiguous, associations between multiple numbers, reviewer and status. Retain untouched holdout examples. Do not treat model outputs as ground truth.
