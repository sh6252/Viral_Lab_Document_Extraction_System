import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fields } from "../src/api.ts";
import type { DemoRun, ReviewCommand, SelectedForm } from "../src/api.ts";
import { appHarness, text } from "./appHarness.ts";

function server(commitments = 3) {
  const source = { first_name: ["שרה"], last_name: ["כהן"], israeli_id: ["012345678"], central_numbers: ["803215", "803216"],
    sample_receipt_date: ["18.08.2026"], sender: ["לאומית"], commitment_numbers: ["004", "005", "006"] };
  const forms: SelectedForm[] = ["one", "two"].map((form_id, index) => ({ form_id, revision: 0, review_status: "unresolved",
    fields: fields.map(field_name => ({ field_name, provider_values: (index === 1 && field_name === "israeli_id" ? []
      : index === 0 && field_name === "commitment_numbers" && commitments !== 3
        ? Array.from({ length: commitments }, (_, i) => String(i).padStart(6, "0")) : source[field_name]).map(raw => ({ raw, normalized: null })),
      provider_value_status: index === 1 && field_name === "israeli_id" ? "missing" : "present", provider_review_status: "unreviewed",
      reviewed_values: [], review_status: "unreviewed" })) }));
  const commands: ReviewCommand[] = [], calls: string[] = [];
  const run = (form = forms[0]): DemoRun => ({ run_id: "synthetic-doc", review_token: "bound", status: "succeeded", data_origin: "live",
    form_ids: forms.map(form => form.form_id), provider: "offline_mock", model: "synthetic-only", failure: null, selected_form: structuredClone(form) });
  const fetch: typeof globalThis.fetch = async (input, options) => {
    const url = new URL(String(input), "http://127.0.0.1:5173");
    calls.push(`${options?.method ?? "GET"} ${url.pathname}`);
    assert.equal(new Headers(options?.headers).get("X-Local-Demo"), "1");
    if (url.pathname === "/api/v1/business-records") return Response.json({ total: 2, offset: 0, limit: 20, items: forms.map(form => ({
      run_id: "synthetic-doc", document_id: "synthetic-doc", form_id: form.form_id, revision: form.revision, review_status: "pending",
      summary: Object.fromEntries(form.fields.map(field => [field.field_name, field.provider_values.map(v => v.raw)])),
      created_at: "2026-08-18T00:00:00Z", updated_at: "2026-08-18T00:00:00Z" })) });
    if (url.pathname.endsWith("/source")) return new Response("%PDF synthetic preview", { headers: { "Content-Type": "application/pdf" } });
    if (url.pathname.endsWith("/review")) {
      const body = JSON.parse(String(options?.body)) as ReviewCommand; commands.push(body);
      const form = forms.find(form => url.pathname.includes(`/forms/${form.form_id}/`))!;
      assert.equal(body.expected_revision, form.revision);
      for (const action of body.actions) {
        const field = form.fields.find(field => field.field_name === action.field_name)!;
        field.reviewed_values = action.reviewed_values;
        field.review_status = action.action === "acknowledge_blank" ? "acknowledged_blank" : action.action === "correct_and_approve" ? "approved_corrected" : "approved_provider";
        form.revision++;
      }
      form.review_status = form.fields.every(field => field.review_status !== "unreviewed") ? "complete" : "unresolved";
      return Response.json(run(form));
    }
    return Response.json(run(forms.find(form => form.form_id === url.searchParams.get("form_id"))));
  };
  return { fetch, calls, commands, forms };
}

