import { test } from "node:test";
import assert from "node:assert/strict";
import { fields, initialDrafts } from "../src/api.ts";
import type { ReviewField, SelectedForm } from "../src/api.ts";
import { commitmentRows, distinctCommitments, exceptionQueue, fieldState, humanApproved,
  matchesWorkFilter, mergeForms, needsAttention } from "../src/workbench.ts";

function form(form_id = "one"): SelectedForm {
  return { form_id, revision: 0, review_status: "unresolved", fields: fields.map(field_name => ({ field_name,
    provider_values: (field_name === "commitment_numbers" ? ["004", "005", "004", "006"] : field_name === "central_numbers"
      ? ["00012", "00013"] : ["012345678"]).map(raw => ({ raw, normalized: null })),
    provider_value_status: "present", provider_review_status: "unreviewed", reviewed_values: [], review_status: "unreviewed" })) };
}

test("one exact-distinct commitment per row; retain strings, central list and form/document isolation", () => {
  const first = form(), second = form();
  const rows = commitmentRows([{ run_id: "doc-one", form: first }, { run_id: "doc-two", form: second }]);
  assert.deepEqual(rows.map(row => row.commitment), ["004", "005", "006", "004", "005", "006"]);
  assert.equal(new Set(rows.map(row => row.key)).size, 6);
  assert.ok(rows.every(row => row.cells.central_numbers.values.join() === "00012,00013"));
  assert.ok(rows.every(row => row.cells.israeli_id.values[0] === "012345678"));
  assert.deepEqual(distinctCommitments([" 004", "004", "004"]), [" 004", "004"]);
  assert.deepEqual(distinctCommitments([]), [null]);
});

test("only reported exceptions are highlighted; no unreviewed value gains human approval", () => {
  const field = form().fields[0];
  assert.equal(needsAttention(field), false);
  assert.equal(humanApproved(field), false);
  assert.equal(fieldState(field), "חולץ · ללא אישור אנושי");
  for (const status of ["missing", "unreadable", "ambiguous", "not_processed", "unknown"]) {
    assert.equal(needsAttention({ ...field, provider_value_status: status }), true);
  }
  for (const status of ["needs_review", "needs_clarification", "rejected", "unknown"]) {
    assert.equal(needsAttention({ ...field, provider_review_status: status }), true);
  }
  assert.equal(needsAttention({ ...field, provider_values: [] }), true);
  assert.equal(needsAttention({ ...field, provider_values: [{ raw: " ", normalized: null }] }), true);
  assert.equal(needsAttention({ ...field, review_status: "rejected_unresolved" }), true);
  assert.equal(humanApproved({ ...field, provider_review_status: "approved_as_extracted" }), false);
  assert.equal(needsAttention({ ...field, provider_value_status: "missing", review_status: "acknowledged_blank" }), false);
});

test("queue deduplicates shared exceptions across rows; no-flag does not mean completed", () => {
  const value = form(); value.fields[2].provider_value_status = "missing"; value.fields[2].provider_values = [];
  const rows = commitmentRows([{ run_id: "doc", form: value }]);
  assert.equal(rows.filter(row => matchesWorkFilter(row, "attention")).length, 3);
  assert.deepEqual(exceptionQueue(rows), [{ run_id: "doc", form_id: "one", field: "israeli_id" }]);
  assert.ok(rows.every(row => matchesWorkFilter(row, "unapproved") && !matchesWorkFilter(row, "approved")));
});

test("draft shared correction appears consistently on every row without mutating source or falsely approving", () => {
  const value = form(), before = JSON.stringify(value), drafts = initialDrafts(value);
  drafts.first_name = { action: "correct_and_approve", values: ["שרה"], reason: "בדיקה סינתטית" };
  const rows = commitmentRows([{ run_id: "doc", form: value }, { run_id: "other-doc", form: value }], "doc", { one: drafts });
  assert.ok(rows.slice(0, 3).every(row => row.cells.first_name.values[0] === "שרה" && row.cells.first_name.draft));
  assert.ok(rows.slice(0, 3).every(row => !humanApproved(row.cells.first_name.field)));
  assert.ok(rows.slice(3).every(row => row.cells.first_name.values[0] === "012345678" && !row.cells.first_name.draft));
  assert.equal(JSON.stringify(value), before);
});

test("persisted corrections/blank acknowledgments remove exceptions only for their field and form", () => {
  const value = form();
  value.fields[2] = { ...value.fields[2], provider_value_status: "missing", review_status: "acknowledged_blank" };
  value.fields[4] = { ...value.fields[4], review_status: "approved_corrected", reviewed_values: ["19.08.2026"] };
  const rows = commitmentRows([{ run_id: "doc", form: value }]);
  assert.ok(rows.every(row => row.cells.sample_receipt_date.values[0] === "19.08.2026"));
  assert.ok(rows.every(row => fieldState(row.cells.sample_receipt_date.field) === "תוקן ואושר אנושית"));
  assert.ok(rows.every(row => row.cells.israeli_id.values.length === 0 && !row.cells.israeli_id.attention));
  assert.ok(rows.every(row => row.review_status === "unresolved"));
});

test("50 commitments use the same projection without invented sample-to-commitment pairings", () => {
  const value = form(); value.fields[6].provider_values = Array.from({ length: 50 }, (_, i) => ({ raw: String(i).padStart(6, "0"), normalized: null }));
  const rows = commitmentRows([{ run_id: "doc", form: value }]);
  assert.equal(rows.length, 50);
  assert.ok(rows.every(row => row.cells.central_numbers.values.length === 2));
  assert.equal(rows.slice(0, 20).length, 20);
});

test("merging refreshes only the identical document/form, without cross-document field reuse", () => {
  const a = form(), b = form(), updated = { ...a, revision: 7 };
  const merged = mergeForms([{ run_id: "a", form: a }, { run_id: "b", form: b }], [{ run_id: "a", form: updated }]);
  assert.equal(merged.length, 2); assert.equal(merged[0].form.revision, 7); assert.equal(merged[1].form.revision, 0);
});

test("a late background snapshot cannot roll back a confirmed form revision", () => {
  const confirmed = { ...form(), revision: 2, finalized: true };
  const merged = mergeForms([{ run_id: "a", form: confirmed }], [
    { run_id: "a", form: { ...form(), revision: 0, finalized: false } },
    { run_id: "b", form: form() },
  ]);
  assert.equal(merged.length, 2);
  assert.equal(merged[0].form, confirmed);
  assert.equal(merged[1].run_id, "b");
});
