export const fields = ["first_name", "last_name", "israeli_id", "central_numbers",
  "sample_receipt_date", "sender", "commitment_numbers"] as const;
export type FieldName = typeof fields[number];
export type Action = "approve_as_extracted" | "correct_and_approve" | "acknowledge_blank" | "reject" | "leave_unresolved";
export interface ReviewField {
  field_name: FieldName;
  provider_values: { raw: string; normalized: string | null }[];
  provider_value_status: string;
  provider_review_status: string | null;
  reviewed_values: string[];
  review_status: string;
  requires_review?: boolean;
  review_reasons?: string[];
  value_source?: string;
}
export interface SelectedForm { form_id: string; revision: number; review_status: string; fields: ReviewField[];
  finalized?: boolean; automatic_acceptance?: false; review_policy_version?: string }
export interface DemoRun {
  run_id: string; review_token: string; status: "processing" | "succeeded" | "failed";
  data_origin: "live"; form_ids: string[]; provider: string | null; model: string | null;
  selected_form: SelectedForm | null;
  failure: { code: string; message: string; retryable: boolean } | null;
}
export interface Draft { values: string[]; action: Action | ""; reason: string }
export type Drafts = Record<FieldName, Draft>;
export interface ReviewCommand {
  review_token: string; expected_revision: number; operator_declaration: string; declared_at: string;
  actions: { field_name: FieldName; decision_id: string; action: Action; reviewed_values: string[]; reason: string }[];
}

export interface FinalizeCommand {
  review_token: string; expected_revision: number; decision_id: string;
  operator_declaration: string; declared_at: string; attest_all_values: true;
}

export interface RecordsFilters { q: string; status: "" | "pending" | "completed"; sender: string; receipt_date: string }
export interface SavedRecord {
  run_id: string; document_id: string; form_id: string; revision: number;
  review_status: "pending" | "completed"; summary: Record<FieldName, string[]>;
  created_at: string; updated_at: string;
  finalized?: boolean;
}
export interface RecordsPage { items: SavedRecord[]; total: number; limit: number; offset: number }

export async function getRecords(filters: RecordsFilters, offset = 0): Promise<RecordsPage> {
  const query = new URLSearchParams({ limit: "20", offset: String(offset) });
  for (const [key, value] of Object.entries(filters)) if (value.trim()) query.set(key, value.trim());
  return (await request(`/api/v1/business-records?${query}`)).json();
}

export async function getSavedSource(runId: string): Promise<Blob> {
  const response = await request(`/api/v1/demo-runs/${encodeURIComponent(runId)}/source`);
  const blob = await response.blob();
  if (!["application/pdf", "image/png", "image/jpeg"].includes(blob.type)) throw new Error("Invalid source type");
  return blob;
}

export function initialDrafts(form: SelectedForm): Drafts {
  return Object.fromEntries(form.fields.map(f => [f.field_name, {
    values: f.review_status === "unreviewed" ? f.provider_values.map(v => v.raw) : [...f.reviewed_values],
    action: "", reason: "",
  }])) as Drafts;
}

export function draftChanges(drafts: Drafts, saved?: Drafts): Drafts {
  return Object.fromEntries(fields.map(name => {
    const draft = drafts[name], previous = saved?.[name];
    const unchanged = previous && draft.action === previous.action && draft.reason === previous.reason &&
      draft.values.length === previous.values.length && draft.values.every((value, i) => value === previous.values[i]);
    return [name, unchanged ? { ...draft, action: "" } : draft];
  })) as Drafts;
}

export function savedDrafts(form: SelectedForm, submitted: Drafts): Drafts {
  const persisted = initialDrafts(form);
  return Object.fromEntries(fields.map(name => [name, {
    ...persisted[name], action: submitted[name].action, reason: submitted[name].reason,
  }])) as Drafts;
}

export function makeCommand(run: DemoRun, drafts: Drafts, operator: string,
  uuid = () => crypto.randomUUID(), now = () => new Date().toISOString()): ReviewCommand {
  if (!run.selected_form) throw new Error("No selected form");
  return {
    review_token: run.review_token, expected_revision: run.selected_form.revision,
    operator_declaration: operator, declared_at: now(),
    actions: run.selected_form.fields.flatMap(f => {
      const draft = drafts[f.field_name];
      if (!draft.action) return [];
      const values = draft.action === "approve_as_extracted" ? f.provider_values.map(v => v.raw)
        : ["acknowledge_blank", "reject"].includes(draft.action) ? [] : [...draft.values];
      return [{ field_name: f.field_name, decision_id: uuid(), action: draft.action,
        reviewed_values: values, reason: draft.reason }];
    }),
  };
}

