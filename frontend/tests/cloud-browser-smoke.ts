// Real HTTPS/first-party cookies, actual built AuthGate and ASGI boundary; mock data only.
// Never run against a deployed service. Certificate exceptions apply only to this fresh test profile.
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { createInterface } from "node:readline";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { syntheticPdf } from "./preview-server.ts";

const root = fileURLToPath(new URL("../../", import.meta.url));
const python = process.env.BACKEND_TEST_PYTHON ?? resolve(root, process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python");
const binary = process.env.UI_TEST_BROWSER ?? ["C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"].find(existsSync);
assert.ok(binary && existsSync(binary) && existsSync(python), "Existing test Python and Chromium required");
const directory = resolve(root, "frontend/storage", `cloud-browser-${Date.now()}`);
mkdirSync(directory, { recursive: true });
const cert = resolve(directory, "certificate.pem"), key = resolve(directory, "private.key");
const openssl = process.env.UI_TEST_OPENSSL ?? (process.platform === "win32" ? "C:/Program Files/Git/usr/bin/openssl.exe" : "openssl");
assert.equal(spawnSync(openssl, ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-keyout", key,
  "-out", cert, "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1"], { windowsHide: true, stdio: "ignore" }).status, 0);
const environment = { ...process.env, AI_OUTBOUND_ENABLED: "false", ALLOW_EXTERNAL_PHI: "false" };
for (const name of Object.keys(environment)) if (/KEY|TOKEN|SECRET|PASSWORD|DSN/i.test(name)) delete environment[name as keyof typeof environment];
const server = spawn(python, ["-X", "utf8", "-B", "-m", "tests.cloud_browser_server", "--certificate", cert, "--key", key],
  { cwd: root, env: environment, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
let browser: ReturnType<typeof spawn> | undefined, socket: WebSocket | undefined;
const pause = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));
async function until(check: () => boolean | Promise<boolean>, label: string) {
  const end = Date.now() + 20000;
  while (Date.now() < end) { if (await check()) return; await pause(100); }
  throw new Error(`CLOUD_BROWSER_TIMEOUT: ${label}`);
}
const deadline = setTimeout(() => { server.kill(); browser?.kill(); }, 90000);
try {
  const origin = await new Promise<string>((resolve, reject) => {
    createInterface({ input: server.stdout! }).once("line", line => {
      const value = JSON.parse(line); assert.equal(value.mode, "synthetic-test-only"); resolve(value.origin);
    });
    server.once("exit", () => reject(new Error("SYNTHETIC_TLS_SERVER_FAILED")));
    server.once("error", reject);
  });
  assert.match(origin, /^https:\/\/127\.0\.0\.1:\d+$/);
  const profile = resolve(directory, "profile");
  browser = spawn(binary!, ["--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    "--disable-background-networking", "--disable-sync", "--disable-component-update", "--disable-default-apps",
    "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1, EXCLUDE localhost", "--ignore-certificate-errors",
    "--test-third-party-cookie-phaseout", "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0",
    `--user-data-dir=${profile}`, "about:blank"], { windowsHide: true, stdio: "ignore" });
  await until(() => existsSync(resolve(profile, "DevToolsActivePort")), "isolated browser");
  const port = Number(readFileSync(resolve(profile, "DevToolsActivePort"), "utf8").split("\n")[0]);
  const page = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: "PUT" })).json() as { webSocketDebuggerUrl: string };
  socket = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise<void>((resolve, reject) => { socket!.addEventListener("open", () => resolve(), { once: true }); socket!.addEventListener("error", reject, { once: true }); });
  let nextId = 0;
  const pending = new Map<number, { resolve(value: any): void; reject(error: Error): void }>();
  socket.addEventListener("message", event => {
    const value = JSON.parse(String(event.data));
    if (value.id) {
      const promise = pending.get(value.id); pending.delete(value.id);
      if (value.error) promise?.reject(new Error(value.error.message)); else promise?.resolve(value.result);
    }
  });
  function cdp(method: string, params: Record<string, unknown> = {}): Promise<any> {
    return new Promise((resolve, reject) => { const id = ++nextId; pending.set(id, { resolve, reject }); socket!.send(JSON.stringify({ id, method, params })); });
  }
  async function evaluate(expression: string) {
    const value = await cdp("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (value.exceptionDetails) throw new Error("BROWSER_EVALUATION_FAILED");
    return value.result.value;
  }
  await cdp("Network.enable");
  await cdp("Runtime.enable");
  await cdp("Network.setCookieControls", { enableThirdPartyCookieRestriction: true, disableThirdPartyCookieMetadata: true, disableThirdPartyCookieHeuristics: true });
  await cdp("Page.enable");
  await cdp("Page.navigate", { url: origin });
  await until(async () => Boolean(await evaluate("document.querySelector('#demo-password') && !document.querySelector('button[type=submit]').disabled")), "built login screen");
  async function checkLayout(page: "login" | "workbench") {
    await until(async () => await evaluate("document.querySelector('img[src=\"/brand.svg\"]')?.naturalWidth>0"), "local brand image loaded");
    for (const [label, width, height] of [["desktop", 1440, 1000], ["mobile", 390, 844]] as const) {
      await cdp("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false });
      await pause(100);
      const geometry = await evaluate(`(() => {
        const element=document.querySelector('${page === "login" ? ".auth-card" : ".page-heading"}');
        const rect=element.getBoundingClientRect();
        return {rtl:getComputedStyle(element).direction,overflow:document.documentElement.scrollWidth>innerWidth+1,
          left:rect.left,right:rect.right,width:innerWidth,logo:document.querySelector('img[src="/brand.svg"]').naturalWidth>0,
          strips:document.querySelectorAll('.demo-access,.topbar').length};
      })()`);
      assert.equal(geometry.rtl,"rtl"); assert.equal(geometry.overflow,false);
      assert.ok(geometry.left>=0 && geometry.right<=geometry.width+1 && geometry.logo, JSON.stringify(geometry));
      assert.equal(geometry.strips,0);
      const shot=await cdp("Page.captureScreenshot",{format:"png",captureBeyondViewport:false});
      writeFileSync(resolve(directory,`${label}-${page}.png`),Buffer.from(shot.data,"base64"));
    }
  }
  assert.equal(await evaluate("document.body.textContent.includes('כניסת שופטים ומוזמנים')"),false);
  await evaluate("document.querySelector('.password-toggle').click()");
  await until(async () => await evaluate("document.querySelector('#demo-password').type==='text'"),"accessible password toggle");
  await evaluate("document.querySelector('.password-toggle').click()");
  await checkLayout("login");
  await evaluate("document.querySelector('#demo-password').focus()");
  await cdp("Input.insertText", { text: "synthetic-invitation-password-only" });
  await evaluate("document.querySelector('button[type=submit]').click()");
  await until(async () => !await evaluate("Boolean(document.querySelector('.demo-login'))"), "real first-party login");
  assert.equal(await evaluate("document.querySelector('.page-heading .session-controls button').textContent"),"התנתקות");
  await checkLayout("workbench");
  assert.equal(await evaluate("document.cookie.includes('__Host-viral-demo')"), false);
  const cookies = await cdp("Network.getCookies", { urls: [origin] });
  assert.ok(cookies.cookies.some((c: any) => c.name === "__Host-viral-demo" && c.secure && c.httpOnly && c.sameSite === "Strict"));
  // Hold only upload 2 in the test browser; approval still reaches the real ASGI boundary.
  let uploadCount = 0, heldUpload = "";
  socket.addEventListener("message", event => {
    const message = JSON.parse(String(event.data));
    if (message.method === "Fetch.requestPaused") {
      uploadCount++;
      if (uploadCount === 2) heldUpload = message.params.requestId;
      else void cdp("Fetch.continueRequest", { requestId: message.params.requestId });
    }
  });
  await cdp("Fetch.enable", { patterns: [{ urlPattern: `${origin}/api/v1/demo-runs`, requestStage: "Request" }] });
  await evaluate(`(() => {
    const files=new DataTransfer();
    for (const id of ['one','two']) files.items.add(new File([${JSON.stringify(syntheticPdf(2).toString("ascii"))}],id+'.pdf',{type:'application/pdf'}));
    const input=document.querySelector('#source-file'); input.files=files.files; input.dispatchEvent(new Event('change',{bubbles:true}));
  })()`);
  await evaluate("document.querySelector('input[aria-label=\"אישור עיבוד חיצוני לכל הקבצים\"]').click()");
  await until(async () => await evaluate("!document.querySelector('.upload-button').disabled"), "batch consent");
  await evaluate("document.querySelector('.upload-button').click()");
  await until(() => Boolean(heldUpload), "second upload held");
  await until(async () => await evaluate("Boolean(document.querySelector('[data-row-action]:not(:disabled)'))"), "first record review enabled during batch");
  const firstPreview = await evaluate("document.querySelector('.pdf-viewer').dataset.sourceUrl");
  await evaluate("document.querySelector('.pdf-scroll').scrollIntoView({block:'center'})");
  const workerAsset=readdirSync(resolve(root, 'frontend/dist/assets')).find(name=>/^pdf.worker.*\.js$/.test(name));
  assert.ok(workerAsset, 'PDF worker must have a portable JavaScript extension');
  const workerResponse=await evaluate(`(async()=>{const response=await fetch('/assets/${workerAsset}');return {status:response.status,type:response.headers.get('content-type')}})()`);
  assert.equal(workerResponse.status,200);assert.match(workerResponse.type,/^(text|application)\/javascript/);
  await until(async () => await evaluate("document.querySelectorAll('[data-pdf-page]').length===2 && Boolean(document.querySelector('[data-pdf-page] canvas[data-rendered=true]'))"), "self-hosted two-page PDF rendered under cloud CSP");
  assert.equal(await evaluate("document.querySelectorAll('iframe,.thumbnailView,#sidebarContainer').length"), 0);
  assert.ok(await evaluate("(() => {const p=document.querySelector('.pdf-page'),s=document.querySelector('.pdf-scroll');return Math.abs(p.getBoundingClientRect().width-(s.clientWidth-24))<2})()"));
  await evaluate("document.querySelector('.pdf-toolbar button[aria-label=\"הגדלת מסמך\"]').click()");
  await until(async () => await evaluate("document.querySelector('.pdf-toolbar').textContent.includes('125%')"), "PDF zoom");
  await evaluate("document.querySelector('.pdf-toolbar button').click()");
  await until(async () => await evaluate("document.querySelector('.pdf-toolbar').textContent.includes('100%')"), "PDF fit to width");
  for (const width of [1440, 390, 320]) {
    await cdp("Emulation.setDeviceMetricsOverride", { width, height: 900, deviceScaleFactor: 1, mobile: width < 720 });
    await evaluate("document.querySelector('#review-workspace').scrollIntoView({block:'start'})"); await pause(150);
    assert.equal(await evaluate("document.documentElement.scrollWidth>innerWidth+1"), false, "Review workspace does not overflow horizontally");
    const shot=await cdp("Page.captureScreenshot",{format:"png",captureBeyondViewport:false});
    writeFileSync(resolve(directory,`review-${width}.png`),Buffer.from(shot.data,"base64"));
  }
  await cdp("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await evaluate("document.querySelector('[data-pdf-page=\"2\"]').scrollIntoView({block:'center'})");
  await until(async () => await evaluate("Boolean(document.querySelector('[data-pdf-page=\"2\"] canvas[data-rendered=true]'))"), "PDF second page scroll");
  await evaluate("document.querySelector('[data-row-action]').click()");
  await until(async () => await evaluate("Boolean(document.querySelector('dialog[open]'))"), "approval dialog while upload pending");
  await evaluate("document.querySelector('#final-reviewer').focus()");
  await cdp("Input.insertText", { text: "בודקת סינתטית" });
  await evaluate("document.querySelector('input[aria-label=\"הצהרת אישור לכל ערכי הטופס\"]').click()");
  await until(async () => await evaluate("Array.from(document.querySelectorAll('dialog button')).some(b=>b.textContent==='אישור סופי לטופס'&&!b.disabled)"), "explicit approval enabled");
  await evaluate("Array.from(document.querySelectorAll('dialog button')).find(b=>b.textContent==='אישור סופי לטופס').click()");
  await until(async () => await evaluate("document.querySelector('[data-row-action]').textContent==='אושר סופית' && !document.querySelector('dialog[open]')"), "persisted approval before second extraction");
  assert.equal(uploadCount, 2);
  await cdp("Fetch.continueRequest", { requestId: heldUpload });
  await until(async () => await evaluate("document.querySelectorAll('[data-upload-status=succeeded]').length===2"), "batch continues after review");
  assert.equal(await evaluate("document.querySelector('.pdf-viewer').dataset.sourceUrl"), firstPreview);
  assert.equal(await evaluate("document.querySelector('.review-heading .badge').textContent"), "הטופס אושר סופית");
  assert.equal(uploadCount, 2);
  await cdp("Fetch.disable");
  for (const mime of ["image/png", "image/jpeg"]) {
    await evaluate(`(async () => {
      const canvas=document.createElement('canvas');canvas.width=80;canvas.height=100;
      canvas.getContext('2d').fillRect(10,10,20,20);
      const blob=await new Promise(resolve=>canvas.toBlob(resolve,${JSON.stringify(mime)}));
      const files=new DataTransfer();files.items.add(new File([blob],'synthetic-image.'+(${JSON.stringify(mime)}==='image/png'?'png':'jpg'),{type:${JSON.stringify(mime)}}));
      const input=document.querySelector('#source-file');input.files=files.files;input.dispatchEvent(new Event('change',{bubbles:true}));
    })()`);
    await until(async () => await evaluate("document.querySelector('.document-preview img')?.naturalWidth===80"), "original image preview remains available");
  }
  const result = await evaluate(`(async () => {
    const h={'X-Demo-Request':'1'};
    const session=await (await fetch('/api/v1/auth/session',{headers:h})).json();
    const denied=await fetch('/api/v1/business-records',{headers:h,credentials:'omit'});
    const blocked=await fetch('/api/v1/auth/logout',{method:'POST',headers:h});
    const data=new FormData(); data.set('file',new File(['%PDF-1.7 synthetic allowlisted only'],'synthetic.pdf',{type:'application/pdf'})); data.set('allow_external_processing','true');
    const upload=await fetch('/api/v1/demo-runs',{method:'POST',headers:{...h,'X-Demo-CSRF':session.csrf_token},body:data});
    const run=await upload.json();
    const book=await fetch('/api/v1/demo-runs/'+run.run_id+'/export.xlsx',{headers:h});
    const logout=await fetch('/api/v1/auth/logout',{method:'POST',headers:{...h,'X-Demo-CSRF':session.csrf_token}});
    const after=await fetch('/api/v1/business-records',{headers:h});
    return {denied:denied.status,blocked:blocked.status,upload:upload.status,finalized:run.selected_form.finalized,
      workbook:book.status,workbookBytes:(await book.arrayBuffer()).byteLength,logout:logout.status,after:after.status};
  })()`);
  assert.deepEqual({ ...result, workbookBytes: 1 }, { denied: 401, blocked: 403, upload: 201, finalized: false, workbook: 200, workbookBytes: 1, logout: 200, after: 401 });
  assert.ok(result.workbookBytes > 1000);
  console.log("PASS: premium RTL login/workbench; first document approved while second upload waits; batch continues exactly once without changing selected source; real HTTPS first-party cookies, CSRF denial, synthetic upload/XLSX and logout revocation; zero database/Gemini calls.");
  console.log(`SCREENSHOTS: ${directory}`);
} finally {
  clearTimeout(deadline); socket?.close(); browser?.kill(); server.kill();
}
