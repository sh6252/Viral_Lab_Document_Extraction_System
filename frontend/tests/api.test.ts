import { test } from "node:test";
import assert from "node:assert/strict";
import { fields, initialDrafts, makeCommand, request, APIError } from "../src/api.ts";
import type { DemoRun, Drafts } from "../src/api.ts";

function run(): DemoRun {
  return { run_id: "opaque", review_token: "bound", status: "succeeded", data_origin: "live", form_ids: ["1", "2"],
    provider: "mock", model: "test", failure: null, selected_form: { form_id: "1", revision: 3, review_status: "unresolved",
      fields: fields.map(field_name => ({ field_name, provider_values: [{ raw: "000/7) עברית", normalized: "different" }],
        provider_value_status: "present", provider_review_status: "unreviewed", reviewed_values: [], review_status: "unreviewed" })) } };
}
test("all seven drafts have no implicit action and copy exact raw text", () => {
  const value = run(); const drafts = initialDrafts(value.selected_form!);
  assert.deepEqual(Object.keys(drafts), [...fields]);
  for (const field of fields) assert.equal(drafts[field].action, "");
  drafts.first_name.values.push("new"); assert.equal(value.selected_form!.fields[0].provider_values.length, 1);
});
test("explicit actions preserve whole list, exact multi-values, duplicates and blanks", () => {
  const value = run(); const drafts: Drafts = initialDrafts(value.selected_form!);
  drafts.first_name = { action: "approve_as_extracted", values: ["edited ignored"], reason: " original " };
  drafts.central_numbers = { action: "correct_and_approve", values: ["0001", "A,B", "0001", "  עברית "], reason: "inspect" };
  drafts.last_name = { action: "acknowledge_blank", values: ["ignored"], reason: "blank" };
  let id = 0;
  const body = makeCommand(value, drafts, " declared ", () => `d-${id++}`, () => "2026-01-01T00:00:00Z");
  assert.equal(body.review_token, "bound"); assert.equal(body.expected_revision, 3);
  assert.deepEqual(body.actions[0].reviewed_values, ["000/7) עברית"]);
  assert.deepEqual(body.actions[1].reviewed_values, []);
  assert.deepEqual(body.actions[2].reviewed_values, ["0001", "A,B", "0001", "  עברית "]);
  assert.equal(body.operator_declaration, " declared ");
  assert.ok(!("canonical_snapshot" in body));
});
test("no selected action produces no inferred approval", () => {
  const value = run(); assert.deepEqual(makeCommand(value, initialDrafts(value.selected_form!), "operator").actions, []);
});
test("acknowledged blank is not repopulated from provider text when revisited", () => {
  const value = run(); value.selected_form!.fields[0].review_status = "acknowledged_blank";
  assert.deepEqual(initialDrafts(value.selected_form!).first_name.values, []);
});
test("fetch sets local guard/no-store and sanitizes unstructured errors", async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async (_, options) => {
      assert.equal(new Headers(options?.headers).get("X-Local-Demo"), "1");
      assert.equal(options?.cache, "no-store"); assert.equal(options?.credentials, "omit");
      return new Response(JSON.stringify({ error: { code: "PRIVATE value/path" } }), { status: 422 });
    };
    await assert.rejects(request("/api/v1/demo-runs/x"), (error: unknown) => error instanceof APIError && error.code === "DEMO_REQUEST_FAILED");
  } finally { globalThis.fetch = original; }
});
