import { test } from "node:test";
import assert from "node:assert/strict";
import { configureAPI, fields, setCSRFToken } from "../src/api.ts";
import type { DemoRun, ReviewCommand, FinalizeCommand } from "../src/api.ts";
import { appHarness, text } from "./appHarness.ts";

function gate() {
  let resolve!: () => void;
  const promise = new Promise<void>(done => { resolve = done; });
  return { promise, resolve };
}

function document(id: string): DemoRun {
  return { run_id: id, review_token: `synthetic-${id}`.padEnd(48, "x"), status: "succeeded", data_origin: "live",
    provider: "offline_mock", model: "synthetic", form_ids: ["same-form-id"], failure: null,
    selected_form: { form_id: "same-form-id", revision: 0, finalized: false, review_status: "unresolved",
      automatic_acceptance: false, review_policy_version: "human-form-attestation-v1",
      fields: fields.map(field_name => ({ field_name, provider_value_status: "present", provider_review_status: "unreviewed",
        review_status: "unreviewed", reviewed_values: [], requires_review: false, review_reasons: [],
        provider_values: [{ raw: `${id}-${field_name}`, normalized: null }] })) } };
}

function server(options: { failSecond?: boolean; holdReview?: boolean; holdFinal?: boolean } = {}) {
  const docs = new Map(["one", "two", "three"].map(id => [id, document(id)]));
  const second = gate(), third = gate(), secondStarted = gate(), thirdStarted = gate(), reviewStarted = gate(), finalStarted = gate();
  const review = gate(), final = gate();
  const uploads: string[] = [], commands: { path: string; body: ReviewCommand | FinalizeCommand }[] = [];
  let active = 0, maximum = 0, overlap = false;
  const fetch: typeof globalThis.fetch = async (input, request) => {
    const url = new URL(String(input), "https://example.test"), path = url.pathname;
    const headers = new Headers(request?.headers);
    assert.equal(headers.get("X-Demo-Request"), "1");
    if (request?.method === "POST") assert.equal(headers.get("X-Demo-CSRF"), "synthetic-csrf");
    if (request?.body instanceof FormData) {
      assert.equal(path, "/api/v1/demo-runs");
      assert.equal(request.body.get("allow_external_processing"), "true");
      const id = ((request.body.get("file") as File).name).replace(".pdf", "");
      uploads.push(id); active++; maximum = Math.max(maximum, active);
      try {
        if (id === "two") { secondStarted.resolve(); await second.promise; }
        if (id === "three") { thirdStarted.resolve(); await third.promise; }
        const value = structuredClone(docs.get(id)!);
        if (id === "two" && options.failSecond) return Response.json({ ...value, status: "failed", form_ids: [], selected_form: null,
          failure: { code: "PROVIDER_UNAVAILABLE", message: "SECRET_PROVIDER_BODY", retryable: true }, error: { code: "PROVIDER_UNAVAILABLE" } }, { status: 503 });
        return Response.json(value, { status: 201 });
      } finally { active--; }
    }
    const id = path.split("/")[4], value = docs.get(id)!;
    assert.ok(value, "Only an existing completed document may be reviewed");
    if (path.endsWith("/source")) return new Response("%PDF synthetic", { headers: { "Content-Type": "application/pdf" } });
    if (request?.method === "POST") {
      overlap ||= active === 1;
      const body = JSON.parse(String(request.body)); commands.push({ path, body });
      assert.equal(body.review_token, value.review_token);
      assert.equal(body.expected_revision, value.selected_form!.revision);
      if (path.endsWith("/review")) {
        reviewStarted.resolve(); if (options.holdReview) await review.promise;
        for (const action of (body as ReviewCommand).actions) {
          const field = value.selected_form!.fields.find(field => field.field_name === action.field_name)!;
          field.reviewed_values = action.reviewed_values; field.review_status = "approved_corrected";
        }
      } else {
        assert.ok(path.endsWith("/finalize")); assert.equal(body.attest_all_values, true);
        finalStarted.resolve(); if (options.holdFinal) await final.promise;
        value.selected_form!.finalized = true; value.selected_form!.review_status = "complete";
      }
      value.selected_form!.revision++;
    }
    return Response.json(structuredClone(value));
  };
  return { fetch, docs, uploads, commands, second, third, secondStarted, thirdStarted, reviewStarted, finalStarted, review, final,
    get maximum() { return maximum; }, get overlap() { return overlap; },
    release() { second.resolve(); third.resolve(); review.resolve(); final.resolve(); } };
}

