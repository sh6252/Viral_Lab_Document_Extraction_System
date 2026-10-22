import { test } from "node:test";
import assert from "node:assert/strict";
import { fields } from "../src/api.ts";
import type { DemoRun, FinalizeCommand, ReviewCommand, SelectedForm } from "../src/api.ts";
import { appHarness, text } from "./appHarness.ts";
import { commitmentRows, humanApproved, needsAttention } from "../src/workbench.ts";

function server(flagged = false) {
  const form: SelectedForm = { form_id: "one", revision: 0, review_status: "unresolved", finalized: false,
    review_policy_version: "human-form-attestation-v1", automatic_acceptance: false,
    fields: fields.map(field_name => ({ field_name, review_status: "unreviewed", reviewed_values: [],
      provider_value_status: "present", provider_review_status: flagged && field_name === "sample_receipt_date" ? "needs_review" : "unreviewed",
      provider_values: (field_name === "commitment_numbers" ? ["004", "005", "006"] : field_name === "sample_receipt_date" ? ["17.08.2026"] : ["012345678"]).map(raw => ({ raw, normalized: null })),
      requires_review: flagged && field_name === "sample_receipt_date",
      review_reasons: flagged && field_name === "sample_receipt_date" ? ["PROVIDER_REVIEW_REQUIRED"] : [],
    })) };
  const finalCommands: FinalizeCommand[] = [], reviewCommands: ReviewCommand[] = [];
  let failure = "", held: (() => void) | undefined;
  const projection = (): DemoRun => ({ run_id: "synthetic-document", review_token: "token", data_origin: "live", status: "succeeded",
    provider: "offline_mock", model: "synthetic", form_ids: ["one"], failure: null, selected_form: structuredClone(form) });
  const fetch: typeof globalThis.fetch = async (input, options) => {
    const path = String(input);
    if (path.endsWith("/finalize")) {
      const command = JSON.parse(String(options?.body)) as FinalizeCommand; finalCommands.push(command);
      if (failure === "held") await new Promise<void>(resolve => { held = resolve; });
      if (failure === "ambiguous") { failure = ""; throw new Error("Network result unknown"); }
      if (failure === "conflict") return Response.json({ error: { code: "STALE_REVIEW_REVISION" } }, { status: 409 });
      if (failure === "migration") return Response.json({ error: { code: "FORM_APPROVAL_MIGRATION_REQUIRED" } }, { status: 503 });
      assert.equal(command.expected_revision, form.revision);
      assert.equal(command.attest_all_values, true);
      assert.equal(form.fields.some(needsAttention), false);
      form.revision++; form.finalized = true; form.review_status = "complete";
      for (const field of form.fields) if (field.review_status === "unreviewed") {
        field.review_status = "accepted_by_form"; field.reviewed_values = field.provider_values.map(v => v.raw);
        field.value_source = "human_form_attestation";
      }
      return Response.json(projection());
    }
    if (path.endsWith("/review")) {
      const command = JSON.parse(String(options?.body)) as ReviewCommand; reviewCommands.push(command);
      for (const action of command.actions) {
        const field = form.fields.find(f => f.field_name === action.field_name)!;
        field.reviewed_values = action.reviewed_values; field.review_status = "approved_corrected";
        field.requires_review = action.field_name === "sample_receipt_date" && action.reviewed_values[0] === "18-08-2026";
        field.review_reasons = field.requires_review ? ["INVALID_DATE"] : []; form.revision++;
      }
      form.finalized = false; return Response.json(projection());
    }
    if (failure === "refresh" && options?.method !== "POST") throw new Error("Offline refresh failure");
    return Response.json(projection(), { status: options?.method === "POST" ? 201 : 200 });
  };
  return { fetch, form, finalCommands, reviewCommands,
    fail(value: string) { failure = value; }, release() { failure = ""; held?.(); } };
}

