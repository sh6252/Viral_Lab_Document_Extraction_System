import { test } from "node:test";
import assert from "node:assert/strict";
import { configureAPI, request, setCSRFToken, APIError } from "../src/api.ts";
import { appHarness, text } from "./appHarness.ts";

test("public session sends HttpOnly-cookie credentials plus CSRF without a local-demo bypass or bundled password", async () => {
  const original = globalThis.fetch;
  try {
    configureAPI({ baseURL: "", publicDemo: true }); setCSRFToken("synthetic-csrf-only");
    globalThis.fetch = async (url, options) => {
      assert.equal(url, "/api/v1/business-records/export.xlsx");
      const headers = new Headers(options?.headers);
      assert.equal(headers.get("X-Demo-Request"), "1");
      assert.equal(headers.get("X-Demo-CSRF"), "synthetic-csrf-only");
      assert.equal(headers.get("X-Local-Demo"), null);
      assert.equal(headers.get("Authorization"), null);
      assert.equal(options?.credentials, "include");
      return new Response("synthetic workbook");
    };
    await request("/api/v1/business-records/export.xlsx", { method: "POST" });
  } finally { globalThis.fetch = original; configureAPI({ baseURL: "", publicDemo: false }); }
});

test("configured HTTPS API origin is used without leaking CSRF on reads; ordinary query text is preserved", async () => {
  const original = globalThis.fetch;
  try {
    configureAPI({ baseURL: "https://api.example.test", publicDemo: true }); setCSRFToken("synthetic");
    globalThis.fetch = async (url, options) => {
      assert.equal(url, "https://api.example.test/api/v1/business-records?q=..");
      assert.equal(new Headers(options?.headers).get("X-Demo-CSRF"), null);
      return new Response("{}");
    };
    await request("/api/v1/business-records?q=..");
    for (const baseURL of ["http://api.test", "https://user:password@api.test", "https://api.test/path", "https://api.test?key=secret"]) {
      assert.throws(() => configureAPI({ baseURL, publicDemo: true }));
    }
    await assert.rejects(request("https://evil.test/"));
    await assert.rejects(request("/api/v1/../../evil"));
  } finally { globalThis.fetch = original; configureAPI({ baseURL: "", publicDemo: false }); }
});

test("expired business sessions signal re-login, while login errors preserve typed errors without automatic retry", async () => {
  const original = globalThis.fetch;
  let signals = 0, requests = 0;
  try {
    configureAPI({ baseURL: "", publicDemo: true, onUnauthorized: () => signals++ });
    globalThis.fetch = async () => { requests++; return new Response(JSON.stringify({ error: { code: "AUTHENTICATION_REQUIRED" } }), { status: 401 }); };
    await assert.rejects(request("/api/v1/business-records"), (error: unknown) => error instanceof APIError && error.status === 401);
    assert.equal(signals, 1); assert.equal(requests, 1);
    await assert.rejects(request("/api/v1/auth/login", { method: "POST" }));
    assert.equal(signals, 1); assert.equal(requests, 2);
  } finally { globalThis.fetch = original; configureAPI({ baseURL: "", publicDemo: false }); }
});

test("a failed cloud upload preserves its real 504 status and failure export capability without fabricating a form", async () => {
  const original = globalThis.fetch;
  let calls = 0;
  try {
    configureAPI({ baseURL: "", publicDemo: true });
    globalThis.fetch = async (_input, options) => {
      if (options?.method === "POST") {
        calls++;
        return new Response(JSON.stringify({ status: "failed", run_id: "synthetic-failed-run",
          review_token: "synthetic-capability-token-0123456789", data_origin: "live", form_ids: [], selected_form: null,
          provider: "offline_mock", model: null, failure: { code: "PROVIDER_TIMEOUT", retryable: false },
          error: { code: "PROVIDER_TIMEOUT" } }), { status: 504 });
      }
      return new Response("synthetic failure workbook");
    };
    const app = appHarness(); await app.upload();
    assert.equal(calls, 1);
    assert.match(text(app.tree), /HTTP 504/);
    assert.match(text(app.tree), /דוח הכשל זמין לייצוא/);
    assert.equal(app.nodes().filter(n => n.props["data-row-action"]).length, 0);
    await app.click("הורדת דוח כשל");
    assert.ok(app.download);
  } finally { globalThis.fetch = original; configureAPI({ baseURL: "", publicDemo: false }); }
});

test("unstructured failures cannot invent a failed record or expose arbitrary response content", async () => {
  const original = globalThis.fetch;
  try {
    configureAPI({ baseURL: "", publicDemo: true });
    globalThis.fetch = async () => new Response(JSON.stringify({ status: "failed", run_id: "forged", selected_form: { fields: [] },
      error: { code: "PRIVATE path/body" } }), { status: 503 });
    await assert.rejects(request("/api/v1/demo-runs", { method: "POST" }), (value: unknown) =>
      value instanceof APIError && value.status === 503 && value.code === "DEMO_REQUEST_FAILED" && !value.failedRun);
  } finally { globalThis.fetch = original; configureAPI({ baseURL: "", publicDemo: false }); }
});
