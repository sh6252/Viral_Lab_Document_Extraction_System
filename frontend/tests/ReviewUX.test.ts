import { test } from "node:test";
import assert from "node:assert/strict";
import { fields } from "../src/api.ts";
import type { DemoRun, ReviewCommand } from "../src/api.ts";
import { appHarness, text } from "./appHarness.ts";

function server() {
  const form = { form_id: "synthetic-form", revision: 0, finalized: false, review_status: "unresolved",
    review_policy_version: "human-form-attestation-v1", automatic_acceptance: false as const,
    fields: fields.map(field_name => ({ field_name, provider_value_status: field_name === "israeli_id" ? "missing" : "present",
      provider_review_status: "unreviewed", review_status: "unreviewed", reviewed_values: [] as string[],
      requires_review: field_name === "israeli_id", review_reasons: field_name === "israeli_id" ? ["MISSING_VALUE"] : [],
      provider_values: field_name === "israeli_id" ? [] : [{ raw: `source-${field_name}`, normalized: null }] })) };
  const projection = (): DemoRun => ({ run_id: "synthetic-run", review_token: "synthetic-token", status: "succeeded", data_origin: "live",
    provider: "offline_mock", model: "synthetic", form_ids: [form.form_id], failure: null, selected_form: structuredClone(form) });
  const commands: ReviewCommand[] = [];
  let failure = 0, uploads = 0, held: (() => void) | undefined;
  const replies = new Map<string, DemoRun>();
  const fetch: typeof globalThis.fetch = async (input, options) => {
    if (options?.body instanceof FormData) { uploads++; return Response.json(projection(), { status: 201 }); }
    if (String(input).endsWith("/review")) {
      const command = JSON.parse(String(options?.body)) as ReviewCommand; commands.push(command);
      if (failure === 1) await new Promise<void>(resolve => { held = resolve; });
      if (failure === 422) return Response.json({ error: { code: "INVALID_DEMO_REVIEW" } }, { status: 422 });
      const key = command.actions.map(action => action.decision_id).join(",");
      if (!replies.has(key)) {
        assert.equal(command.expected_revision, form.revision);
        assert.ok(command.operator_declaration.trim());
        for (const action of command.actions) {
          const field = form.fields.find(field => field.field_name === action.field_name)!;
          assert.ok(action.action === "correct_and_approve" || action.reason.trim());
          field.reviewed_values = action.reviewed_values;
          field.review_status = { approve_as_extracted: "approved_provider", correct_and_approve: "approved_corrected",
            acknowledge_blank: "acknowledged_blank", reject: "rejected_unresolved", leave_unresolved: "unresolved" }[action.action];
          field.requires_review = ["reject", "leave_unresolved"].includes(action.action);
          form.revision++;
        }
        replies.set(key, projection());
      }
      if (failure === 503) { failure = 0; throw new Error("SECRET_NETWORK_BODY"); }
      return Response.json(replies.get(key));
    }
    return Response.json(projection());
  };
  return { fetch, form, commands, fail(status: number) { failure = status; }, release() { held?.(); }, get uploads() { return uploads; } };
}

test("selected record exposes all seven fields, exact source values, statuses and a writable missing value without dropdowns", async () => {
  const original = globalThis.fetch, mock = server(); globalThis.fetch = mock.fetch;
  try {
    const app = appHarness(); await app.upload(); await app.openApproval();
    assert.deepEqual(app.nodes().filter(node => node.type === "fieldset").map(node => node.props["data-field"]), [...fields]);
    assert.equal(app.nodes().filter(node => node.type === "select" && /בחירת שדה|החלטה עבור/.test(node.props["aria-label"] ?? "")).length, 0);
    for (const field of fields) {
      const card = app.nodes().find(node => node.type === "fieldset" && node.props["data-field"] === field)!;
      assert.equal(String(card.props.className).includes("needs-attention"), field === "israeli_id");
      if (field !== "israeli_id") assert.ok(text(card).includes(`source-${field}`));
    }
    assert.ok(app.actionButton("israeli_id", "approve_as_extracted").props.disabled);
    assert.equal(app.nodes().find(node => node.props["data-value-field"] === "israeli_id")!.props.value, "");
    app.value("israeli_id", 0, "001234567");
    assert.equal(app.nodes().find(node => node.props["data-value-field"] === "israeli_id")!.props.value, "001234567");
    assert.equal(mock.commands.length, 0); assert.equal(mock.uploads, 1);
  } finally { globalThis.fetch = original; }
});

test("direct approve saves only the clicked field and updates its confirmed status", async () => {
  const original = globalThis.fetch, mock = server(); globalThis.fetch = mock.fetch;
  try {
    const app = appHarness(); await app.upload(); app.operator("בודקת סינתטית"); app.setReason("first_name", "נבדק מול המקור");
    await app.act("first_name", "approve_as_extracted");
    assert.equal(mock.commands.length, 1); assert.equal(mock.commands[0].actions.length, 1);
    assert.deepEqual(mock.commands[0].actions[0].reviewed_values, ["source-first_name"]);
    assert.equal(mock.form.fields[0].review_status, "approved_provider");
    assert.match(text(app.tree), /נשמרה החלטת ביקורת אחת/);
    assert.equal(app.nodes().find(node => node.props["data-human-approved-count"] !== undefined)!.props["data-human-approved-count"], 1);
    await app.act("first_name", "approve_as_extracted"); assert.equal(mock.commands.length, 1, "An unchanged saved action is not duplicated");
  } finally { globalThis.fetch = original; }
});