test("all commitment rows share one confirmation and one POST followed by a GET refresh", async () => {
  const previous = globalThis.fetch, mock = server(), calls: string[] = [];
  globalThis.fetch = async (input, options) => { calls.push(`${options?.method ?? "GET"} ${input}`); return mock.fetch(input, options); };
  try {
    const app = appHarness(); await app.upload();
    assert.equal(app.rowActions().length, 3);
    assert.ok(app.rowActions().every(b => text(b) === "אישור סופי"));
    await app.openApproval(1);
    assert.equal(app.nodes().filter(n => n.type === "dialog").length, 1);
    assert.match(text(app.tree), /אישור משותף לכל 3 מספרי ההתחייבות/);
    assert.equal(app.button("אישור סופי לטופס").props.disabled, true);
    app.operator("בודקת סינתטית"); app.attest(); await app.click("אישור סופי לטופס");
    assert.equal(mock.finalCommands.length, 1); assert.equal(mock.reviewCommands.length, 0);
    assert.ok(app.rowActions().every(b => text(b) === "אושר סופית" && b.props.disabled));
    assert.ok(calls.findIndex(c => c.startsWith("POST") && c.endsWith("/finalize")) < calls.length - 1);
    assert.ok(mock.form.fields.every(f => !humanApproved(f)));
    assert.equal(app.nodes().filter(n => n.type === "dialog").length, 0);
  } finally { globalThis.fetch = previous; }
});

test("all fields remain visible while blocking date prevents approval until a valid explicit correction", async () => {
  const previous = globalThis.fetch, mock = server(true); globalThis.fetch = mock.fetch;
  try {
    const app = appHarness(); await app.upload(); await app.openApproval();
    assert.ok(app.rowActions().every(b => text(b) === "בדיקת חריגות"));
    assert.equal(app.nodes().filter(n => n.type === "fieldset").length, 7);
    assert.equal(app.nodes().filter(n => n.type === "select" && n.props["aria-label"] === "בחירת שדה לביקורת").length, 0);
    assert.match(String(app.nodes().find(n => n.props["data-field"] === "sample_receipt_date")!.props.className), /needs-attention/);
    app.operator("בודקת סינתטית"); app.value("sample_receipt_date", 0, "18-08-2026"); app.decide("sample_receipt_date", "correct_and_approve", "");
    await app.click("שמירת החלטות הביקורת"); await app.openApproval();
    assert.match(text(app.tree), /תאריך לא תקין או פורמט לא נתמך/);
    assert.equal(app.nodes().filter(n => n.type === "dialog").length, 0);
    app.value("sample_receipt_date", 0, "18.08.2026"); app.decide("sample_receipt_date", "correct_and_approve", "");
    await app.click("שמירת החלטות הביקורת");
    assert.equal(app.nodes().filter(n => n.props["data-attention"]).length, 0);
    assert.equal(mock.form.fields[4].provider_review_status, "needs_review");
    await app.openApproval(2); app.attest(); await app.click("אישור סופי לטופס");
    assert.equal(mock.form.fields[4].review_status, "approved_corrected");
  } finally { globalThis.fetch = previous; }
});

test("unsaved drafts, unknown policy and failed refresh never authorize a form", async () => {
  const previous = globalThis.fetch, mock = server(); globalThis.fetch = mock.fetch;
  try {
    const app = appHarness(); await app.upload(); app.value("sample_receipt_date", 0, "18.08.2026");
    await app.openApproval();
    assert.match(text(app.tree), /טיוטות שטרם נשמרו בטופס זה: תאריך קבלת דגימה/);
    assert.equal(app.nodes().filter(n => n.type === "dialog").length, 0);
    delete mock.form.fields[0].requires_review; await app.click("רענון מצב מהשרת");
    assert.match(text(app.tree), /השרת לא החזיר מצב זכאות מלא/);
    mock.fail("refresh"); await app.openApproval();
    assert.match(text(app.tree), /רענון מצב השרת נכשל/);
    assert.equal(mock.finalCommands.length, 0);
  } finally { globalThis.fetch = previous; }
});

test("ambiguous command retries identical IDs after closing and reopening", async () => {
  const previous = globalThis.fetch, mock = server(); globalThis.fetch = mock.fetch;
  try {
    const app = appHarness(); await app.upload(); await app.openApproval(); app.operator("בודקת סינתטית"); app.attest(); mock.fail("ambiguous");
    await app.click("אישור סופי לטופס"); assert.match(text(app.tree), /תוצאת האישור הסופי אינה ידועה/);
    await app.click("סגירת חלון האישור"); await app.openApproval(2);
    await app.click("שליחה חוזרת של אותו אישור סופי");
    assert.deepEqual(mock.finalCommands[0], mock.finalCommands[1]);
  } finally { globalThis.fetch = previous; }
});

