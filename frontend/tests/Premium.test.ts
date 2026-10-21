import { test } from "node:test";
import assert from "node:assert/strict";
import { fields } from "../src/api.ts";
import type { DemoRun } from "../src/api.ts";
import { appHarness, text } from "./appHarness.ts";

function fixture(): DemoRun {
  return { run_id: "synthetic-demo", review_token: "bound", status: "succeeded", data_origin: "live",
    form_ids: ["synthetic-form"], provider: "offline_mock", model: "synthetic-v1", failure: null,
    selected_form: { form_id: "synthetic-form", revision: 0, review_status: "unresolved",
      fields: fields.map(field_name => ({ field_name,
        provider_values: field_name === "israeli_id" ? [] : [{ raw: field_name === "sender" ? "לאומית" : "000123", normalized: null }],
        provider_value_status: field_name === "israeli_id" ? "missing" : "present", provider_review_status: "unreviewed",
        reviewed_values: [], review_status: "unreviewed" })) } };
}

test("public demo warns against patient documents and never preselects external AI consent", () => {
  const app = appHarness();
  assert.match(text(app.tree), /אין להעלות מסמכי מטופלים אמיתיים או מידע רפואי אישי/);
  assert.match(text(app.tree), /Gemini/);
  assert.equal(app.nodes().find(n => n.props["aria-label"] === "אישור עיבוד חיצוני לכל הקבצים")!.props.checked, false);
});

test("RTL shell navigates without discarding the active unsaved review or posting new extraction", async () => {
  const original = globalThis.fetch; const calls: string[] = [];
  globalThis.fetch = async (input, options) => {
    calls.push(`${options?.method ?? "GET"} ${String(input)}`);
    return Response.json(fixture());
  };
  try {
    const app = appHarness(); await app.upload(); app.operator("בודקת סינתטית");
    app.decide("first_name", "correct_and_approve", "נבדק מול המקור");
    assert.equal(app.tree.props.dir, "rtl");
    await app.click("רשומות שמורות");
    assert.equal(app.button("רשומות שמורות").props["aria-current"], "page");
    assert.equal(app.nodes().find(n => n.type === "section" && n.props["aria-label"] === "רשומות שמורות")!.props.hidden, false);
    await app.click("עיבוד וביקורת");
    assert.equal(app.action("first_name").props.value, "correct_and_approve");
    assert.equal(app.reason("first_name").props.value, "נבדק מול המקור");
    assert.equal(calls.filter(call => call.startsWith("POST")).length, 1);
  } finally { globalThis.fetch = original; }
});

test("drag and drop accepts a bounded multi-file queue but never supplies consent implicitly", () => {
  const app = appHarness();
  const pdf = new File(["%PDF synthetic"], "demo.pdf", { type: "application/pdf" });
  app.drop([pdf, pdf]);
  assert.equal(app.nodes().filter(n => n.props["data-upload-status"] === "queued").length, 2);
  assert.ok(app.button("העלאה וחילוץ").props.disabled);
  app.drop([new File(["invalid"], "script.html", { type: "text/html" })]);
  assert.match(text(app.tree), /סוג הקובץ אינו נתמך/);
  assert.ok(app.button("העלאה וחילוץ").props.disabled);
  app.drop([pdf]);
  assert.match(text(app.tree), /demo.pdf/);
  assert.ok(app.button("העלאה וחילוץ").props.disabled, "Dropping a valid PDF cannot authorize provider processing");
});

test("present source values never increase the human approval count; missing ID cannot be approved as extracted", async () => {
  const original = globalThis.fetch; globalThis.fetch = async () => Response.json(fixture());
  try {
    const app = appHarness(); await app.upload();
    const count = app.nodes().find(n => n.props["data-human-approved-count"] !== undefined)!;
    assert.equal(count.props["data-human-approved-count"], 0);
    assert.equal(app.nodes().some(n => n.props.role === "progressbar"), false, "No seven-approval progress task is imposed by the UI");
    const missing = app.nodes().find(n => n.props["data-field"] === "israeli_id")!;
    assert.match(String(missing.props.className), /needs-attention/);
    const approve = app.action("israeli_id").props.children[1].find((n: any) => n.props.value === "approve_as_extracted");
    assert.ok(approve.props.disabled);
    assert.match(text(app.tree), /אישור שדה ריק/);
    assert.ok(app.button("שמירת החלטות הביקורת").props.disabled);
    assert.match(text(app.tree), /ספק מדומה · נתוני הדגמה/);
    for (const name of ["central_numbers", "commitment_numbers"]) {
      app.focusField(name);
      const input = app.nodes().find(n => n.props["data-value-field"] === name)!;
      assert.equal(input.props.dir, "ltr"); assert.equal(input.props.value, "000123");
    }
  } finally { globalThis.fetch = original; }
});

test("failed extraction has no review form or success claim and retains the failure-report action", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => Response.json({ ...fixture(), status: "failed", selected_form: null, form_ids: [],
    failure: { code: "PROVIDER_TIMEOUT", message: "Extraction failed", retryable: true } } satisfies DemoRun);
  try {
    const app = appHarness(); await app.upload();
    assert.match(text(app.tree), /לא נוצרה רשומה עסקית/);
    assert.match(text(app.tree), /PROVIDER_TIMEOUT/);
    assert.ok(!app.button("הורדת דוח כשל").props.disabled);
    assert.equal(app.nodes().filter(n => n.type === "fieldset").length, 0);
    assert.equal(app.nodes().filter(n => n.props.role === "progressbar").length, 0);
    assert.doesNotMatch(text(app.tree), /המסמך חולץ — ממשיכים לבדיקה/);
  } finally { globalThis.fetch = original; }
});

test("export confirms only after the server returns a file and errors never claim success", async () => {
  const original = globalThis.fetch;
  let complete!: (response: Response) => void;
  globalThis.fetch = async input => String(input).endsWith("export.xlsx")
    ? new Promise<Response>(resolve => { complete = resolve; }) : Response.json(fixture());
  try {
    const app = appHarness(); await app.upload();
    const first = app.click("ייצוא ל־Excel");
    assert.doesNotMatch(text(app.tree), /התקבל מהשרת והועבר להורדה/);
    complete(Response.json({ error: { code: "BUSINESS_STORAGE_UNAVAILABLE" } }, { status: 503 }));
    await first;
    assert.match(text(app.tree), /הורדת הקובץ נכשלה/);
    assert.doesNotMatch(text(app.tree), /התקבל מהשרת והועבר להורדה/);
    const second = app.click("ייצוא ל־Excel");
    complete(new Response("synthetic workbook")); await second;
    assert.match(text(app.tree), /קובץ ה־Excel התקבל מהשרת והועבר להורדה בדפדפן/);
    assert.equal(await app.download!.text(), "synthetic workbook");
  } finally { globalThis.fetch = original; }
});

test("empty saved-record results show an honest empty state and cannot fabricate records", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => Response.json({ items: [], total: 0, limit: 20, offset: 0 });
  try {
    const app = appHarness(); await app.click("רשומות שמורות"); await app.click("חיפוש / רענון");
    assert.match(text(app.tree), /אין רשומות להצגה/);
    assert.equal(app.nodes().filter(n => String(n.props["aria-label"] ?? "").startsWith("פתיחת רשומה ")).length, 0);
    assert.ok(app.button("הקודם").props.disabled); assert.ok(app.button("הבא").props.disabled);
  } finally { globalThis.fetch = original; }
});