function start(app: ReturnType<typeof appHarness>) {
  app.drop(["one", "two", "three"].map(id => new File(["%PDF synthetic"], `${id}.pdf`, { type: "application/pdf" })));
  app.check("אישור עיבוד חיצוני לכל הקבצים");
  return app.click("העלאה וחילוץ");
}

function firstName(app: ReturnType<typeof appHarness>) {
  app.focusField("first_name");
  return app.nodes().find(node => node.props["data-value-field"] === "first_name")!.props.value;
}

test("document one can be approved while document two extracts; queue continues independently", { timeout: 10000 }, async () => {
  const original = globalThis.fetch, mock = server(); globalThis.fetch = mock.fetch;
  configureAPI({ baseURL: "", publicDemo: true }); setCSRFToken("synthetic-csrf");
  try {
    const app = appHarness(), completion = start(app);
    await mock.secondStarted.promise; app.render();
    assert.equal(app.rowActions().length, 1); assert.equal(app.rowActions()[0].props.disabled, false);
    assert.equal(app.button("העלאה וחילוץ").props.disabled, true);
    await app.openApproval(); app.operator("בודקת סינתטית"); app.attest();
    await app.click("אישור סופי לטופס");
    assert.equal(mock.commands.length, 1); assert.equal(mock.overlap, true);
    assert.equal(mock.docs.get("one")!.selected_form!.finalized, true);
    assert.deepEqual(mock.uploads, ["one", "two"]);
    assert.match(text(app.tree), /הטופס אושר סופית בשרת/);
    mock.second.resolve(); await mock.thirdStarted.promise; app.render();
    assert.equal(firstName(app), "one-first_name");
    mock.third.resolve(); await completion;
    assert.deepEqual(mock.uploads, ["one", "two", "three"]); assert.equal(mock.maximum, 1);
    assert.equal(firstName(app), "one-first_name");
    assert.equal(app.rowActions()[0].props.disabled, true); assert.equal(app.rowActions().length, 3);
  } finally { mock.release(); globalThis.fetch = original; configureAPI({ baseURL: "", publicDemo: false }); }
});

test("incoming result and batch completion preserve unsaved edits, selected source and operator", { timeout: 10000 }, async () => {
  const original = globalThis.fetch, mock = server(); globalThis.fetch = mock.fetch;
  configureAPI({ baseURL: "", publicDemo: true }); setCSRFToken("synthetic-csrf");
  try {
    const app = appHarness(), completion = start(app);
    await mock.secondStarted.promise; app.render();
    app.value("first_name", 0, "synthetic human edit"); app.decide("first_name", "correct_and_approve", "synthetic reason");
    assert.equal(app.nodes().find(node => node.type === "fieldset" && node.props["data-field"] === "first_name")!.props.disabled, false);
    app.operator("בודקת סינתטית");
    mock.second.resolve(); await mock.thirdStarted.promise; app.render();
    assert.equal(firstName(app), "synthetic human edit"); assert.equal(app.action("first_name").props.value, "correct_and_approve");
    assert.equal(app.reason("first_name").props.value, "synthetic reason");
    app.setAllowDiscard(false); await app.openApproval(1); // Explicit navigation still requires consent to discard drafts.
    assert.equal(firstName(app), "synthetic human edit"); assert.equal(app.confirmations, 1);
    mock.third.resolve(); await completion;
    assert.equal(firstName(app), "synthetic human edit"); assert.equal(app.reason("first_name").props.value, "synthetic reason");
    await app.click("שמירת החלטות הביקורת");
    assert.equal(mock.commands[0].body.operator_declaration, "בודקת סינתטית");
    assert.ok(mock.commands[0].path.includes("/one/"));
    assert.equal(mock.maximum, 1); assert.equal(mock.uploads.length, 3);
  } finally { mock.release(); globalThis.fetch = original; configureAPI({ baseURL: "", publicDemo: false }); }
});

