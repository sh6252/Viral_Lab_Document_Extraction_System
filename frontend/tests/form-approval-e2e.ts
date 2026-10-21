// Opt-in full React -> actual FastAPI routes/domain -> real XLSX, mock provider only.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { createInterface } from "node:readline";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { appHarness, text } from "./appHarness.ts";

const root = fileURLToPath(new URL("../../", import.meta.url));
const python = process.env.BACKEND_TEST_PYTHON ?? resolve(root,
  process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python");
assert.ok(existsSync(python), "Existing backend test Python required; no installation performed");
const environment = { ...process.env };
for (const key of Object.keys(environment)) if (/KEY|TOKEN|SECRET|PASSWORD|DSN|AI_OUTBOUND|ALLOW_EXTERNAL_PHI/i.test(key)) delete environment[key];
const child = spawn(python, ["-X", "utf8", "-B", "-m", "tests.form_approval_bridge"], { cwd: root, env: environment, windowsHide: true, stdio: ["pipe", "pipe", "pipe"] });
let id = 0;
const pending = new Map<number, { resolve(value: any): void; reject(error: Error): void }>();
createInterface({ input: child.stdout }).on("line", line => {
  const message = JSON.parse(line); const promise = pending.get(message.id); pending.delete(message.id);
  if (message.error) promise?.reject(new Error(message.error)); else promise?.resolve(message.payload);
});
child.on("error", () => { for (const promise of pending.values()) promise.reject(new Error("Backend bridge unavailable")); });
child.on("exit", code => { for (const promise of pending.values()) promise.reject(new Error(`Backend bridge exited ${code}`)); });
function call(request: Record<string, unknown>): Promise<any> {
  return new Promise((resolve, reject) => { const next = ++id; pending.set(next, { resolve, reject }); child.stdin.write(JSON.stringify({ id: next, ...request }) + "\n"); });
}
const previous = globalThis.fetch;
globalThis.fetch = async (input, options) => {
  const request: Record<string, unknown> = { path: String(input), method: options?.method ?? "GET" };
  if (options?.body instanceof FormData) {
    const pdf = options.body.get("file") as File;
    request.upload = Buffer.from(await pdf.arrayBuffer()).toString("base64");
  } else if (options?.body) request.body = String(options.body);
  const result = await call(request);
  return new Response(Buffer.from(result.content, "base64"), { status: result.status, headers: result.headers });
};
const timeout = setTimeout(() => { child.kill(); throw new Error("OFFLINE_E2E_TIMEOUT"); }, 60000);
try {
  const app = appHarness(); await app.upload();
  assert.ok(app.nodes().some(n => n.props.id === "operator-declaration"), "Real backend upload must create reviewable forms: " + text(app.tree));
  await app.openApproval(); app.operator("בודקת סינתטית — בדקתי מול המקור"); app.attest();
  await app.click("אישור סופי לטופס");
  assert.match(text(app.tree), /הטופס אושר סופית בשרת/);
  await app.selectForm("form-2");
  assert.equal(app.nodes().filter(n => n.props["data-attention"]).length, 4, "Missing ID and uncertain populated date across two rows");
  assert.ok(app.rowActions().some(button => text(button) === "בדיקת חריגות"));
  app.decide("israeli_id", "acknowledge_blank", "חסר במקור הסינתטי");
  app.value("sample_receipt_date", 0, "18-08-2026");
  app.decide("sample_receipt_date", "correct_and_approve", "");
  await app.click("שמירת החלטות הביקורת");
  assert.match(text(app.tree), /נשמרו 2 החלטות ביקורת/);
  await app.click("רענון מצב מהשרת");
  assert.ok(app.rowActions().some(button => text(button) === "בדיקת חריגות"));
  assert.match(text(app.tree), /תאריך לא תקין או פורמט לא נתמך/);
  assert.equal(app.nodes().filter(n => n.props["data-attention"]).length, 2, "Only saved invalid date remains a blocker across two commitments");
  app.value("sample_receipt_date", 0, "18.08.2026");
  app.decide("sample_receipt_date", "correct_and_approve", "");
  await app.click("שמירת החלטות הביקורת");
  await app.click("רענון מצב מהשרת");
  assert.equal(app.nodes().filter(n => n.props["data-attention"]).length, 0);
  await app.openApproval(2); app.attest(); await app.click("אישור סופי לטופס");
  assert.match(text(app.tree), /הטופס אושר סופית בשרת · גרסה 4/);
  await app.click("רענון מצב מהשרת");
  assert.ok(app.rowActions().every(button => button.props.disabled && text(button) === "אושר סופית"));
  await app.selectForm("form-1");
  assert.ok(app.rowActions().every(button => button.props.disabled), "Reopened finalized forms stay finalized");
  await app.click("ייצוא ל־Excel");
  assert.ok(app.download);
  const book = await call({ op: "inspect_workbook", content: Buffer.from(await app.download.arrayBuffer()).toString("base64") });
  assert.equal(book.Forms.length, 4);
  assert.deepEqual(book.Forms.map((r: any) => r.commitment_numbers), ["00001", "00002", "00001", "00002"]);
  assert.ok(book.Forms.every((r: any) => r.form_finalized === "true"));
  assert.equal(book.ReviewAudit.length, 5, "Three actual field resolutions and two form attestations");
  assert.equal(book.ReviewAudit.filter((r: any) => r.field_name === "__form__").length, 2);
  assert.ok(book.Forms.filter((r: any) => r.form_id === "form-2").every((r: any) => r.israeli_id === "" && r.sample_receipt_date === "18.08.2026"));
  const stats = await call({ op: "stats" });
  assert.deepEqual(stats, { provider_mock_calls: 1, external_calls: 0, database_calls: 0 });
  await app.click("ייצוא כל השורות לאקסל");
  const bulk = await call({ op: "inspect_workbook", content: Buffer.from(await app.download!.arrayBuffer()).toString("base64") });
  assert.equal(bulk.Forms.length, 4); assert.equal(bulk.ReviewAudit.length, 5);
  app.drop([new File(["%PDF-1.7 synthetic a"], "a.pdf", { type: "application/pdf" }),
    new File(["%PDF-1.7 synthetic b"], "b.pdf", { type: "application/pdf" })]);
  app.check("אישור עיבוד חיצוני לכל הקבצים"); await app.click("העלאה וחילוץ");
  assert.equal(app.nodes().filter(n => n.props["data-upload-status"] === "succeeded").length, 2);
  await app.click("ייצוא כל השורות לאקסל");
  const batchBook = await call({ op: "inspect_workbook", content: Buffer.from(await app.download!.arrayBuffer()).toString("base64") });
  assert.equal(batchBook.Forms.length, 12);
  assert.equal(new Set(batchBook.Forms.map((row: any) => row.document_id)).size, 3);
  assert.equal(batchBook.Forms.filter((row: any) => row.form_finalized === "false").length, 8);
  assert.equal(batchBook.ReviewAudit.length, 5, "Batch upload and bulk export cannot create approvals");
  assert.deepEqual(await call({ op: "stats" }), { provider_mock_calls: 3, external_calls: 0, database_calls: 0 });
  console.log("PASS: React -> actual FastAPI -> two forms -> exception correction/blank -> one final event per form -> reopen -> 4 XLSX rows; zero external/database calls.");
} finally {
  clearTimeout(timeout); globalThis.fetch = previous; child.stdin.end(); child.kill();
}