test("stale revision and missing migration show errors and never claim success", async () => {
  for (const failure of ["conflict", "migration"]) {
    const previous = globalThis.fetch, mock = server(); globalThis.fetch = mock.fetch;
    try {
      const app = appHarness(); await app.upload(); await app.openApproval(); app.operator("בודקת סינתטית"); app.attest(); mock.fail(failure);
      await app.click("אישור סופי לטופס");
      assert.match(text(app.tree), failure === "conflict" ? /גרסת הטופס השתנתה/ : /נדרשת התאמת מסד הנתונים/);
      assert.equal(mock.form.finalized, false);
      assert.ok(!text(app.tree).includes("הטופס אושר סופית בשרת"));
    } finally { globalThis.fetch = previous; }
  }
});

test("double click sends one request and waits for provider-independent API confirmation", async () => {
  const previous = globalThis.fetch, mock = server(); globalThis.fetch = mock.fetch;
  try {
    const app = appHarness(); await app.upload(); await app.openApproval(); app.operator("בודקת סינתטית"); app.attest(); mock.fail("held");
    const handler = app.button("אישור סופי לטופס").props.onClick;
    const first = handler(), second = handler(); await new Promise(resolve => setTimeout(resolve, 5));
    assert.equal(mock.finalCommands.length, 1); assert.equal(mock.form.finalized, false);
    mock.release(); await first; await second; await app.click("רענון מצב מהשרת");
    assert.ok(app.rowActions().every(b => text(b) === "אושר סופית"));
  } finally { globalThis.fetch = previous; }
});

test("failed GET after a confirmed POST preserves confirmation without another POST", async () => {
  const previous = globalThis.fetch, mock = server(); globalThis.fetch = mock.fetch;
  try {
    const app = appHarness(); await app.upload(); await app.openApproval(); app.operator("בודקת סינתטית"); app.attest(); mock.fail("refresh");
    await app.click("אישור סופי לטופס");
    assert.match(text(app.tree), /האישור נשמר בשרת, אך הרענון הנוסף נכשל/);
    assert.ok(app.rowActions().every(b => text(b) === "אושר סופית")); assert.equal(mock.finalCommands.length, 1);
  } finally { globalThis.fetch = previous; }
});

test("cancel and reopen never preserve a checked attestation", async () => {
  const previous = globalThis.fetch, mock = server(); globalThis.fetch = mock.fetch;
  try {
    const app = appHarness(); await app.upload(); await app.openApproval(); app.operator("בודקת סינתטית"); app.attest();
    await app.click("סגירת חלון האישור"); await app.openApproval(2);
    assert.equal(app.button("אישור סופי לטופס").props.disabled, true);
    assert.equal(mock.finalCommands.length, 0);
  } finally { globalThis.fetch = previous; }
});

test("confirmation covers all commitments across pages and updates the next page without another POST", async () => {
  const previous = globalThis.fetch, mock = server(); globalThis.fetch = mock.fetch;
  mock.form.fields[6].provider_values = Array.from({ length: 30 }, (_, i) => ({ raw: String(i).padStart(6, "0"), normalized: null }));
  try {
    const app = appHarness(); await app.upload(); assert.equal(app.rowActions().length, 20);
    await app.openApproval(17); assert.match(text(app.tree), /אישור משותף לכל 30 מספרי ההתחייבות/);
    app.operator("בודקת סינתטית"); app.attest(); await app.click("אישור סופי לטופס");
    await app.click("עמוד הבא בטבלה"); assert.equal(app.rowActions().length, 10);
    assert.ok(app.rowActions().every(b => text(b) === "אושר סופית" && b.props.disabled));
    assert.equal(mock.finalCommands.length, 1);
  } finally { globalThis.fetch = previous; }
});

test("unsaved edits in another logical form do not block the selected form or enter its confirmation", async () => {
  const previous = globalThis.fetch, mock = server(), second = structuredClone(mock.form); second.form_id = "two";
  globalThis.fetch = async (input, options) => {
    const response = await mock.fetch(input, options), value = await response.json() as DemoRun;
    value.form_ids = ["one", "two"];
    if (String(input).includes("form_id=two")) value.selected_form = second;
    return Response.json(value, { status: response.status });
  };
  try {
    const app = appHarness(); await app.upload(); app.value("first_name", 0, "טיוטה סינתטית בטופס אחר");
    await app.selectForm("two"); await app.openApproval(3);
    assert.equal(app.nodes().filter(n => n.type === "dialog").length, 1);
    assert.match(text(app.tree), /אישור משותף לכל 3 מספרי ההתחייבות/);
    await app.click("סגירת חלון האישור"); await app.selectForm("one"); await app.openApproval(0);
    assert.equal(app.nodes().filter(n => n.type === "dialog").length, 0);
    assert.equal(mock.finalCommands.length, 0);
  } finally { globalThis.fetch = previous; }
});