test("batch finishing during review save cannot release its lock or overwrite its response", { timeout: 10000 }, async () => {
  const original = globalThis.fetch, mock = server({ holdReview: true, failSecond: true }); globalThis.fetch = mock.fetch;
  configureAPI({ baseURL: "", publicDemo: true }); setCSRFToken("synthetic-csrf");
  try {
    const app = appHarness(), completion = start(app);
    await mock.secondStarted.promise; app.render();
    app.value("first_name", 0, "synthetic correction"); app.operator("בודקת סינתטית");
    const saving = app.act("first_name", "correct_and_approve"); await mock.reviewStarted.promise;
    mock.second.resolve(); await mock.thirdStarted.promise; mock.third.resolve(); await completion;
    assert.equal(app.button("שליחה חוזרת של אותה בקשה").props.disabled, true);
    assert.equal(mock.commands.length, 1);
    mock.review.resolve(); await saving;
    assert.equal(firstName(app), "synthetic correction"); assert.equal(mock.commands.length, 1);
    assert.equal(mock.overlap, true); assert.equal(mock.maximum, 1);
    assert.deepEqual(mock.uploads, ["one", "two", "three"]);
    assert.match(text(app.tree), /נשמרה החלטת ביקורת אחת/);
    assert.match(text(app.tree), /HTTP 503/); assert.doesNotMatch(text(app.tree), /SECRET_PROVIDER_BODY/);
    assert.equal(app.rowActions().length, 2);
  } finally { mock.release(); globalThis.fetch = original; configureAPI({ baseURL: "", publicDemo: false }); }
});

test("manual navigation to a completed document and its source survives later batch arrivals", { timeout: 10000 }, async () => {
  const original = globalThis.fetch, mock = server(); globalThis.fetch = mock.fetch;
  configureAPI({ baseURL: "", publicDemo: true }); setCSRFToken("synthetic-csrf");
  try {
    const app = appHarness(), completion = start(app);
    await mock.secondStarted.promise; mock.second.resolve(); await mock.thirdStarted.promise; app.render();
    await app.openApproval(1); await app.click("סגירת חלון האישור");
    assert.equal(firstName(app), "two-first_name"); assert.equal(app.hasPreview(), true);
    app.value("first_name", 0, "two human draft");
    mock.third.resolve(); await completion;
    assert.equal(firstName(app), "two human draft"); assert.equal(app.hasPreview(), true);
    assert.equal(mock.uploads.length, 3); assert.equal(mock.commands.length, 0);
  } finally { mock.release(); globalThis.fetch = original; configureAPI({ baseURL: "", publicDemo: false }); }
});

test("incoming results do not replace the selected document during an in-flight final approval", { timeout: 10000 }, async () => {
  const original = globalThis.fetch, mock = server({ holdFinal: true }); globalThis.fetch = mock.fetch;
  configureAPI({ baseURL: "", publicDemo: true }); setCSRFToken("synthetic-csrf");
  try {
    const app = appHarness(), completion = start(app);
    await mock.secondStarted.promise; app.render();
    await app.openApproval(); app.operator("בודקת סינתטית"); app.attest();
    const confirming = app.click("אישור סופי לטופס"); await mock.finalStarted.promise;
    mock.second.resolve(); await mock.thirdStarted.promise; mock.third.resolve(); await completion;
    assert.equal(app.button("האישור נשלח…").props.disabled, true);
    assert.equal(mock.commands.length, 1); assert.equal(mock.docs.get("one")!.selected_form!.finalized, false);
    mock.final.resolve(); await confirming;
    assert.equal(firstName(app), "one-first_name");
    assert.equal(mock.docs.get("one")!.selected_form!.finalized, true);
    assert.equal(mock.docs.get("two")!.selected_form!.finalized, false);
    assert.equal(mock.docs.get("three")!.selected_form!.finalized, false);
    assert.equal(mock.uploads.length, 3); assert.equal(mock.maximum, 1);
  } finally { mock.release(); globalThis.fetch = original; configureAPI({ baseURL: "", publicDemo: false }); }
});