export class APIError extends Error {
  readonly code: string;
  readonly status: number;
  readonly failedRun?: DemoRun;
  constructor(code: string, status: number, failedRun?: DemoRun) {
    super(code); this.code = code; this.status = status; this.failedRun = failedRun;
  }
}
function failedUpload(input: unknown): DemoRun | undefined {
  if (!input || typeof input !== "object") return;
  const value = input as Record<string, unknown>;
  if (!value.failure || typeof value.failure !== "object") return;
  const failure = value.failure as Record<string, unknown>;
  if (value.status !== "failed" || value.selected_form !== null || value.data_origin !== "live"
      || !Array.isArray(value.form_ids) || value.form_ids.length || typeof value.run_id !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(value.run_id)
      || typeof value.review_token !== "string" || !/^[A-Za-z0-9_-]{32,128}$/.test(value.review_token)
      || typeof failure.code !== "string" || !/^[A-Z0-9_]+$/.test(failure.code) || typeof failure.retryable !== "boolean") return;
  return { run_id: value.run_id, review_token: value.review_token, status: "failed", data_origin: "live",
    form_ids: [], selected_form: null, provider: typeof value.provider === "string" ? value.provider : null,
    model: typeof value.model === "string" ? value.model : null,
    failure: { code: failure.code, retryable: failure.retryable, message: "Extraction failed; no fallback was used." } };
}
let apiBase = "";
let publicDemo = false;
let csrfToken = "";
let expired: (() => void) | undefined;
export function configureAPI(options: { baseURL: string; publicDemo: boolean; onUnauthorized?: () => void }) {
  if (options.baseURL && (!/^https:\/\/[^/]+$/.test(options.baseURL) || new URL(options.baseURL).username
      || new URL(options.baseURL).password || new URL(options.baseURL).search || new URL(options.baseURL).hash)) {
    throw new Error("HTTPS_API_ORIGIN_REQUIRED");
  }
  apiBase = options.baseURL; publicDemo = options.publicDemo; expired = options.onUnauthorized; csrfToken = "";
}
export function setCSRFToken(value: string) { csrfToken = value; }
export async function request(path: string, options: RequestInit = {}): Promise<Response> {
  if (!path.startsWith("/api/v1/") || path.includes("\\") || path.split("?")[0].split("/").includes("..")) throw new Error("API_PATH_REQUIRED");
  const headers = new Headers(options.headers);
  if (publicDemo) {
    headers.set("X-Demo-Request", "1");
    if (csrfToken && !["GET", "HEAD"].includes((options.method ?? "GET").toUpperCase())) headers.set("X-Demo-CSRF", csrfToken);
  } else headers.set("X-Local-Demo", "1");
  const response = await fetch(apiBase + path, { ...options, headers, cache: "no-store", credentials: publicDemo ? "include" : "omit" });
  if (!response.ok) {
    if (publicDemo && response.status === 401 && !path.startsWith("/api/v1/auth/")) expired?.();
    const error = await response.json().catch(() => null);
    const code = typeof error?.error?.code === "string" && /^[A-Z0-9_]+$/.test(error.error.code)
      ? error.error.code : "DEMO_REQUEST_FAILED";
    throw new APIError(code, response.status, path === "/api/v1/demo-runs" ? failedUpload(error) : undefined);
  }
  return response;
}
export const getRun = async (id: string, form?: string): Promise<DemoRun> =>
  (await request(`/api/v1/demo-runs/${encodeURIComponent(id)}${form ? `?form_id=${encodeURIComponent(form)}` : ""}`)).json();
export const postReview = async (id: string, form: string, body: ReviewCommand): Promise<DemoRun> =>
  (await request(`/api/v1/demo-runs/${encodeURIComponent(id)}/forms/${encodeURIComponent(form)}/review`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })).json();

export const postFinalization = async (id: string, form: string, body: FinalizeCommand): Promise<DemoRun> =>
  (await request(`/api/v1/demo-runs/${encodeURIComponent(id)}/forms/${encodeURIComponent(form)}/finalize`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })).json();

export interface ExportSelection { run_id: string; form_id: string; revision: number; commitment: string | null }
export async function exportRows(rows: ExportSelection[], failures: DemoRun[] = []): Promise<Blob> {
  if (!rows.length && !failures.length) throw new Error("EMPTY_EXPORT_SELECTION");
  const tokens = new Map<string, string>();
  for (const id of new Set(rows.map(row => row.run_id))) {
    const run = await getRun(id);
    if (run.run_id !== id) throw new Error("EXPORT_BINDING_MISMATCH");
    tokens.set(id, run.review_token);
  }
  const forms = new Map<string, { run_id: string; form_id: string; expected_revision: number; review_token: string; commitments: (string | null)[] }>();
  for (const row of rows) {
    const key = JSON.stringify([row.run_id, row.form_id]);
    const group = forms.get(key) ?? { run_id: row.run_id, form_id: row.form_id, expected_revision: row.revision,
      review_token: tokens.get(row.run_id)!, commitments: [] };
    if (!group.commitments.includes(row.commitment)) group.commitments.push(row.commitment);
    forms.set(key, group);
  }
  const response = await request("/api/v1/business-records/export.xlsx", { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify({ forms: [...forms.values()],
      failures: failures.map(run => ({ run_id: run.run_id, review_token: run.review_token })) }) });
  return response.blob();
}
