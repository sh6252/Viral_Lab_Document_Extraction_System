import { test } from "node:test";
import assert from "node:assert/strict";
import { configureAPI, fields, setCSRFToken } from "../src/api.ts";
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

test("rate limit marks each file failed without retrying or bypassing the server limit", async () => {
  const old = globalThis.fetch; let requests = 0;
  globalThis.fetch = async () => { requests++; return Response.json({ ...run("limited"), status: "failed", form_ids: [], selected_form: null,
    failure: { code: "PROVIDER_RATE_LIMIT", message: "safe", retryable: true } }); };
  try {
    const app = appHarness(); app.drop([1, 2, 3].map(i => new File(["%PDF synthetic"], `${i}.pdf`, { type: "application/pdf" })));
    app.check("אישור עיבוד חיצוני לכל הקבצים"); await app.click("העלאה וחילוץ");
    assert.equal(requests, 3); assert.equal(app.nodes().filter(n => n.props["data-upload-status"] === "failed").length, 3);
    assert.match(text(app.tree), /עיבוד האצווה הסתיים/);
  } finally { globalThis.fetch = old; }
});

test("unknown network results advance to distinct files and never retry the ambiguous uploads", async () => {
  const old = globalThis.fetch; let requests = 0;
  globalThis.fetch = async () => { requests++; throw new Error("Offline disconnect"); };
  try {
    const app = appHarness(); app.drop([1, 2].map(i => new File(["%PDF synthetic"], `${i}.pdf`, { type: "application/pdf" })));
    app.check("אישור עיבוד חיצוני לכל הקבצים"); await app.click("העלאה וחילוץ");
    assert.equal(requests, 2); assert.match(text(app.tree), /UNKNOWN_UPLOAD_RESULT/);
    assert.equal(app.nodes().filter(n => n.props["data-upload-status"] === "queued").length, 0);
    assert.match(text(app.tree), /יש לבדוק ברשומות השמורות/);
  } finally { globalThis.fetch = old; }
});

for (const scenario of [
  { label: "first 503 fails then second succeeds", results: [503, 201] },
  { label: "all four fail with HTTP 503", results: [503, 503, 503, 503] },
  { label: "all four succeed", results: [201, 201, 201, 201] },
  { label: "504 timeout then success", results: [504, 201] },
  { label: "429 provider limit then success without retry", results: [429, 201] },
  { label: "database 503 without a saved failure then success", results: ["database", 201] },
  { label: "unstructured HTTP 503 then success", results: ["html", 201] },
  { label: "unknown network outcome then success", results: ["network", 201] },
  { label: "successes survive an intervening HTTP 503", results: [201, 503, 201] },
] as const) {
  test(`cloud batch automatically completes: ${scenario.label}`, async () => {
    const old = globalThis.fetch, names: string[] = [];
    let active = 0, maximum = 0;
    configureAPI({ baseURL: "", publicDemo: true }); setCSRFToken("synthetic-csrf-only");
    globalThis.fetch = async (input, options) => {
      assert.equal(input, "/api/v1/demo-runs"); assert.equal(options?.method, "POST");
      assert.ok(options.body instanceof FormData);
      assert.equal(options.body.get("allow_external_processing"), "true");
      assert.equal(new Headers(options.headers).get("X-Demo-CSRF"), "synthetic-csrf-only");
      const result = scenario.results[names.length];
      names.push((options.body.get("file") as File).name);
      active++; maximum = Math.max(maximum, active);
      try {
        await new Promise(resolve => setTimeout(resolve, 5));
        if (result === "network") throw new Error("SECRET_NETWORK_DETAIL");
        if (result === "html") return new Response("SECRET_PROXY_DETAIL", { status: 503 });
        if (result === "database") return Response.json({ error: { code: "CLOUD_DATABASE_NOT_READY" } }, { status: 503 });
        const value = run(`doc-${names.length}`);
        if (result === 201) return Response.json(value, { status: 201 });
        const code = result === 504 ? "PROVIDER_TIMEOUT" : result === 429 ? "PROVIDER_RATE_LIMIT" : "PROVIDER_UNAVAILABLE";
        return Response.json({ ...value, review_token: value.review_token.padEnd(48, "x"), status: "failed", form_ids: [], selected_form: null,
          failure: { code, message: "SECRET_PROVIDER_DETAIL", retryable: true }, error: { code },
          diagnostic: { stage: "provider_request", provider_http_status: result } }, { status: result });
      } finally { active--; }
    };
    try {
      const app = appHarness();
      const files = scenario.results.map((_, i) => new File(["%PDF synthetic"], `${i}.pdf`, { type: "application/pdf" }));
      app.drop(files); app.check("אישור עיבוד חיצוני לכל הקבצים");
      await app.click("העלאה וחילוץ");
      assert.deepEqual(names, files.map(file => file.name)); assert.equal(maximum, 1);
      const succeeded = scenario.results.filter(result => result === 201).length;
      assert.equal(app.nodes().filter(n => n.props["data-upload-status"] === "succeeded").length, succeeded);
      assert.equal(app.nodes().filter(n => n.props["data-upload-status"] === "failed").length, files.length - succeeded);
      assert.match(text(app.tree), /עיבוד האצווה הסתיים/);
      assert.ok(text(app.tree).includes(`הושלמו: ${succeeded} · נכשלו או לא אומתו: ${files.length - succeeded}`));
      assert.doesNotMatch(text(app.tree), /SECRET_/);
      assert.equal(app.rowActions().length, succeeded * 2);
      assert.equal(app.button("העלאה וחילוץ").props.disabled, true);
      if (scenario.results.some(result => result === 503)) assert.match(text(app.tree), /HTTP 503/);
    } finally { globalThis.fetch = old; configureAPI({ baseURL: "", publicDemo: false }); }
  });
}