test("table renders one row per commitment and highlights only the shared missing-ID cells", async () => {
  const original = globalThis.fetch, mock = server(); globalThis.fetch = mock.fetch;
  try {
    const app = appHarness(); await app.upload();
    assert.equal(app.nodes().filter(node => node.props["data-work-row"]).length, 6);
    assert.equal(app.nodes().filter(node => node.type === "th").length, 11, "Selection, actions, document, seven business columns and review status");
    const highlighted = app.nodes().filter(node => node.props["data-attention"]);
    assert.equal(highlighted.length, 3);
    assert.ok(highlighted.every(node => node.props["data-column"] === "israeli_id"));
    assert.equal(app.nodes().filter(node => node.type === "fieldset").length, 0, "An unflagged form imposes no seven-field editor");
    assert.match(text(app.tree), /אין דגלים מדווחים/);
    assert.match(text(app.tree), /אינו אומדן ביטחון ואינו אישור אנושי/);
    assert.equal(app.nodes().find(node => node.props.type === "file")!.props.multiple, true);
    assert.equal(mock.calls.filter(call => call.startsWith("POST")).length, 1);
  } finally { globalThis.fetch = original; }
});

test("exception-only save posts one acknowledgment, removes flags and leaves six fields unapproved", async () => {
  const original = globalThis.fetch, mock = server(); globalThis.fetch = mock.fetch;
  try {
    const app = appHarness(); await app.upload();
    app.input("סינון שולחן העבודה", "attention");
    assert.equal(app.nodes().filter(node => node.props["data-work-row"]).length, 3);
    await app.click("החריגה הבאה");
    assert.equal(app.nodes().filter(node => node.type === "fieldset").length, 1);
    app.operator("בודקת סינתטית"); app.decide("israeli_id", "acknowledge_blank", "השדה חסר במסמך הסינתטי");
    await app.click("שמירת החלטות הביקורת");
    assert.equal(mock.commands.length, 1); assert.equal(mock.commands[0].actions.length, 1);
    assert.equal(mock.commands[0].actions[0].field_name, "israeli_id");
    assert.ok(mock.forms[1].fields.filter(field => field.field_name !== "israeli_id").every(field => field.review_status === "unreviewed"));
    assert.equal(mock.forms[1].review_status, "unresolved");
    assert.equal(mock.forms[1].revision, 1);
    assert.equal(app.nodes().filter(node => node.props["data-work-row"]).length, 0);
    assert.equal(app.action("israeli_id").props.value, "acknowledge_blank");
    assert.match(text(app.tree), /ממתין לבדיקה/);
  } finally { globalThis.fetch = original; }
});

test("shared form correction updates every commitment row as draft, then as persisted human correction", async () => {
  const original = globalThis.fetch, mock = server(); globalThis.fetch = mock.fetch;
  try {
    const app = appHarness(); await app.upload(); app.operator("בודק סינתטי");
    app.decide("first_name", "correct_and_approve", "תיקון משותף לטופס"); app.value("first_name", 0, "שם מתוקן");
    const cells = () => app.nodes().filter(node => node.props["data-column"] === "first_name");
    assert.ok(cells().slice(0, 3).every(node => text(node).includes("שם מתוקן") && text(node).includes("טיוטה")));
    assert.ok(cells().slice(3).every(node => text(node).includes("שרה") && !text(node).includes("שם מתוקן")));
    assert.equal(mock.forms[0].fields[0].provider_values[0].raw, "שרה");
    await app.click("שמירת החלטות הביקורת");
    assert.equal(mock.commands[0].actions.length, 1);
    assert.ok(cells().slice(0, 3).every(node => text(node).includes("שם מתוקן") && text(node).includes("תוקן ואושר אנושית")));
    assert.equal(mock.forms[1].revision, 0);
  } finally { globalThis.fetch = original; }
});

test("50 commitments are paginated 20/20/10 and filtering resets the table page", async () => {
  const original = globalThis.fetch, mock = server(47); globalThis.fetch = mock.fetch;
  try {
    const app = appHarness(); await app.upload();
    const count = () => app.nodes().filter(node => node.props["data-work-row"]).length;
    assert.equal(count(), 20); await app.click("עמוד הבא בטבלה"); assert.equal(count(), 20);
    await app.click("עמוד הבא בטבלה"); assert.equal(count(), 10);
    assert.ok(app.button("עמוד הבא בטבלה").props.disabled);
    app.input("סינון שולחן העבודה", "attention"); assert.equal(count(), 3);
    assert.ok(app.button("עמוד קודם בטבלה").props.disabled);
  } finally { globalThis.fetch = original; }
});

