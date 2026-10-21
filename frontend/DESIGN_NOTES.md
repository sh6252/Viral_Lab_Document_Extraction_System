# Premium UI redesign — isolation and review guide

Subsequent whole-form approval adds backend changes in the original checkout and
React integration here. See `docs/form_approval.md` in the original checkout for
the current policy, migration prerequisite and test commands. The frontend-only
claims and seven-decision limitation below describe the earlier redesign baseline.

The current batch-centered table, focused exception review, API limitations and
updated validation are documented in [BATCH_UX_NOTES.md](BATCH_UX_NOTES.md).
The current wordmark is **מחויבים ל־100**, without diacritics. Its subtitle is
**מטופסי התחייבות לנתונים מסודרים**. The local SVG combines a folded document
with 100 and is shared by the header and favicon. The number belongs to the
product name; it is not a measured or verified AI accuracy claim. Navy/teal,
RTL and explicit human-review semantics remain unchanged.

## Branch and working-tree ownership

- Shared committed base: `01315c9` (`feat: add durable reviewed records and search UI`).
- Original checkout remains on `feat/durable-business-records`.
- New checkout: `storage/premium-ui-worktree`, branch `feat/premium-ui-redesign`.
- No commit, stash, reset, clean, merge, rebase or push was performed.
- A separate Git worktree isolates the redesign's uncommitted files. Merely
  switching a branch would not have isolated the original dirty working tree.
- The frontend has its own copy of the already installed `node_modules` so build
  caches remain worktree-local. No dependencies were installed or updated.

All pre-existing uncommitted work remains ONLY in the original checkout:

- Twelve tracked modifications: root README; `backend/services/excel_export.py`;
  `backend/services/extraction/prompts.py`; architecture/business/provider docs;
  and integration business-repository, demo-API, Excel, prompt, smoke and
  review-export tests.
- Existing untracked files: synthetic seeder and its tests/docs; commitment-export
  compatibility tests/docs; gap-analysis document; the `demo/` directory; the
  fixture helper; the original `frontend/tests/mock-e2e.ts`; and two holdout PDFs.
- Ignored credentials, private artifacts and previous experiments were not copied
  into the worktree or changed. No new backend or database changes belong to the
  redesign branch. The original untracked mock-e2e runner retains its original
  English selectors; it was not overwritten with the new test helpers.

**Integration detail:** the separate worktree's backend is the committed base;
the latest uncommitted commitment-level export/prompt fixes remain in the original
checkout. To review the redesigned UI with those fixes, keep using the authorized
backend launched from the **original checkout**, not the worktree backend. The
current original export behavior produces one row per distinct reviewed
commitment. The UI describes that behavior; no export algorithm changed here.

## Frontend changes

- `src/App.tsx`: branded Hebrew shell; two-view navigation; honest status and
  error feedback; file picking/drop validation; source and seven-field review;
  saved-record filtering, paging and reopening; precise export feedback.
- `src/style.css`: navy `#122e40`, teal `#087f7a`, pale cyan/teal, warm amber
  uncertainty, accessible error colors, reusable panels/buttons/badges and spacing.
- `index.html`: Hebrew language, RTL, local title and theme metadata.
- `tests/App.test.ts`, `tests/Records.test.ts`, `tests/appHarness.ts`: Hebrew
  selector updates while retaining all original behavioral assertions.
- `tests/Premium.test.ts`: navigation/draft preservation, single-file drop and
  explicit consent, missing-field approval safety, failure UI, truthful export
  feedback, and honest empty records.
- `tests/preview-server.ts`, `tests/browser-smoke.ts`: explicit synthetic-only
  browser verification, never imported by production code.

No API contract, request builder, production route, database schema, migration,
model, prompt, scoring or credential configuration was modified. No external font,
image, CDN or UI dependency was added. Raw identifiers are never reformatted.

## UX and accessibility

System Hebrew fonts, RTL logical layout, bidirectional isolation for identifiers,
visible focus rings, keyboard skip link, semantic fieldsets/legends, explicit
labels, status/alert regions and reduced-motion support. Desktop uses a persistent
right navigation rail and a source/review split; narrower screens stack the
workspace. Wide records tables scroll within their own labeled region.

