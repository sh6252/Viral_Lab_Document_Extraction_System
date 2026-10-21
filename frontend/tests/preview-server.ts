// Explicit developer-only synthetic UI preview. Never imported by application code.
// No proxy, database, SDK, credentials, real files or external network requests.
import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve, sep, extname } from "node:path";
import { fields } from "../src/api.ts";
import type { DemoRun, FinalizeCommand, ReviewCommand, SelectedForm, SavedRecord } from "../src/api.ts";

export function syntheticPdf(): Buffer {
  const stream = "BT /F1 19 Tf 40 740 Td (SYNTHETIC MOCK PREVIEW ONLY) Tj 0 -35 Td /F1 12 Tf (No real patient. No model extraction. UI layout fixture.) Tj ET";
  const objects = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>", `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`];
  let result = "%PDF-1.4\n";
  const offsets = [0];
  for (const [index, object] of objects.entries()) { offsets.push(Buffer.byteLength(result)); result += `${index + 1} 0 obj\n${object}\nendobj\n`; }
  const xref = Buffer.byteLength(result);
  result += `xref\n0 6\n0000000000 65535 f \n${offsets.slice(1).map(offset => `${String(offset).padStart(10, "0")} 00000 n \n`).join("")}trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`;
  return Buffer.from(result);
}