test("loading saved forms into the workbench uses GET only and saved list expands commitment rows", async () => {
  const original = globalThis.fetch, mock = server(); globalThis.fetch = mock.fetch;
  try {
    const app = appHarness(); await app.click("רשומות שמורות"); await app.click("חיפוש / רענון");
    assert.equal(app.nodes().filter(node => node.type === "button" && String(node.props["aria-label"] ?? "").startsWith("פתיחת רשומה ")).length, 6);
    await app.click("צירוף העמוד לשולחן העבודה");
    assert.equal(app.nodes().filter(node => node.props["data-work-row"]).length, 6);
    assert.ok(mock.calls.every(call => call.startsWith("GET")));
    assert.match(text(app.tree), /לא בוצע חילוץ חדש/);
  } finally { globalThis.fetch = original; }
});

test("wordmark, title and favicon use consistent branding with no Hebrew diacritics", () => {
  const app = appHarness();
  const brand = app.nodes().find(node => node.type === "a" && node.props.className === "brand")!;
  const title = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const icon = readFileSync(new URL("../public/brand.svg", import.meta.url), "utf8");
  assert.match(text(brand), /מחויבים ל־100/); assert.match(title, /<title>מחויבים ל־100/); assert.match(title, /brand\.svg/);
  assert.match(text(brand), /מטופסי התחייבות לנתונים מסודרים/);
  assert.equal(brand.props["aria-label"], "מחויבים ל־100 — מסך ראשי");
  assert.ok(app.nodes().some(node => node.type === "bdi" && node.props.dir === "ltr" && text(node) === "100"));
  assert.ok(app.nodes().some(node => node.type === "img" && node.props.src === "/brand.svg" && node.props.alt === ""));
  assert.doesNotMatch(text(app.tree) + title + icon, /סדורה|100\s*[%٪]|[\u0591-\u05BD\u05BF-\u05C2\u05C4-\u05C5\u05C7]/u);
  assert.match(icon, /<svg/); assert.doesNotMatch(icon, /https?:\/\/[^" ]+\.(png|jpg|woff)/);
  assert.match(icon, /מחויבים ל־100/);
  assert.match(icon, /#122e40/); assert.match(icon, /#087f7a/);
});

test("typed correction without a disposition is preserved on cancelled document replacement and never submitted", async () => {
  const original = globalThis.fetch, mock = server(); globalThis.fetch = mock.fetch;
  try {
    const app = appHarness(); await app.upload();
    app.value("first_name", 0, "טיוטה בלי אישור");
    app.setAllowDiscard(false);
    app.drop([new File(["%PDF synthetic"], "other.pdf", { type: "application/pdf" })]);
    const value = app.nodes().find(node => node.props["data-value-field"] === "first_name")!;
    assert.equal(value.props.value, "טיוטה בלי אישור");
    assert.equal(app.confirmations, 1);
    assert.equal(mock.commands.length, 0);
    assert.equal(app.action("first_name").props.value, "");
  } finally { globalThis.fetch = original; }
});

test("partial form-read failure never fabricates missing rows or retries provider extraction", async () => {
  const original = globalThis.fetch, mock = server();
  globalThis.fetch = async (input, options) => new URL(String(input), "http://127.0.0.1").searchParams.get("form_id") === "two"
    ? Response.json({ error: { code: "BUSINESS_STORAGE_UNAVAILABLE" } }, { status: 503 }) : mock.fetch(input, options);
  try {
    const app = appHarness(); await app.upload();
    assert.equal(app.nodes().filter(node => node.props["data-work-row"]).length, 3);
    assert.match(text(app.tree), /חלק מהטפסים לא נטענו לטבלה/);
    assert.equal(mock.calls.filter(call => call.startsWith("POST")).length, 1);
  } finally { globalThis.fetch = original; }
});