test("direct correction preserves exact new text and another field's unsaved edits and selected action", async () => {
  const original = globalThis.fetch, mock = server(); globalThis.fetch = mock.fetch;
  try {
    const app = appHarness(); await app.upload(); app.operator("בודקת סינתטית");
    app.value("sender", 0, "other unsaved value"); app.decide("sender", "leave_unresolved", "other unsaved reason");
    app.value("first_name", 0, "0017 עברית");
    await app.act("first_name", "correct_and_approve");
    assert.equal(mock.commands[0].actions.length, 1); assert.equal(mock.commands[0].actions[0].field_name, "first_name");
    assert.deepEqual(mock.commands[0].actions[0].reviewed_values, ["0017 עברית"]);
    assert.equal(mock.form.fields[0].review_status, "approved_corrected");
    assert.equal(app.nodes().find(node => node.props["data-value-field"] === "sender")!.props.value, "other unsaved value");
    assert.equal(app.action("sender").props.value, "leave_unresolved");
    assert.equal(app.reason("sender").props.value, "other unsaved reason");
    assert.equal(mock.form.fields[5].review_status, "unreviewed");
  } finally { globalThis.fetch = original; }
});

test("missing reviewer, missing reason and empty corrections never submit or claim success", async () => {
  const original = globalThis.fetch, mock = server(); globalThis.fetch = mock.fetch;
  try {
    const app = appHarness(); await app.upload();
    await app.act("first_name", "approve_as_extracted"); assert.equal(mock.commands.length, 0);
    app.operator("בודקת סינתטית"); await app.act("first_name", "approve_as_extracted"); assert.equal(mock.commands.length, 0);
    await app.act("israeli_id", "correct_and_approve"); assert.equal(mock.commands.length, 0);
    assert.doesNotMatch(text(app.tree), /נשמרה החלטת ביקורת/);
    assert.equal(mock.form.fields[0].review_status, "unreviewed");
    app.setReason("israeli_id", "המקור נבדק והשדה חסר"); await app.act("israeli_id", "acknowledge_blank");
    assert.equal(mock.commands.length, 1); assert.deepEqual(mock.commands[0].actions[0].reviewed_values, []);
    assert.equal(mock.form.fields[2].review_status, "acknowledged_blank");
  } finally { globalThis.fetch = original; }
});

for (const [action, status] of [["reject", "rejected_unresolved"], ["leave_unresolved", "unresolved"]] as const) {
  test(`direct ${action} retains its distinct business meaning`, async () => {
    const original = globalThis.fetch, mock = server(); globalThis.fetch = mock.fetch;
    try {
      const app = appHarness(); await app.upload(); app.operator("בודקת סינתטית"); app.setReason("sender", "נדרש בירור מול המקור");
      await app.act("sender", action);
      assert.equal(mock.commands[0].actions[0].action, action);
      assert.equal(mock.form.fields[5].review_status, status);
      if (action === "reject") assert.deepEqual(mock.commands[0].actions[0].reviewed_values, []);
      assert.equal(mock.form.finalized, false);
    } finally { globalThis.fetch = original; }
  });
}

for (const status of [422, 503]) {
  test(`failed direct save ${status} preserves decisions and supports safe explicit retry`, async () => {
    const original = globalThis.fetch, mock = server(); globalThis.fetch = mock.fetch;
    try {
      const app = appHarness(); await app.upload(); app.operator("בודקת סינתטית"); app.value("first_name", 0, "0017"); mock.fail(status);
      await app.act("first_name", "correct_and_approve");
      assert.doesNotMatch(text(app.tree), /נשמרה החלטת ביקורת/); assert.doesNotMatch(text(app.tree), /SECRET_NETWORK_BODY/);
      assert.equal(app.nodes().find(node => node.props["data-value-field"] === "first_name")!.props.value, "0017");
      mock.fail(0);
      await app.click(status === 503 ? "שליחה חוזרת של אותה בקשה" : "שמירת החלטות הביקורת");
      if (status === 503) assert.deepEqual(mock.commands[0], mock.commands[1]);
      assert.equal(mock.form.revision, 1); assert.equal(mock.uploads, 1);
    } finally { globalThis.fetch = original; }
  });
}

test("direct save shows a server-pending state and blocks duplicate clicks", async () => {
  const original = globalThis.fetch, mock = server(); globalThis.fetch = mock.fetch;
  try {
    const app = appHarness(); await app.upload(); app.operator("בודקת סינתטית"); app.setReason("first_name", "נבדק"); mock.fail(1);
    const handler = app.actionButton("first_name", "approve_as_extracted").props.onClick;
    const saving = app.act("first_name", "approve_as_extracted"); await handler();
    assert.equal(mock.commands.length, 1); assert.match(text(app.tree), /שומר את החלטת השדה/);
    mock.release(); await saving; assert.equal(mock.commands.length, 1);
  } finally { mock.release(); globalThis.fetch = original; }
});
