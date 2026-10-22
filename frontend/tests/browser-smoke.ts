// Opt-in real Chromium rendering against the synthetic-only preview server.
// No production backend, database, provider, external assets or installed packages.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { startPreview, syntheticPdf } from "./preview-server.ts";
import { fields } from "../src/api.ts";

const binary = process.env.UI_TEST_BROWSER ?? [
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
].find(existsSync);
if (!binary || !existsSync(binary)) throw new Error("EXISTING_CHROMIUM_REQUIRED_NO_INSTALL_PERFORMED");
const directory = resolve(fileURLToPath(new URL("../storage/", import.meta.url)), `browser-${Date.now()}`);
mkdirSync(directory, { recursive: true });
const profile = resolve(directory, "profile");
console.log("Starting loopback-only synthetic preview…");
const preview = await startPreview(0); // OS-assigned loopback port; never stop an existing user preview.
console.log("Starting isolated headless Chromium…");
const browser = spawn(binary, ["--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
  "--disable-background-networking", "--disable-sync", "--disable-component-update",
  "--disable-default-apps", "--disable-domain-reliability", "--metrics-recording-only",
  "--disable-features=OptimizationHints,MediaRouter,Translate,AutofillServerCommunication",
  "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1, EXCLUDE localhost",
  "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"],
  { windowsHide: true, stdio: "ignore" });