export async function startPreview(port = 5174) {
  const root = resolve(fileURLToPath(new URL("../dist/", import.meta.url)));
  const index = readFileSync(resolve(root, "index.html"), "utf8")
    .replace("<title>", "<title>[MOCK] ")
    .replace("<body>", '<body><div style="position:fixed;bottom:10px;left:10px;z-index:99;background:#122e40;color:white;border-radius:6px;padding:6px 10px;font:11px Arial" dir="rtl">בדיקת ממשק בלבד · נתונים סינתטיים · ללא Gemini או מסד נתונים</div>');
  const values = { first_name: ["שרה"], last_name: ["כהן"], israeli_id: ["012345678"],
    central_numbers: ["803215", "803216"], sample_receipt_date: ["18.08.2026"], sender: ["לאומית"],
    commitment_numbers: ["456001", "456002", "456003"] };
  const forms: SelectedForm[] = ["synthetic-one", "synthetic-two"].map((form_id, index) => ({
    form_id, revision: 0, review_status: "unresolved", fields: fields.map(field_name => {
      const raw = index === 1 && field_name === "israeli_id" ? [] : index === 0 && field_name === "commitment_numbers"
        ? Array.from({ length: 22 }, (_, i) => String(456001 + i)) : values[field_name];
      return { field_name, provider_values: raw.map(raw => ({ raw, normalized: null })),
        provider_value_status: raw.length ? "present" : "missing", provider_review_status: "unreviewed",
        reviewed_values: [], review_status: "unreviewed" };
    }),
  }));
  const stats = { uploads: 0, reviews: 0, decisions: 0, externalRequests: 0 };
  let failure = false;
  let formApprovalFixture = false;
  let finalizations = 0;
  function fixtureFlags(form: SelectedForm) {
    if (!formApprovalFixture) return;
    for (const field of form.fields) {
      const invalidDate = field.field_name === "sample_receipt_date" && field.reviewed_values[0] === "18-08-2026";
      const missing = field.review_status === "unreviewed" && !field.provider_values.length;
      field.requires_review = invalidDate || missing;
      field.review_reasons = invalidDate ? ["INVALID_DATE"] : missing ? ["MISSING_VALUE"] : [];
    }
  }
  const run = (form = forms[0]): DemoRun => ({ run_id: "synthetic-ui-preview", review_token: "synthetic-bound-token",
    status: failure ? "failed" : "succeeded", data_origin: "live", form_ids: failure ? [] : forms.map(f => f.form_id),
    provider: "offline_mock", model: "ui-fixture-only", selected_form: failure ? null : form,
    failure: failure ? { code: "PROVIDER_TIMEOUT", message: "Mock-only failure", retryable: false } : null });
  const server = createServer(async (request, response) => {
    response.setHeader("Cache-Control", "no-store"); response.setHeader("X-UI-Preview", "synthetic-only");
    const address = server.address();
    const actualPort = address && typeof address === "object" ? address.port : port;
    if (request.headers.host !== `127.0.0.1:${actualPort}`) { response.writeHead(403).end(); return; }
    const url = new URL(request.url ?? "/", `http://127.0.0.1:${actualPort}`);
    const json = (value: unknown, status = 200) => { response.writeHead(status, { "Content-Type": "application/json" }).end(JSON.stringify(value)); };
    try {
      if (url.pathname.startsWith("/api/")) {
        if (request.headers["x-local-demo"] !== "1") { json({ error: { code: "LOCAL_DEMO_ONLY" } }, 403); return; }
        if (url.pathname === "/api/v1/business-records") {
          const rows: SavedRecord[] = forms.map(form => ({ run_id: "synthetic-ui-preview", document_id: "synthetic-ui-preview",
            form_id: form.form_id, revision: form.revision, review_status: form.review_status === "complete" ? "completed" : "pending",
            summary: Object.fromEntries(form.fields.map(field => [field.field_name, field.review_status === "unreviewed"
              ? field.provider_values.map(v => v.raw) : field.reviewed_values])) as SavedRecord["summary"],
            created_at: "2026-08-18T09:30:00Z", updated_at: "2026-08-18T09:30:00Z" }));
          const matching = rows.filter(row => (!url.searchParams.get("q") || JSON.stringify(row.summary).includes(url.searchParams.get("q")!))
            && (!url.searchParams.get("status") || row.review_status === url.searchParams.get("status"))
            && (!url.searchParams.get("sender") || row.summary.sender.includes(url.searchParams.get("sender")!)));
          const offset = Number(url.searchParams.get("offset") ?? 0);
          json({ items: matching.slice(offset, offset + 20), total: matching.length, offset, limit: 20 }); return;
        }
        if (url.pathname.endsWith("/source")) { response.writeHead(200, { "Content-Type": "application/pdf" }).end(syntheticPdf()); return; }
        if (url.pathname.endsWith("/export.xlsx")) { json({ error: { code: "MOCK_EXPORT_NOT_AVAILABLE" } }, 501); return; }
        if (request.method === "POST" && url.pathname.endsWith("/finalize") && formApprovalFixture) {
          const chunks: Buffer[] = []; for await (const chunk of request) chunks.push(Buffer.from(chunk));
          const command = JSON.parse(Buffer.concat(chunks).toString()) as FinalizeCommand;
          const form = forms.find(form => url.pathname.includes(`/forms/${form.form_id}/`))!;
          if (command.review_token !== "synthetic-bound-token" || !command.attest_all_values || !command.operator_declaration.trim()) { json({ error: { code: "INVALID_FORM_APPROVAL" } }, 422); return; }
          if (command.expected_revision !== form.revision || form.finalized) { json({ error: { code: "STALE_REVIEW_REVISION" } }, 409); return; }
          if (form.fields.some(field => field.requires_review)) { json({ error: { code: "FORM_HAS_UNRESOLVED_FIELDS" } }, 422); return; }
          form.finalized = true; form.revision++; finalizations++; json(run(form)); return;
        }
        if (request.method === "POST" && url.pathname.endsWith("/review")) {
          const chunks: Buffer[] = []; for await (const chunk of request) chunks.push(Buffer.from(chunk));
          const command = JSON.parse(Buffer.concat(chunks).toString()) as ReviewCommand;
          const form = forms.find(form => url.pathname.includes(`/forms/${form.form_id}/`))!;
          if (!command.operator_declaration.trim() || !command.actions.every(a => a.reason.trim())) { json({ error: { code: "INVALID_DEMO_REVIEW" } }, 422); return; }
          if (command.expected_revision !== form.revision) { json({ error: { code: "BUSINESS_REVIEW_CONFLICT" } }, 409); return; }
          for (const action of command.actions) {
            const field = form.fields.find(field => field.field_name === action.field_name)!;
            field.reviewed_values = action.reviewed_values;
            field.review_status = action.action === "acknowledge_blank" ? "acknowledged_blank" : action.action === "approve_as_extracted" ? "approved_provider" : action.action === "correct_and_approve" ? "approved_corrected" : "unresolved";
            form.revision++; stats.decisions++;
          }
          form.review_status = form.fields.every(field => ["approved_provider", "approved_corrected", "acknowledged_blank"].includes(field.review_status)) ? "complete" : "unresolved";
          if (formApprovalFixture) { form.finalized = false; fixtureFlags(form); }
          stats.reviews++; json(run(form)); return;
        }
        if (request.method === "POST" && url.pathname === "/api/v1/demo-runs") {
          for await (const _chunk of request) { /* Discard test-only input. */ }
          stats.uploads++; json(run(), 201); return;
        }
        if (request.method === "GET" && url.pathname === "/api/v1/demo-runs/synthetic-ui-preview") {
          json(run(forms.find(form => form.form_id === url.searchParams.get("form_id")) ?? forms[0])); return;
        }
        json({ error: { code: "MOCK_ROUTE_NOT_FOUND" } }, 404); return;
      }
      const path = resolve(root, "." + decodeURIComponent(url.pathname));
      if (url.pathname === "/") { response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" }).end(index); return; }
      if (!path.startsWith(root + sep) || ![".js", ".css", ".svg"].includes(extname(path))) { response.writeHead(404).end(); return; }
      const content = readFileSync(path);
      response.writeHead(200, { "Content-Type": extname(path) === ".js" ? "text/javascript" : extname(path) === ".svg" ? "image/svg+xml" : "text/css" }).end(content);
    } catch { json({ error: { code: "MOCK_PREVIEW_ERROR" } }, 500); }
  });
  await new Promise<void>((resolve, reject) => { server.once("error", reject); server.listen(port, "127.0.0.1", resolve); });
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("LOOPBACK_PREVIEW_ADDRESS_REQUIRED");
  return { server, stats, origin: `http://127.0.0.1:${address.port}`, failExtraction(value: boolean) { failure = value; },
    get finalizations() { return finalizations; },
    useFinalApprovalFixture() {
      // Opt-in browser-only reproduction, clearly labeled synthetic by the preview banner.
      formApprovalFixture = true;
      for (const form of forms) { form.finalized = false; form.review_policy_version = "human-form-attestation-v1"; }
      const date = forms[1].fields.find(field => field.field_name === "sample_receipt_date")!;
      date.reviewed_values = ["18-08-2026"]; date.review_status = "approved_corrected";
      date.provider_review_status = "needs_review";
      for (const form of forms) fixtureFlags(form);
    },
  };
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  await startPreview(); console.log("SYNTHETIC UI ONLY: http://127.0.0.1:5174 — no database, provider or real XLSX.");
}
