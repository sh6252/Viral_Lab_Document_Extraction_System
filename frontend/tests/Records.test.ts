import { test } from "node:test";
import assert from "node:assert/strict";
import { fields } from "../src/api.ts";
import type { DemoRun, ReviewCommand, SavedRecord, SelectedForm } from "../src/api.ts";
import { appHarness, text } from "./appHarness.ts";

function recordsServer() {
  const forms = new Map<string, SelectedForm>();
  const rows: SavedRecord[] = Array.from({ length: 21 }, (_, i) => {
    const run_id = i < 2 ? "doc-0" : `doc-${i}`;
    const form_id = i === 1 ? "two" : "one";
    const summary = Object.fromEntries(fields.map(name => [name,
      name === "first_name" ? ["נועה"] : name === "last_name" ? ["כהן"] : name === "sender" ? ["מרכז א"]
        : name === "sample_receipt_date" ? ["01/02/2026"] : name === "central_numbers" ? ["0007", "008/9)"] : ["000123"]])) as SavedRecord["summary"];
    if (i === 1) summary.israeli_id = [];
    forms.set(`${run_id}/${form_id}`, { form_id, revision: 7, review_status: "complete", fields: fields.map(field_name => ({
      field_name, provider_values: summary[field_name].map(raw => ({ raw, normalized: null })),
      provider_value_status: summary[field_name].length ? "present" : "missing", provider_review_status: "unreviewed",
      reviewed_values: [...summary[field_name]], review_status: summary[field_name].length ? "approved_provider" : "acknowledged_blank",
    })) });
    return { run_id, document_id: run_id, form_id, revision: 7, review_status: "completed", summary,
      created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-02T00:00:00Z" };
  });
  const calls: { method: string; path: string }[] = [];
  const commands: ReviewCommand[] = [];
  let fail = "";
  const fetch: typeof globalThis.fetch = async (input, options) => {
    const url = new URL(String(input), "http://127.0.0.1:8000");
    assert.equal(new Headers(options?.headers).get("X-Local-Demo"), "1");
    calls.push({ method: options?.method ?? "GET", path: url.pathname + url.search });
    if (url.pathname === "/api/v1/business-records") {
      if (fail === "list") return Response.json({ error: { code: "BUSINESS_STORAGE_UNAVAILABLE" } }, { status: 503 });
      const q = url.searchParams.get("q") ?? "", status = url.searchParams.get("status"), sender = url.searchParams.get("sender");
      const matching = rows.filter(row => JSON.stringify(row.summary).includes(q) && (!status || row.review_status === status)
        && (!sender || row.summary.sender.includes(sender)));
      const offset = Number(url.searchParams.get("offset") ?? 0), limit = Number(url.searchParams.get("limit") ?? 20);
      return Response.json({ items: matching.slice(offset, offset + limit), total: matching.length, offset, limit });
    }
    if (url.pathname.endsWith("/source")) {
      if (fail === "source") return Response.json({ error: { code: "BUSINESS_STORAGE_UNAVAILABLE" } }, { status: 503 });
      return new Response("%PDF-1.7 frozen synthetic source", { headers: { "Content-Type": "application/pdf" } });
    }
    if (url.pathname.endsWith("export.xlsx")) return new Response("synthetic XLSX export");
    const runId = url.pathname.split("/")[4];
    const formId = url.pathname.endsWith("/review") ? url.pathname.split("/")[6] : url.searchParams.get("form_id") ?? "one";
    const form = forms.get(`${runId}/${formId}`)!;
    assert.ok(form);
    if (url.pathname.endsWith("/review")) {
      const command = JSON.parse(String(options?.body)) as ReviewCommand;
      commands.push(command);
      assert.equal(command.expected_revision, form.revision);
      for (const action of command.actions) {
        const field = form.fields.find(f => f.field_name === action.field_name)!;
        field.reviewed_values = action.reviewed_values;
        field.review_status = "approved_corrected";
        form.revision++;
        const row = rows.find(r => r.run_id === runId && r.form_id === formId)!;
        row.summary[action.field_name] = [...action.reviewed_values]; row.revision = form.revision;
      }
    }
    return Response.json({ run_id: runId, review_token: `bound-${runId}`, status: "succeeded", data_origin: "live",
      provider: "mock", model: "synthetic", failure: null,
      form_ids: rows.filter(r => r.run_id === runId).map(r => r.form_id), selected_form: structuredClone(form) } satisfies DemoRun);
  };
  return { fetch, calls, commands, rows, forms, fail(value: string) { fail = value; } };
}

test("Hebrew records search/filter/pagination uses bounded GET queries and reopening restores protected preview", async () => {
  const original = globalThis.fetch, mock = recordsServer(); globalThis.fetch = mock.fetch;
  try {
    const app = appHarness();
    await app.click("חיפוש / רענון");
    assert.match(text(app.tree), /רשומות שמורות/);
    assert.match(text(app.tree), /21 רשומות נמצאו/);
    assert.ok(app.button("הקודם").props.disabled);
    await app.click("הבא");
    assert.match(mock.calls.at(-1)!.path, /offset=20/);
    assert.ok(app.button("הבא").props.disabled);
    await app.click("הקודם");
    app.input("חיפוש רשומות", "0007");
    app.input("סינון מצב בדיקה", "completed");
    app.input("סינון שולח", "מרכז א");
    app.input("סינון תאריך קבלת דגימה", "2026-02-01");
    await app.click("חיפוש / רענון");
    const query = new URL(mock.calls.at(-1)!.path, "http://localhost").searchParams;
    assert.equal(query.get("q"), "0007"); assert.equal(query.get("offset"), "0");
    assert.equal(query.get("status"), "completed"); assert.equal(query.get("sender"), "מרכז א");
    assert.equal(query.get("receipt_date"), "2026-02-01");
    await app.openRecord(1);
    assert.equal(app.action("israeli_id").props.value, "");
    assert.ok(app.hasPreview());
    assert.ok(app.nodes().some(n => n.type === "a" && n.props.href === "blob:synthetic" && n.props.rel === "noopener noreferrer"), "Protected object URL remains available when the inline viewer is unsupported");
    assert.match(text(app.tree), /רשומה שמורה.*mock/);
    assert.ok(mock.calls.some(c => c.path === "/api/v1/demo-runs/doc-0/source"));
    assert.ok(mock.calls.every(c => c.method === "GET"));
  } finally { globalThis.fetch = original; }
});

test("reopened approved date can be corrected explicitly; saving preserves other decisions, form scope and export", async () => {
  const original = globalThis.fetch, mock = recordsServer(); globalThis.fetch = mock.fetch;
  try {
    const app = appHarness(); await app.click("חיפוש / רענון"); await app.openRecord();
    const originalProvider = mock.forms.get("doc-0/one")!.fields[4].provider_values[0].raw;
    app.operator("Declared synthetic reviewer");
    app.decide("sample_receipt_date", "correct_and_approve", "Corrected after checking persisted original");
    app.value("sample_receipt_date", 0, "2026-02-02");
    await app.click("שמירת החלטות הביקורת");
    assert.equal(mock.commands.length, 1);
    assert.equal(mock.commands[0].expected_revision, 7);
    assert.equal(mock.commands[0].actions.length, 1);
    assert.deepEqual(mock.commands[0].actions[0].reviewed_values, ["2026-02-02"]);
    assert.equal(mock.forms.get("doc-0/one")!.fields[4].provider_values[0].raw, originalProvider);
    assert.equal(mock.forms.get("doc-0/two")!.revision, 7);
    assert.match(text(app.tree), /גרסה 8/);
    assert.equal(app.action("sample_receipt_date").props.value, "correct_and_approve");
    await app.click("ייצוא ל־Excel");
    assert.equal(await app.download!.text(), "synthetic XLSX export");
    assert.ok(mock.calls.filter(c => c.method === "POST").every(c => c.path.endsWith("/review")));
    const restarted = appHarness(); await restarted.click("חיפוש / רענון"); await restarted.openRecord();
    assert.match(text(restarted.tree), /גרסה 8/);
    assert.equal(restarted.action("sample_receipt_date").props.value, "");
    assert.equal(mock.commands.length, 1, "Reopening must not resubmit past decisions");
  } finally { globalThis.fetch = original; }
});

test("failed list and failed source fetch show errors; reopening never discards unsaved review before success", async () => {
  const original = globalThis.fetch, mock = recordsServer(); globalThis.fetch = mock.fetch;
  try {
    const app = appHarness(); mock.fail("list"); await app.click("חיפוש / רענון");
    assert.match(text(app.tree), /טעינת הרשומות נכשלה/);
    mock.fail(""); await app.click("חיפוש / רענון"); await app.openRecord();
    app.operator("Reviewer"); app.decide("sender", "correct_and_approve", "Uncommitted correction");
    app.value("sender", 0, "ספק מתוקן");
    app.setAllowDiscard(false);
    const before = mock.calls.length;
    await app.openRecord(1);
    assert.equal(mock.calls.length, before);
    assert.equal(app.reason("sender").props.value, "Uncommitted correction");
    app.setAllowDiscard(true); mock.fail("source"); await app.openRecord(1);
    assert.match(text(app.tree), /פתיחת הרשומה נכשלה/);
    assert.equal(app.action("sender").props.value, "correct_and_approve");
    assert.equal(app.reason("sender").props.value, "Uncommitted correction");
    assert.equal(mock.commands.length, 0);
  } finally { globalThis.fetch = original; }
});

test("record navigation restores separate unsaved drafts even when documents share a form ID", async () => {
  const original = globalThis.fetch, mock = recordsServer(); globalThis.fetch = mock.fetch;
  try {
    const app = appHarness(); await app.click("חיפוש / רענון"); await app.openRecord();
    app.value("sender", 0, "טיוטה למסמך הראשון"); app.decide("sender", "leave_unresolved", "נימוק ראשון"); app.operator("בודקת ראשונה");
    await app.openRecord(2);
    assert.notEqual(app.nodes().find(node => node.props["data-value-field"] === "sender")!.props.value, "טיוטה למסמך הראשון");
    app.value("sender", 0, "טיוטה למסמך השני"); app.operator("בודקת שנייה");
    await app.openRecord();
    assert.equal(app.nodes().find(node => node.props["data-value-field"] === "sender")!.props.value, "טיוטה למסמך הראשון");
    assert.equal(app.action("sender").props.value, "leave_unresolved"); assert.equal(app.reason("sender").props.value, "נימוק ראשון");
    assert.equal(app.nodes().find(node => node.props.id === "operator-declaration")!.props.value, "בודקת ראשונה");
    await app.openRecord(2);
    assert.equal(app.nodes().find(node => node.props["data-value-field"] === "sender")!.props.value, "טיוטה למסמך השני");
    assert.equal(mock.commands.length, 0);
  } finally { globalThis.fetch = original; }
});

test("a cached unsaved draft cannot be applied silently to a newer server revision", async () => {
  const original = globalThis.fetch, mock = recordsServer(); globalThis.fetch = mock.fetch;
  try {
    const app = appHarness(); await app.click("חיפוש / רענון"); await app.openRecord();
    app.value("sender", 0, "טיוטה ישנה"); await app.openRecord(2);
    mock.forms.get("doc-0/one")!.revision++;
    await app.openRecord();
    assert.match(text(app.tree), /הרשומה השתנתה בשרת/);
    assert.notEqual(app.nodes().find(node => node.props["data-value-field"] === "sender")!.props.value, "טיוטה ישנה");
    assert.equal(mock.commands.length, 0);
  } finally { globalThis.fetch = original; }
});