test("rapid duplicate clicks cannot start a second upload loop", async () => {
  const old = globalThis.fetch, names: string[] = [];
  let release!: () => void;
  const waiting = new Promise<void>(resolve => { release = resolve; });
  globalThis.fetch = async (_input, options) => {
    names.push(((options!.body as FormData).get("file") as File).name);
    await waiting;
    return Response.json(run(`doc-${names.length}`), { status: 201 });
  };
  try {
    const app = appHarness(); app.drop([1, 2].map(i => new File(["%PDF synthetic"], `${i}.pdf`, { type: "application/pdf" })));
    app.check("אישור עיבוד חיצוני לכל הקבצים");
    const staleClick = app.button("העלאה וחילוץ").props.onClick;
    const completion = app.click("העלאה וחילוץ");
    await staleClick(); assert.equal(names.length, 1);
    assert.equal(app.button("העלאה וחילוץ").props.disabled, true);
    assert.ok(text(app.tree).includes("בעיבוד: 1 · ממתינים: 1"));
    assert.doesNotMatch(text(app.tree), /עיבוד האצווה הסתיים/);
    release(); await completion;
    assert.deepEqual(names, ["1.pdf", "2.pdf"]);
    assert.equal(app.button("העלאה וחילוץ").props.disabled, true);
  } finally { release(); globalThis.fetch = old; }
});

for (const status of [401, 403]) {
  test(`authentication boundary ${status} stops further uploads`, async () => {
    const old = globalThis.fetch; let requests = 0;
    globalThis.fetch = async () => { requests++; return Response.json({ error: { code: "AUTHENTICATION_REQUIRED" } }, { status }); };
    try {
      const app = appHarness(); app.drop([1, 2].map(i => new File(["%PDF synthetic"], `${i}.pdf`, { type: "application/pdf" })));
      app.check("אישור עיבוד חיצוני לכל הקבצים"); await app.click("העלאה וחילוץ");
      assert.equal(requests, 1);
      assert.equal(app.nodes().filter(n => n.props["data-upload-status"] === "queued").length, 1);
      assert.match(text(app.tree), /העיבוד נעצר עקב חסימת גישה/);
      assert.doesNotMatch(text(app.tree), /עיבוד האצווה הסתיים/);
    } finally { globalThis.fetch = old; }
  });
}

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