The manual approval count reflects only server-persisted human approval/blank-acknowledgment
states, not extracted values or unsaved choices. The former seven-step approval
meter is removed; the editor now focuses one field at a time. Present, uncertain, missing,
corrected and approved states remain distinct. Reopening makes GET requests only.
Review drafts, reasons, declared reviewer, revisions and identical-command retries
retain their existing semantics. Unresolved export remains supported and is
explicitly described as exporting saved state without implying approval.

## Exact commands (PowerShell, original repository root)

```powershell
npm --prefix .\storage\premium-ui-worktree\frontend test
npm --prefix .\storage\premium-ui-worktree\frontend run typecheck
npm --prefix .\storage\premium-ui-worktree\frontend run build
git -C .\storage\premium-ui-worktree diff --check
```

No lint script/tool is configured, so no lint success is claimed. The environment's
sandbox blocked esbuild path access; production build was rerun successfully with
normal filesystem access, without altering its command or configuration.

Executed validation: all **33** frontend tests pass (the original 12 behavioral
tests plus 21 UI/projection tests); strict TypeScript checking and production build
pass. Real Chrome headless checks pass at **1440×1000** and **390×844**, covering
RTL, keyboard skip link, overflow, reopening without extraction, missing-ID
acknowledgment, seven decisions saved, retained selections, a one-field date
correction to revision eight, reduced motion, extraction failure and export error.
The branding update also passes header fit/RTL checks at **1100**, **390** and
**320** pixels, local SVG loading and matching favicon/title checks. Desktop and
320-pixel screenshots were inspected. The 33-test suite, typecheck and production
build were rerun successfully after the branding change.
No database, provider or external API requests were made. Screenshots were
visually inspected, including the native desktop PDF viewer displaying the
synthetic source. PDF viewer initialization is browser-dependent; a separate
object-URL link remains available. Verify the deployment browser's viewer and
actual authorized source documents manually before using the workflow.

### Safe visual review — no production backend or Gemini

After building:

```powershell
node --experimental-strip-types .\storage\premium-ui-worktree\frontend\tests\preview-server.ts
```

Open **http://127.0.0.1:5174**. The persistent banner states that all records are
synthetic, in-memory UI fixtures. Select **רשומות שמורות**, reopen either test form,
compare both views and review the deliberately missing ID in form 2. There is no
database or external service. The PDF is a minimal labeled preview fixture, not
an extraction/accuracy benchmark. Review changes are mocked in memory. Export
deliberately returns an explicit error rather than fabricating a valid XLSX.
Stop with **Ctrl+C**. No database rollback is needed.

Automated real-browser rendering/safety checks (existing local Chrome/Edge only):

```powershell
node --experimental-strip-types .\storage\premium-ui-worktree\frontend\tests\browser-smoke.ts
```

This starts and stops its own preview server and headless browser with a separate
profile. It allows only local page resources and writes screenshots under ignored
`storage/` inside the frontend. Set `UI_TEST_BROWSER` only if the existing browser
is installed elsewhere. The browser test selects its own free loopback port and
can run alongside the manual preview server.

### Real application integration — existing authorized backend only

```powershell
npm --prefix .\storage\premium-ui-worktree\frontend run dev
```

Open **http://127.0.0.1:5173**. Keep the existing backend on `127.0.0.1:8000` in the
original checkout. Port 5173 is required by its existing Origin policy; do not
rewrite Origin, relax the backend gate, or change ports to work around it. If
another frontend occupies 5173, stop that frontend normally before starting this
one. No live extraction was performed or authorized as part of this redesign.

Manual integration checklist: confirm consent is required; select one authorized
document; inspect seven fields per logical form; explicitly approve/correct or
acknowledge blank with reviewer declaration and reasons; confirm saved revision;
switch forms without losing drafts; reopen via Records; correct a previously
approved date; export and inspect workbook/audit using the existing backend.
Actual PostgreSQL restart persistence and valid XLSX contents require the existing
backend integration suite and are not established by the UI-only preview.

## Rollback / original UI

Stop the redesigned frontend or preview with **Ctrl+C**. From the original root,
run `npm --prefix .\frontend run dev` to use the untouched original frontend.
No branch switch, data deletion, database rollback or credential changes are
necessary. Do not remove the worktree while it contains uncommitted redesign work.
