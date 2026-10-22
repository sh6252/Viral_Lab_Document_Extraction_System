// Runs the actual App handlers and JSX without a DOM or new dependencies.
// This is a component-state test, not a substitute for a real browser check.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import vm from "node:vm";
import ts from "typescript";
import * as api from "../src/api.ts";
import * as workbench from "../src/workbench.ts";

interface Element { type: string; props: Record<string, any> }
const require = createRequire(import.meta.url);

export function text(node: any): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node !== "object") return String(node);
  if (Array.isArray(node)) return node.map(text).join("");
  return text(node.props?.children);
}

function elements(node: any): Element[] {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!node || typeof node !== "object") return [];
  return [node, ...elements(node.props?.children)];
}

export function appHarness(sourceURL = new URL("../src/App.tsx", import.meta.url)) {
  const slots: any[] = [];
  let cursor = 0;
  let tree: any;
  let downloaded: Blob | undefined;
  let confirmations = 0;
  let allowDiscard = true;
  const exports: Record<string, any> = {};
  const source = readFileSync(sourceURL, "utf8");
  const compiled = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText;
  const context = {
    exports, module: { exports },
    require(name: string) {
      if (name === "react") return {
        useRef(initial: any) {
          const index = cursor++;
          if (!(index in slots)) slots[index] = { current: initial };
          return slots[index];
        },
        useState(initial: any) {
          const index = cursor++;
          if (!(index in slots)) slots[index] = initial;
          return [slots[index], (value: any) => {
            slots[index] = typeof value === "function" ? value(slots[index]) : value;
          }];
        },
        useEffect() {}, // Source preview lifecycle is outside these save tests.
      };
      if (name === "./api.ts") return api;
      if (name === "./workbench.ts") return workbench;
      if (name === "./PdfPreview.tsx") return { PdfPreview: function PdfPreview() { return null; } };
      return require(name);
    },
    FormData, crypto, Date,
    URL: { createObjectURL(blob: Blob) { downloaded = blob; return "blob:synthetic"; }, revokeObjectURL() {} },
    window: { confirm() { confirmations++; return allowDiscard; } },
    document: { createElement() { return { click() {}, remove() {} }; }, body: { append() {} } },
    setTimeout(callback: () => void) { callback(); },
  };
  vm.runInNewContext(compiled, context, { filename: "App.test.cjs" });
  function render() { cursor = 0; tree = exports.default(); }
  function one(predicate: (node: Element) => boolean, root = tree) {
    const matches = elements(root).filter(predicate);
    assert.equal(matches.length, 1, "Expected exactly one matching App element");
    return matches[0];
  }
  function button(label: string) { return one(n => n.type === "button" && (text(n) === label || n.props["aria-label"] === label)); }
  function field(name: string) {
    focusField(name);
    return one(n => n.type === "fieldset" && n.props["data-field"] === name);
  }
  function focusField(name: string) {
    one(n => n.type === "fieldset" && n.props["data-field"] === name);
  }
  // Normalize selected-action state for existing save assertions, independent of control type.
  function action(name: string) {
    const group = one(n => n.props.role === "group" && "data-review-action" in n.props, field(name));
    return { ...group, props: { ...group.props, value: group.props["data-review-action"] } };
  }
  function reason(name: string) { return one(n => n.type === "input" && n.props["data-review-reason"], field(name)); }
  function actionButton(name: string, value: string) { return one(n => n.type === "button" && n.props["data-action"] === value, field(name)); }
  function change(element: Element, value: string) { element.props.onChange({ target: { value } }); render(); }
  async function click(label: string) {
    const target = button(label); assert.ok(!target.props.disabled, `${label} must be enabled`);
    const completion = target.props.onClick(); render(); await completion; render();
  }
  render();
  return {
    button, action, actionButton, reason, click, focusField, render,
    check(label: string, checked = true) {
      one(n => n.type === "input" && n.props["aria-label"] === label).props.onChange({ target: { checked } }); render();
    },
    rowActions() { return elements(tree).filter(n => n.type === "button" && n.props["data-row-action"]); },
    async openApproval(index = 0) {
      const target = elements(tree).filter(n => n.type === "button" && n.props["data-row-action"])[index];
      assert.ok(target && !target.props.disabled, "Row action must be enabled");
      const completion = target.props.onClick({ currentTarget: undefined }); render(); await completion; render();
    },
    get tree() { return tree; },
    nodes() { return elements(tree); },
    get download() { return downloaded; },
    get confirmations() { return confirmations; },
    setAllowDiscard(value: boolean) { allowDiscard = value; },
    input(label: string, value: string) {
      change(one(n => ["input", "select"].includes(n.type) && n.props["aria-label"] === label), value);
    },
    attest(checked = true) {
      one(n => n.type === "input" && n.props["aria-label"] === "הצהרת אישור לכל ערכי הטופס").props.onChange({ target: { checked } });
      render();
    },
    drop(files: File[]) {
      one(n => n.type === "div" && typeof n.props.onDrop === "function").props.onDrop({ preventDefault() {}, dataTransfer: { files } });
      render();
    },
    async openRecord(index = 0) {
      const candidates = elements(tree).filter(n => n.type === "button" && String(n.props["aria-label"] ?? "").startsWith("פתיחת רשומה "));
      const target = [...new Map(candidates.map(node => [node.props["aria-label"], node])).values()][index];
      assert.ok(target && !target.props.disabled);
      const completion = target.props.onClick(); render(); await completion; render();
    },
    hasPreview() { return elements(tree).some(n => n.props.url === "blob:synthetic" || (n.type === "iframe" && n.props.src === "blob:synthetic")); },
    operator(value: string) {
      change(one(n => n.type === "input" && n.props.id === "operator-declaration"), value);
    },
    async upload(file = new File(["%PDF-1.7 synthetic"], "synthetic.pdf", { type: "application/pdf" })) {
      one(n => n.type === "input" && n.props.type === "file").props.onChange({ target: { files: [file] } }); render();
      assert.ok(button("העלאה וחילוץ").props.disabled, "Consent is mandatory");
      one(n => n.type === "input" && n.props["aria-label"] === "אישור עיבוד חיצוני לכל הקבצים").props.onChange({ target: { checked: true } }); render();
      await click("העלאה וחילוץ");
    },
    decide(name: string, value: string, why = "Inspected synthetic PDF") {
      // Stage choices for the explicit multi-field save tests: supply the reviewer last.
      const reviewer = one(n => n.type === "input" && n.props.id === "operator-declaration");
      const operator = reviewer.props.value;
      change(reviewer, "");
      actionButton(name, value).props.onClick(); render();
      change(reason(name), why);
      change(one(n => n.type === "input" && n.props.id === "operator-declaration"), operator);
    },
    async act(name: string, value: string) {
      const target = actionButton(name, value);
      assert.ok(!target.props.disabled);
      const completion = target.props.onClick(); render(); await completion; render();
    },
    setReason(name: string, value: string) { change(reason(name), value); },
    value(name: string, index: number, value: string) {
      focusField(name);
      change(one(n => n.type === "input" && n.props["data-value-field"] === name && n.props["data-value-index"] === index), value);
    },
    async selectForm(id: string) {
      const target = one(n => n.type === "select" && elements(n).some(c => c.type === "option" && c.props.value === id));
      const completion = target.props.onChange({ target: { value: id } }); render(); await completion; render();
    },
  };
}
