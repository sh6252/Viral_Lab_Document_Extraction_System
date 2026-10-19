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
}
export interface SelectedForm { form_id: string; revision: number; review_status: string; fields: ReviewField[] }
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

export interface RecordsFilters { q: string; status: "" | "pending" | "completed"; sender: string; receipt_date: string }
export interface SavedRecord {
  run_id: string; document_id: string; form_id: string; revision: number;
  review_status: "pending" | "completed"; summary: Record<FieldName, string[]>;
  created_at: string; updated_at: string;
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
  constructor(code: string, status: number) { super(code); this.code = code; this.status = status; }
}
export async function request(path: string, options: RequestInit = {}): Promise<Response> {
  const headers = new Headers(options.headers);
  headers.set("X-Local-Demo", "1");
  const response = await fetch(path, { ...options, headers, cache: "no-store", credentials: "omit" });
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    const code = typeof error?.error?.code === "string" && /^[A-Z0-9_]+$/.test(error.error.code)
      ? error.error.code : "DEMO_REQUEST_FAILED";
    throw new APIError(code, response.status);
  }
  return response;
}
export const getRun = async (id: string, form?: string): Promise<DemoRun> =>
  (await request(`/api/v1/demo-runs/${encodeURIComponent(id)}${form ? `?form_id=${encodeURIComponent(form)}` : ""}`)).json();
export const postReview = async (id: string, form: string, body: ReviewCommand): Promise<DemoRun> =>
  (await request(`/api/v1/demo-runs/${encodeURIComponent(id)}/forms/${encodeURIComponent(form)}/review`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })).json();