let socket: WebSocket | undefined;
const pause = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));
async function until(check: () => boolean | Promise<boolean>, label: string) {
  const end = Date.now() + 15000;
  while (Date.now() < end) { if (await check()) return; await pause(100); }
  throw new Error(`BROWSER_CHECK_TIMEOUT: ${label}`);
}
try {
  await until(() => existsSync(resolve(profile, "DevToolsActivePort")), "local Chromium startup");
  const port = Number(readFileSync(resolve(profile, "DevToolsActivePort"), "utf8").split("\n")[0]);
  const page = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: "PUT" })).json() as { webSocketDebuggerUrl: string };
  socket = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise<void>((resolve, reject) => { socket!.addEventListener("open", () => resolve(), { once: true }); socket!.addEventListener("error", reject, { once: true }); });
  let nextId = 0;
  const pending = new Map<number, { resolve(value: any): void; reject(error: Error): void; timer: ReturnType<typeof setTimeout> }>();
  const errors: string[] = [], blocked: string[] = [];
  let pageLoads = 0;
  function cdp(method: string, params: Record<string, unknown> = {}): Promise<any> {
    const id = ++nextId;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP_TIMEOUT: ${method}`)); }, 15000);
      timer.unref(); pending.set(id, { resolve, reject, timer });
      socket!.send(JSON.stringify({ id, method, params }));
    });
  }
  socket.addEventListener("message", event => {
    const message = JSON.parse(String(event.data));
    if (message.method === "Page.loadEventFired") pageLoads++;
    if (message.id) {
      const request = pending.get(message.id); pending.delete(message.id);
      if (request) clearTimeout(request.timer);
      if (message.error) request?.reject(new Error(message.error.message)); else request?.resolve(message.result);
    }
    if (message.method === "Runtime.exceptionThrown") errors.push("Uncaught browser exception");
    if (message.method === "Fetch.requestPaused") {
      const { requestId, request } = message.params;
      const allowed = request.url.startsWith(preview.origin + "/") || /^(blob:|data:|chrome-extension:)/.test(request.url);
      if (!allowed) blocked.push("Blocked non-local request");
      void cdp(allowed ? "Fetch.continueRequest" : "Fetch.failRequest", allowed ? { requestId } : { requestId, errorReason: "BlockedByClient" });
    }
  });
  const evaluate = async (expression: string) => {
    const result = await cdp("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error("BROWSER_EVALUATION_FAILED");
    return result.result.value;
  };
  const ready = async (expression: string) => until(async () => Boolean(await evaluate(`Boolean(${expression})`)), expression);
  const click = async (label: string) => {
    await evaluate(`Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === ${JSON.stringify(label)} || b.getAttribute('aria-label') === ${JSON.stringify(label)}).click()`);
    await pause(100);
  };
  const screenshot = async (name: string) => {
    const result = await cdp("Page.captureScreenshot", { format: "png" });
    writeFileSync(resolve(directory, name), Buffer.from(result.data, "base64"), { flag: "wx" });
  };
  const checkWidth = async () => assert.ok(await evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "Page has horizontal overflow");
  await cdp("Page.enable"); await cdp("Runtime.enable"); await cdp("Fetch.enable", { patterns: [{ urlPattern: "*" }] });
  await cdp("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await cdp("Page.navigate", { url: preview.origin + "/" });
  await ready("document.querySelector('.drop-zone') && document.querySelector('.nav-count')?.textContent === '2'");
  assert.equal(await evaluate("document.documentElement.lang"), "he");
  assert.equal(await evaluate("getComputedStyle(document.body).direction"), "rtl");
  await cdp("Input.dispatchKeyEvent", { type: "keyDown", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });
  await cdp("Input.dispatchKeyEvent", { type: "keyUp", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });
  assert.equal(await evaluate("document.activeElement.className"), "skip-link");
  await evaluate("document.activeElement.blur()");
  await checkWidth(); await screenshot("desktop-upload.png"); console.log("Desktop upload / Hebrew RTL / keyboard skip link: PASS");
  assert.ok((await evaluate("document.title")).includes("מחויבים ל־100"));
  await ready("document.querySelector('img.brand-mark')?.naturalWidth > 0");
  assert.equal(await evaluate("new URL(document.querySelector('link[rel=icon]').href).pathname"), "/brand.svg");
  const checkBrand = async () => {
    assert.equal(await evaluate("document.querySelector('.brand').getAttribute('aria-label')"), "מחויבים ל־100 — מסך ראשי");
    assert.equal(await evaluate("getComputedStyle(document.querySelector('.brand-number bdi')).direction"), "ltr");
    assert.ok(await evaluate("(() => { const b = document.querySelector('.brand').getBoundingClientRect(); return Array.from(document.querySelector('.brand').children).every(c => { const r = c.getBoundingClientRect(); return r.left >= b.left - 1 && r.right <= b.right + 1; }); })()"), "Brand content must fit without clipping");
  };
  await checkBrand();
  for (const width of [1100, 390, 320]) {
    await cdp("Emulation.setDeviceMetricsOverride", { width, height: 844, deviceScaleFactor: 1, mobile: width < 720 });
    await checkBrand(); await checkWidth();
    await screenshot(`branding-${width}.png`);
  }
  await cdp("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  console.log("Document/100 branding, local favicon, 320px mobile and compact desktop: PASS");
  assert.equal(await evaluate("/[\\u0591-\\u05BD\\u05BF-\\u05C2\\u05C4-\\u05C5\\u05C7]/u.test(document.querySelector('.brand').textContent)"), false);
  await click("רשומות שמורות"); await ready("document.querySelectorAll('.saved-records tbody tr').length === 25");
  await checkWidth(); await screenshot("desktop-records.png");
  await evaluate("Array.from(document.querySelectorAll('button')).find(b => b.getAttribute('aria-label')?.startsWith('פתיחת רשומה ')).click()");
  await ready("document.querySelectorAll('[data-work-row]').length === 20 && document.querySelector('.pdf-viewer')");
  assert.equal(await evaluate("document.querySelectorAll('fieldset').length"), 7, "All fields remain visible, including unflagged ones");
  assert.equal(preview.stats.uploads, 0, "Reopening cannot extract");
  assert.equal(await evaluate("document.querySelector('.source-open').rel"), "noopener noreferrer");
  await click("עמוד הבא בטבלה"); assert.equal(await evaluate("document.querySelectorAll('[data-work-row]').length"), 5);
  await click("עמוד קודם בטבלה");
  await evaluate("document.querySelector('#commitment-workbench').scrollIntoView()"); await screenshot("desktop-commitments.png");
  async function select(label: string, value: string) {
    await evaluate(`(() => { const select = document.querySelector('[aria-label=${JSON.stringify(label)}]'); select.value = ${JSON.stringify(value)}; select.dispatchEvent(new Event('change', {bubbles:true})); })()`);
    await pause(70);
  }
  async function input(selector: string, value: string) {
    await evaluate(`(() => { const input = document.querySelector(${JSON.stringify(selector)}); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,${JSON.stringify(value)}); input.dispatchEvent(new Event('input',{bubbles:true})); })()`);
  }
  await select("סינון שולחן העבודה", "attention");
  assert.equal(await evaluate("document.querySelectorAll('[data-work-row]').length"), 3);
  assert.equal(await evaluate("document.querySelectorAll('[data-attention]').length"), 3);
  assert.ok(await evaluate("Array.from(document.querySelectorAll('[data-attention]')).every(cell => cell.dataset.column === 'israeli_id')"));
  await click("החריגה הבאה");
  await ready("document.querySelectorAll('fieldset').length === 7 && document.querySelector('[data-field=israeli_id] .missing-value')");
  assert.equal(await evaluate("document.querySelector('[data-field=israeli_id] button[data-action=approve_as_extracted]').disabled"), true);
  await pause(700);
  await evaluate("document.querySelector('#review-workspace').scrollIntoView()"); await screenshot("desktop-review.png");
  console.log("25 commitment rows / bounded table pages / shared missing-ID exception / focused review: PASS");
  await cdp("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await checkWidth(); await evaluate("document.querySelector('#commitment-workbench').scrollIntoView()"); await screenshot("mobile-commitments.png");
  await evaluate("document.querySelector('[data-field=israeli_id]').scrollIntoView()"); await screenshot("mobile-missing-field.png");
  await evaluate("scrollTo(0,0)"); await screenshot("mobile-upload.png");
  await click("רשומות שמורות"); await checkWidth(); await screenshot("mobile-records.png");
  await click("עיבוד וביקורת");
  await cdp("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] });
  assert.equal(await evaluate("getComputedStyle(document.querySelector('.button')).transitionDuration"), "0s");
  await input("[data-field=israeli_id] [data-review-reason]", "השדה חסר במקור הסינתטי");
  await input("#operator-declaration", "בודקת סינתטית — בדיקת ממשק בלבד");
  await evaluate("document.querySelector('[data-field=israeli_id] button[data-action=acknowledge_blank]').click()");
  await ready("document.body.textContent.includes('נשמרה החלטת ביקורת אחת') && document.body.textContent.includes('גרסה 1')");
  assert.equal(preview.stats.decisions, 1, "Exception-only review must never invent six approvals");
  assert.equal(await evaluate("document.querySelectorAll('[data-attention]').length"), 0);
  assert.equal(await evaluate("document.querySelector('[data-field=israeli_id] [data-review-action]').dataset.reviewAction"), "acknowledge_blank");
  assert.ok(await evaluate("document.querySelector('.review-heading').textContent.includes('ממתין לבדיקה')"));
  assert.equal(await evaluate("document.querySelector('[data-human-approved-count]').dataset.humanApprovedCount"), "1");
  await evaluate("document.querySelector('.review-submit').scrollIntoView()"); await screenshot("mobile-exception-saved.png");
  console.log("Exception-only save: one decision, six fields remain unapproved, server status remains pending: PASS");
  // Explicit full-review regression, separate from exception-only operation.
  await input("#operator-declaration", ""); // Stage multiple decisions, then explicitly save them together.
  for (const name of fields.filter(name => name !== "israeli_id")) {
    await evaluate(`document.querySelector('[data-field="${name}"] button[data-action=approve_as_extracted]').click()`);
    await input(`[data-field="${name}"] [data-review-reason]`, "נבדק מול מקור סינתטי");
  }
  await input("#operator-declaration", "בודקת סינתטית");
  await click("שמירת החלטות הביקורת");
  await ready("document.body.textContent.includes('נשמרו 6 החלטות ביקורת') && document.body.textContent.includes('גרסה 7')");
  assert.equal(preview.stats.decisions, 7);
  await input("[data-field=sample_receipt_date] [data-value-field]", "19.08.2026");
  await input("[data-field=sample_receipt_date] [data-review-reason]", "תיקון תאריך לאחר בדיקה חוזרת");
  await evaluate("document.querySelector('[data-field=sample_receipt_date] button[data-action=correct_and_approve]').click()");
  await ready("document.body.textContent.includes('נשמרה החלטת ביקורת אחת') && document.body.textContent.includes('גרסה 8')");
  assert.equal(preview.stats.decisions, 8, "Only the changed field is posted again");
  await select("סינון שולחן העבודה", "approved");
  assert.equal(await evaluate("document.querySelectorAll('[data-work-row]').length"), 3);
  assert.ok(await evaluate("Array.from(document.querySelectorAll('[data-work-row] [data-column=sample_receipt_date]')).every(cell => cell.textContent.includes('19.08.2026') && cell.textContent.includes('תוקן ואושר אנושית'))"));
  await evaluate("document.querySelector('.review-submit').scrollIntoView()"); await screenshot("mobile-saved-review.png");
  await cdp("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await evaluate("document.querySelector('#commitment-workbench').scrollIntoView()"); await screenshot("desktop-shared-correction.png");
  console.log("Shared correction on three rows / explicit review / revision 8 / no invented sample pairing: PASS");
  preview.useFinalApprovalFixture();
  await click("רענון מצב מהשרת");
  await ready("document.body.textContent.includes('תאריך לא תקין או פורמט לא נתמך')");
  await select("סינון שולחן העבודה", "attention");
  assert.equal(await evaluate("document.querySelectorAll('[data-attention]').length"), 3);
  assert.equal(await evaluate("document.querySelectorAll('[data-row-action]').length"), 3);
  await click("בדיקת חריגות");
  assert.equal(await evaluate("document.querySelectorAll('dialog').length"), 0);
  await click("תאריך קבלת דגימה");
  await input("[data-field=sample_receipt_date] [data-value-field]", "18.08.2026");
  await input("[data-field=sample_receipt_date] [data-review-reason]", "תיקון פורמט בתרחיש סינתטי בלבד");
  await evaluate("document.querySelector('[data-field=sample_receipt_date] button[data-action=correct_and_approve]').click()");
  await ready("document.body.textContent.includes('גרסה 9')");
  await click("רענון מצב מהשרת");
  await ready("document.body.textContent.includes('כל החריגים טופלו לפי השרת')");
  assert.equal(await evaluate("document.querySelectorAll('[data-attention]').length"), 0);
  await select("סינון שולחן העבודה", "all");
  await click("עמוד הבא בטבלה");
  for (const width of [1440, 1024, 390, 320]) {
    await cdp("Emulation.setDeviceMetricsOverride", { width, height: 844, deviceScaleFactor: 1, mobile: width < 720 });
    await evaluate("document.querySelector('#commitment-workbench').scrollIntoView({block:'start'})");
    await screenshot(`table-actions-${width}.png`);
    await evaluate("document.querySelector('.review-tools').scrollIntoView({block:'start'})");
    assert.equal(await evaluate("getComputedStyle(document.querySelector('.review-tools')).position"), "static");
    assert.ok(await evaluate("document.querySelector('.review-tools').getBoundingClientRect().bottom <= document.querySelector('.review-heading').getBoundingClientRect().top"), "Review panels must not overlap");
    assert.ok(await evaluate("document.querySelector('.commitment-workbench').getBoundingClientRect().bottom <= document.querySelector('.run-panel').getBoundingClientRect().top"), "Workbench must not overlap following panels");
    await checkWidth(); await screenshot(`review-flow-${width}.png`);
    await click("אישור סופי");
    await ready("document.querySelector('dialog')?.open");
    assert.equal(await evaluate("document.querySelector('.form-attestation input').checked"), false);
    assert.equal(await evaluate("document.querySelector('.final-approve-button').disabled"), true);
    assert.ok(await evaluate("document.querySelector('#approval-dialog-scope').textContent.includes('3 מספרי ההתחייבות')"));
    assert.ok(await evaluate("(() => {const d = document.querySelector('dialog'); const r = d.getBoundingClientRect();return r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight && d.scrollHeight <= d.clientHeight + 1;})()"), "Compact dialog and controls fit without hidden scroll regions");
    for (let step = 0; step < 6; step++) {
      await cdp("Input.dispatchKeyEvent", { type: "keyDown", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });
      await cdp("Input.dispatchKeyEvent", { type: "keyUp", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });
      assert.ok(await evaluate("document.querySelector('dialog').contains(document.activeElement)"), "Keyboard focus stays inside confirmation");
    }
    await screenshot(`table-approval-dialog-${width}.png`);
    await cdp("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
    await cdp("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
    await ready("!document.querySelector('dialog')");
    assert.equal(await evaluate("document.activeElement.hasAttribute('data-row-action')"), true);
  }
  await click("אישור סופי"); await ready("document.querySelector('dialog')?.open");
  await input("#final-reviewer", "בודקת סינתטית — אישור טופס משותף");
  await evaluate("document.querySelector('input[aria-label=\"הצהרת אישור לכל ערכי הטופס\"]').click()");
  await ready("!document.querySelector('.final-approve-button').disabled");
  await click("אישור סופי לטופס");
  await ready("document.body.textContent.includes('הטופס אושר סופית בשרת')");
  await click("רענון מצב מהשרת");
  assert.equal(await evaluate("document.querySelectorAll('dialog').length"), 0);
  assert.equal(await evaluate("Array.from(document.querySelectorAll('[data-work-row][data-form=synthetic-two] [data-row-action]')).filter(b => b.textContent.trim() === 'אושר סופית' && b.disabled).length"), 3);
  assert.equal(preview.finalizations, 1);
  await evaluate("document.querySelector('#commitment-workbench').scrollIntoView({block:'start'})");
  await screenshot("table-approved-320.png");
  console.log("Row actions / shared form confirmation / no overlap at 1440, 1024, 390, 320 / keyboard focus and Escape / one finalization / refresh: PASS (synthetic UI fixture)");
  await click("ייצוא ל־Excel"); await ready("document.body.textContent.includes('הורדת הקובץ נכשלה')");
  assert.equal(await evaluate("document.body.textContent.includes('התקבל מהשרת והועבר להורדה')"), false);
  preview.failExtraction(true);
  const beforeReload = pageLoads;
  await cdp("Page.reload"); await until(() => pageLoads > beforeReload, "new document after reload");
  await ready("document.querySelector('.drop-zone')");
  const sourcePath = resolve(directory, "synthetic-ui-source.pdf");
  writeFileSync(sourcePath, syntheticPdf(), { flag: "wx" });
  const dom = await cdp("DOM.getDocument");
  const fileInput = await cdp("DOM.querySelector", { nodeId: dom.root.nodeId, selector: "input[type=file]" });
  await cdp("DOM.setFileInputFiles", { nodeId: fileInput.nodeId, files: [sourcePath] });
  assert.equal(await evaluate("Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === 'העלאה וחילוץ').disabled"), true);
  await evaluate("document.querySelector('input[type=checkbox]').click()");
  await click("העלאה וחילוץ"); await ready("document.querySelector('.failure-detail')");
  assert.equal(await evaluate("document.querySelectorAll('fieldset').length"), 0);
  await cdp("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await evaluate("document.querySelector('.run-panel').scrollIntoView()"); await screenshot("mobile-failure.png");
  await checkWidth(); assert.deepEqual(errors, []); assert.deepEqual(blocked, []);
  console.log("Mobile RTL / keyboard / reduced motion / consent / failed extraction / export error: PASS");
  console.log(JSON.stringify({ status: "PASS", external_api_calls: 0, database_calls: 0, provider_calls: 0, screenshots: directory, stats: preview.stats }));
} finally {
  socket?.close(); browser.kill(); preview.server.closeAllConnections();
  await new Promise<void>(resolve => preview.server.close(() => resolve()));
}
