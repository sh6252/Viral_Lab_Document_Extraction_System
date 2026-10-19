import { test } from "node:test";
import assert from "node:assert/strict";
import { fields } from "../src/api.ts";
import type { DemoRun, ReviewCommand, SelectedForm } from "../src/api.ts";
import { appHarness, text } from "./appHarness.ts";

function server() {
  const forms: SelectedForm[] = ["one", "two"].map(form_id => ({ form_id, revision: 0, review_status: "unresolved",
    fields: fields.map(field_name => ({ field_name, provider_values: [{ raw: "0001 עברית", normalized: null }],
      provider_value_status: "present", provider_review_status: "unreviewed", reviewed_values: [], review_status: "unreviewed" })) }));
  forms[1].fields[2] = { ...forms[1].fields[2], provider_values: [], provider_value_status: "missing", provider_review_status: "needs_review" };
  let failure = "";
  const commands: ReviewCommand[] = [];
  function run(form = forms[0]): DemoRun {
    return { run_id: "synthetic-run", review_token: "bound", status: "succeeded", data_origin: "live",
      form_ids: forms.map(f => f.form_id), provider: "mock", model: "test", failure: null, selected_form: structuredClone(form) };
  }
  const fetch: typeof globalThis.fetch = async (input, options) => {
    const url = new URL(String(input), "http://127.0.0.1:18000");
    assert.equal(new Headers(options?.headers).get("X-Local-Demo"), "1");
    if (url.pathname.endsWith("/review")) {
      const command = JSON.parse(String(options?.body)) as ReviewCommand;
      commands.push(command);
      if (failure === "422") return Response.json({ error: { code: "INVALID_DEMO_REVIEW" } }, { status: 422 });
      const form = forms.find(f => url.pathname.includes(`/forms/${f.form_id}/`))!;
      if (command.expected_revision === form.revision) {
        for (const action of command.actions) {
          const field = form.fields.find(f => f.field_name === action.field_name)!;
          field.reviewed_values = action.reviewed_values;
          field.review_status = action.action === "acknowledge_blank" ? "acknowledged_blank" : "approved_provider";
          form.revision++;
        }
        form.review_status = form.fields.every(f => f.review_status !== "unreviewed") ? "complete" : "unresolved";
      }
      if (failure === "lost-response") { failure = ""; throw new TypeError("Synthetic lost reply"); }
      return Response.json(run(form));
    }
    if (url.pathname.endsWith("export.xlsx")) return new Response(new Blob(["synthetic workbook"]));
    return Response.json(run(forms.find(f => f.form_id === url.searchParams.get("form_id"))));
  };
  return { fetch, forms, commands, fail(value: string) { failure = value; } };
}

test("successful two-form saves retain selections/reasons, confirm persisted status and unlock export", async () => {
  const original = globalThis.fetch; const mock = server(); globalThis.fetch = mock.fetch;
  try {
    const app = appHarness(); await app.upload(); app.operator("Declared synthetic operator");
    await app.selectForm("two"); app.decide("israeli_id", "acknowledge_blank", "Blank ID inspected");
    await app.selectForm("one");
    for (const name of fields) app.decide(name, "approve_as_extracted", `Inspected ${name}`);
    await app.click("Save explicit review actions");
    assert.equal(app.action("first_name").props.value, "approve_as_extracted", "Successful save must not erase the visible decision");
    assert.equal(app.reason("first_name").props.value, "Inspected first_name");
    assert.match(text(app.tree), /Saved 7 review actions.*complete.*revision 7/);
    assert.ok(app.button("Save explicit review actions").props.disabled, "Already saved choices cannot be resubmitted");
    assert.ok(!app.button("Download Excel").props.disabled);
    await app.selectForm("two");
    assert.equal(app.action("first_name").props.value, "");
    assert.equal(app.action("israeli_id").props.value, "acknowledge_blank", "Saving the other form must preserve this unsaved decision");
    for (const name of fields) app.decide(name, name === "israeli_id" ? "acknowledge_blank" : "approve_as_extracted");
    await app.click("Save explicit review actions");
    assert.equal(app.action("israeli_id").props.value, "acknowledge_blank");
    assert.match(text(app.tree), /acknowledged_blank/);
    assert.equal(mock.commands[1].actions.find(a => a.field_name === "israeli_id")!.reviewed_values.length, 0);
    assert.equal(mock.commands[1].operator_declaration, "Declared synthetic operator");
    assert.ok(mock.commands.every(c => c.actions.every(a => a.reason.trim())));
    await app.selectForm("one");
    assert.equal(app.action("first_name").props.value, "approve_as_extracted");
    assert.ok(app.button("Save explicit review actions").props.disabled);
    await app.click("Download Excel");
    assert.ok(app.download); assert.equal(app.confirmations, 0, "Saved choices are not unsaved changes");
    assert.equal(mock.commands.flatMap(c => c.actions).length, 14);
  } finally { globalThis.fetch = original; }
});

test("save validation error preserves decisions, corrected values, operator and reasons for an explicit retry", async () => {
  const original = globalThis.fetch; const mock = server(); globalThis.fetch = mock.fetch;
  try {
    const app = appHarness(); await app.upload(); app.operator("Declared operator");
    app.decide("first_name", "correct_and_approve", "Carefully checked source"); app.value("first_name", 0, "0007 עברית");
    mock.fail("422"); await app.click("Save explicit review actions");
    assert.equal(app.action("first_name").props.value, "correct_and_approve");
    assert.equal(app.reason("first_name").props.value, "Carefully checked source");
    assert.match(text(app.tree), /INVALID_DEMO_REVIEW.*preserved/);
    assert.ok(!app.button("Save explicit review actions").props.disabled);
    mock.fail(""); await app.click("Save explicit review actions");
    assert.equal(mock.commands[1].operator_declaration, "Declared operator");
    assert.deepEqual(mock.commands[1].actions[0].reviewed_values, ["0007 עברית"]);
  } finally { globalThis.fetch = original; }
});

test("ambiguous save failure retries the SAME command and does not duplicate persisted decisions", async () => {
  const original = globalThis.fetch; const mock = server(); globalThis.fetch = mock.fetch;
  try {
    const app = appHarness(); await app.upload(); app.operator("Declared operator");
    for (const name of fields) app.decide(name, "approve_as_extracted");
    mock.fail("lost-response"); await app.click("Save explicit review actions");
    assert.match(text(app.tree), /unknown.*preserved/);
    assert.equal(app.action("first_name").props.value, "approve_as_extracted");
    assert.ok(app.button("Download Excel").props.disabled);
    await app.click("Retry SAME review request");
    assert.deepEqual(mock.commands[1], mock.commands[0]);
    assert.equal(mock.forms[0].revision, 7);
    assert.ok(!app.button("Download Excel").props.disabled);
  } finally { globalThis.fetch = original; }
});

test("editing one saved decision sends only that new decision and keeps the other six saved", async () => {
  const original = globalThis.fetch; const mock = server(); globalThis.fetch = mock.fetch;
  try {
    const app = appHarness(); await app.upload(); app.operator("Declared operator");
    for (const name of fields) app.decide(name, "approve_as_extracted");
    await app.click("Save explicit review actions");
    app.decide("sender", "approve_as_extracted", "Rechecked sender explicitly");
    await app.click("Save explicit review actions");
    assert.equal(mock.commands[1].actions.length, 1);
    assert.equal(mock.commands[1].actions[0].field_name, "sender");
    assert.equal(mock.commands[1].expected_revision, 7);
    assert.equal(mock.forms[0].revision, 8);
  } finally { globalThis.fetch = original; }
});
