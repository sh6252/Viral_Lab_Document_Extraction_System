# AGENTS.md — Virology Lab IDP

## 1. Project Mission

Build a reliable, secure, production-oriented Intelligent Document Processing system for the Central Virology Laboratory.

The system extracts structured data from Hebrew medical commitment forms, supports human review, and exports results to Excel.

Development time is limited to 7 days. Prioritize correctness, reliability, testability, security, and a working end-to-end product.

Never sacrifice data integrity for development speed.

## 2. Approved Architecture

- Frontend: React + TypeScript
- Backend: Python + FastAPI
- Database: PostgreSQL
- Processing: PostgreSQL-backed durable jobs
- AI: Provider-independent extraction adapters
- Deployment: Cloud-ready and on-premises compatible
- Testing: pytest, integration tests, and evaluation against ground truth

Use a modular monolith.

Do not introduce microservices, Kubernetes, Redis, Celery, or additional infrastructure without explicit approval.

Follow the project's approved ADRs and business rules. If documents contradict each other, report the contradiction instead of choosing silently.

## 3. Mandatory Engineering Workflow

For every implementation task:

1. Read the relevant documentation and existing code.
2. Inspect the current Git branch and working tree.
3. Identify the files and components affected.
4. Present a short implementation plan.
5. Define or identify acceptance criteria.
6. Implement only the approved scope.
7. Add or update relevant automated tests.
8. Run relevant tests, formatting, linting, and type checking.
9. Review the diff for unintended changes and sensitive information.
10. Report the results and create a commit only if the commit rules are satisfied.

Do not claim that code is tested unless tests were actually executed.

If any required test cannot run, state why.

## 4. Git and Commit Policy

### Branches

- Never commit directly to `main`.
- Work on a dedicated feature or fix branch.
- Do not merge or push without explicit user approval.
- Never force-push.
- Do not modify Git history using reset, rebase, or amend without explicit approval.
- Preserve all pre-existing uncommitted user changes.

### When to commit

Create one atomic commit after a complete, validated unit of work.

A commit is allowed only when:

- The implementation matches the approved scope.
- Relevant automated tests pass.
- The diff has been reviewed.
- No credentials, private documents, generated sensitive data, or unrelated changes are included.
- Documentation has been updated when required.

If tests fail, do not commit the incomplete implementation. Report the blocker.

### Commit messages

Use Conventional Commits:

- `feat: add document extraction adapter`
- `test: add identifier validation tests`
- `fix: preserve leading zeros in identifiers`
- `docs: update extraction architecture`
- `refactor: separate validation from extraction`

Prefer focused commits over large changesets.

Do not make empty or meaningless commits.

## 5. Code Quality Standards

- Use clear module boundaries and explicit interfaces.
- Use type hints in Python and strict TypeScript typing.
- Use Pydantic for API and extraction contracts.
- Keep business logic outside API route handlers.
- Keep provider-specific logic inside adapters.
- Prefer simple, maintainable implementations over unnecessary abstractions.
- Do not duplicate business logic.
- Use structured error handling.
- Do not silently swallow exceptions.
- Avoid hardcoded secrets, deployment URLs, and machine-specific paths.
- Do not add dependencies without a clear justification.

## 6. AI Extraction Rules

- Never invent missing document values.
- Preserve raw extraction separately from normalized data.
- Preserve leading zeros and all observed identifier characters.
- Keep extraction, validation, and review as separate concerns.
- Keep all central numbers and commitment numbers belonging to a logical form.
- One logical commitment form produces one Excel business row.
- Never mix data from unrelated forms.
- Preserve field evidence and processing metadata.
- Never interpret AI self-confidence as verified correctness.
- Mark ambiguous or unreadable fields for review.

No AI adapter may independently approve a business record.

## 7. Evaluation-Driven Development

Any change to the extraction model, prompt, preprocessing, normalization, or review policy must be evaluated against the available labeled dataset.

Requirements:

- Keep development and frozen holdout fixtures separate.
- Do not modify ground truth to make a model appear more accurate.
- Preserve ground-truth annotation uncertainty.
- Track extraction model ID, prompt version, and pipeline version.
- Measure field accuracy, multi-value recall, false positives, missing values, and silent errors.
- Compare results against the previous baseline.

A change that reduces extraction quality must be reported explicitly, even if other tests pass.

Do not claim production-level accuracy based on the small hackathon dataset.

## 8. Security and Privacy

- Treat every uploaded document as sensitive.
- Never commit API keys, credentials, `.env` files, or actual medical records.
- Use `.env.example` with placeholder values.
- Do not log patient information or raw document content.
- Restrict document access through authenticated backend endpoints.
- Validate file types, sizes, and processing limits.
- Store uploads outside publicly accessible static directories.
- Protect exported Excel files against formula injection.
- Keep external AI access disabled by default.
- Send demo documents to external models only under the explicitly approved demo-data policy.
- Never assume permission to transmit real patient records externally.

## 9. Testing Requirements

Each module must have appropriate tests.

Prioritize:

- Domain schema validation
- Identifier and date normalization
- Multiple identifier handling
- Document-to-record isolation
- Missing and ambiguous values
- Review-state transitions
- Safe Excel export
- Processing failure handling
- Evaluation metric correctness

Use synthetic data in automated tests.

External AI calls must be mocked in unit tests.

## 10. Scope Control

Do not implement features outside the current task.

Do not refactor unrelated modules.

Do not change approved architectural decisions without requesting approval.

If a requirement is ambiguous or affects data integrity, stop and ask.

If the task is substantially larger than expected, explain the issue and propose a smaller implementation.

Do not proceed automatically to the next development phase.

## 11. Reporting Requirements

At the end of every task, report:

1. What was implemented
2. Files created or modified
3. Tests executed and results
4. Evaluation changes, if relevant
5. Security considerations
6. Remaining limitations or risks
7. Git branch and commit hash, if committed
8. Recommended next task

Be precise about what works, what is untested, and what remains incomplete.

## 12. Definition of Done

A task is complete only when:

- Its acceptance criteria are satisfied.
- Required tests pass.
- No known critical regression was introduced.
- Changes are reviewed for security and data integrity.
- The implementation is documented where necessary.
- The work is committed according to the Git policy.
- A completion report has been provided.

Never describe incomplete or untested functionality as production-ready.