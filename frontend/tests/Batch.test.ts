import { test } from "node:test";
import assert from "node:assert/strict";
import { fields } from "../src/api.ts";
import type { DemoRun } from "../src/api.ts";
import { appHarness, text } from "./appHarness.ts";

function run(id: string, count = 2): DemoRun {
  return { run_id: id, review_token: `token-${id}`, status: "succeeded", data_origin: "live", provider: "offline_mock", model: "synthetic", form_ids: ["one"], failure: null,
    selected_form: { form_id: "one", revision: 0, review_status: "unresolved", finalized: false,
      fields: fields.map(field_name => ({ field_name, provider_value_status: "present", provider_review_status: "unreviewed", review_status: "unreviewed", reviewed_values: [], requires_review: field_name === "sample_receipt_date", review_reasons: field_name === "sample_receipt_date" ? ["INVALID_DATE"] : [],
        provider_values: (field_name === "commitment_numbers" ? Array.from({ length: count }, (_, i) => String(i).padStart(6, "0")) : ["012345678"]).map(raw => ({ raw, normalized: null })) })) } };
}

test("bulk export uses all filtered pages, grouped documents, explicit selection and no review writes", async () => {
  const old = globalThis.fetch, bodies: any[] = [], calls: string[] = [];
  const value = run("doc", 30);
  globalThis.fetch = async (input, options) => {
    calls.push(`${options?.method ?? "GET"} ${input}`);
    if (String(input).endsWith("business-records/export.xlsx")) { bodies.push(JSON.parse(String(options?.body))); return new Response("synthetic xlsx"); }
    return Response.json(value);
  };
  try {
    const app = appHarness(); assert.equal(app.button("ייצוא כל השורות לאקסל").props.disabled, true);
    await app.upload(); assert.equal(app.rowActions().length, 20);
    await app.click("ייצוא כל השורות לאקסל");
    assert.equal(bodies[0].forms.length, 1); assert.equal(bodies[0].forms[0].commitments.length, 30);
    assert.equal(bodies[0].forms[0].expected_revision, 0); assert.equal(bodies[0].forms[0].review_token, "token-doc");
    app.check("בחירת כל השורות בסינון בכל העמודים"); await app.click("ייצוא שורות מסומנות");
    assert.equal(bodies[1].forms[0].commitments.length, 30);
    app.input("סינון שולחן העבודה", "approved");
    assert.equal(app.button("ייצוא כל השורות לאקסל").props.disabled, true);
    assert.ok(calls.every(path => !/\/review|\/finalize/.test(path)));
  } finally { globalThis.fetch = old; }
});

test("two documents are processed sequentially; a provider failure does not erase successes or retry", async () => {
  const old = globalThis.fetch; let requests = 0, active = 0, maximum = 0;
  globalThis.fetch = async (input, options) => {
    if (options?.body instanceof FormData) {
      requests++; active++; maximum = Math.max(maximum, active);
      assert.equal(options.body.get("allow_external_processing"), "true");
      await new Promise(resolve => setTimeout(resolve, 5)); active--;
      return Response.json(requests === 2 ? { ...run("failed"), status: "failed", form_ids: [], selected_form: null,
        failure: { code: "PROVIDER_UNAVAILABLE", message: "safe", retryable: true } } : run(`doc-${requests}`));
    }
    return Response.json(run("doc"));
  };
  try {
    const app = appHarness(); const files = [1, 2, 3].map(i => new File(["%PDF synthetic"], `${i}.pdf`, { type: "application/pdf" }));
    app.drop(files); assert.equal(requests, 0); assert.equal(app.button("העלאה וחילוץ").props.disabled, true);
    app.check("אישור עיבוד חיצוני לכל הקבצים"); await app.click("העלאה וחילוץ");
    assert.equal(requests, 3); assert.equal(maximum, 1);
    assert.equal(app.nodes().filter(n => n.props["data-upload-status"] === "succeeded").length, 2);
    assert.equal(app.nodes().filter(n => n.props["data-upload-status"] === "failed").length, 1);
    assert.equal(app.rowActions().length, 4);
    assert.equal(app.button("העלאה וחילוץ").props.disabled, true);
  } finally { globalThis.fetch = old; }
});

test("rate limit pauses remaining files without retrying the failed file", async () => {
  const old = globalThis.fetch; let requests = 0;
  globalThis.fetch = async () => { requests++; return Response.json({ ...run("limited"), status: "failed", form_ids: [], selected_form: null,
    failure: { code: "PROVIDER_RATE_LIMIT", message: "safe", retryable: true } }); };
  try {
    const app = appHarness(); app.drop([1, 2, 3].map(i => new File(["%PDF synthetic"], `${i}.pdf`, { type: "application/pdf" })));
    app.check("אישור עיבוד חיצוני לכל הקבצים"); await app.click("העלאה וחילוץ");
    assert.equal(requests, 1); assert.equal(app.nodes().filter(n => n.props["data-upload-status"] === "queued").length, 2);
    assert.match(text(app.tree), /התור הושהה עקב מגבלת קצב/);
  } finally { globalThis.fetch = old; }
});

test("unknown network result pauses the queue and is never retried automatically", async () => {
  const old = globalThis.fetch; let requests = 0;
  globalThis.fetch = async () => { requests++; throw new Error("Offline disconnect"); };
  try {
    const app = appHarness(); app.drop([1, 2].map(i => new File(["%PDF synthetic"], `${i}.pdf`, { type: "application/pdf" })));
    app.check("אישור עיבוד חיצוני לכל הקבצים"); await app.click("העלאה וחילוץ");
    assert.equal(requests, 1); assert.match(text(app.tree), /UNKNOWN_UPLOAD_RESULT/);
    assert.equal(app.nodes().filter(n => n.props["data-upload-status"] === "queued").length, 1);
  } finally { globalThis.fetch = old; }
});

test("draft commitment identities cannot silently select different persisted rows for export", async () => {
  const old = globalThis.fetch; let exports = 0;
  globalThis.fetch = async input => { if (String(input).includes("export.xlsx")) exports++; return Response.json(run("doc")); };
  try {
    const app = appHarness(); await app.upload();
    app.value("commitment_numbers", 0, "new-draft-number"); app.decide("commitment_numbers", "correct_and_approve", "Synthetic source review");
    app.setAllowDiscard(true); await app.click("ייצוא כל השורות לאקסל");
    assert.equal(exports, 0); assert.match(text(app.tree), /יש לשמור או לבטל טיוטות של מספרי התחייבות/);
  } finally { globalThis.fetch = old; }
});
