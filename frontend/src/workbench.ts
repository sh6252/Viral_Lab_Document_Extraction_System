// View-only projection. Never changes extraction/review contracts or approves data.
import { draftChanges, fields } from "./api.ts";
import type { Drafts, FieldName, ReviewField, SelectedForm } from "./api.ts";

export const humanApproved = (field: ReviewField): boolean =>
  ["approved_provider", "approved_corrected", "acknowledged_blank"].includes(field.review_status);

export function needsAttention(field: ReviewField): boolean {
  if (typeof field.requires_review === "boolean") return field.requires_review;
  if (field.review_status === "accepted_by_form") return false;
  if (humanApproved(field)) return false;
  return field.review_status !== "unreviewed" || field.provider_value_status !== "present" ||
    !field.provider_values.length || field.provider_values.some(value => !value.raw.trim()) ||
    ![null, "unreviewed", "approved_as_extracted", "corrected", "approved_corrected", "acknowledged_blank"].includes(field.provider_review_status);
}

export function fieldState(field: ReviewField): string {
  if (field.review_status === "accepted_by_form") return "נכלל בהצהרת אישור הטופס";
  if (field.review_status === "approved_corrected") return "תוקן ואושר אנושית";
  if (field.review_status === "approved_provider") return "אושר אנושית";
  if (field.review_status === "acknowledged_blank") return "אושר כשדה ריק";
  if (field.review_status !== "unreviewed") return "ממתין לבירור";
  return needsAttention(field) ? "דורש טיפול" : "חולץ · ללא אישור אנושי";
}

export function attentionReason(field: ReviewField): string {
  if (field.review_reasons?.length) return field.review_reasons.map(reason => ({
    MISSING_VALUE: "חסר ערך — יש לתקן או לאשר במפורש שדה ריק",
    INVALID_DATE: "תאריך לא תקין או פורמט לא נתמך. יש להזין DD.MM.YYYY, DD/MM/YYYY או YYYY-MM-DD",
    CONTRADICTORY_SCALAR_VALUES: "ערכים סותרים בשדה יחיד", UNVERIFIED_NORMALIZATION: "נרמול לא מאומת",
    PROVIDER_REVIEW_REQUIRED: "החילוץ דורש בדיקה או הבהרה", PROVIDER_VALUE_UNCERTAIN: "ערך חסר או לא ודאי",
    PROVIDER_NOTES_REQUIRE_REVIEW: "הערת חילוץ דורשת בדיקת מקור",
    PROVIDER_SIGNALS_REQUIRE_REVIEW: "דגלי חילוץ דורשים בדיקה", UNRESOLVED_REVIEW: "הבדיקה טרם הוכרעה",
  } as Record<string, string>)[reason] ?? "נדרשת בדיקת מקור").join(" · ");
  if (!needsAttention(field)) return "ללא דגל מדווח; אין בכך אומדן ביטחון";
  if (field.review_status !== "unreviewed") return "החלטת הביקורת הותירה את השדה לבירור";
  return ({ missing: "חסר במקור", unreadable: "לא קריא", ambiguous: "לא חד־משמעי",
    not_processed: "טרם עובד" } as Record<string, string>)[field.provider_value_status] ??
    (!field.provider_values.length || field.provider_values.some(v => !v.raw.trim()) ? "ערך ריק או לא שלם" : "דגל בדיקה מהחילוץ");
}

// Only explicit server policy flags establish eligibility. Legacy visual fallbacks
// remain useful for the table, but cannot authorize finalization.
export function finalizationBlockers(form: SelectedForm): ReviewField[] {
  return form.fields.filter(field => field.requires_review === true);
}

export function eligibilityKnown(form: SelectedForm): boolean {
  return typeof form.finalized === "boolean" && fields.every(name =>
    form.fields.some(field => field.field_name === name && typeof field.requires_review === "boolean"));
}

export interface WorkForm { run_id: string; form: SelectedForm }
export interface WorkCell { values: string[]; field: ReviewField; attention: boolean; draft: boolean }
export interface CommitmentRow {
  key: string; run_id: string; form_id: string; commitment: string | null;
  revision: number; review_status: string; cells: Record<FieldName, WorkCell>;
  finalized?: boolean;
}
export type WorkFilter = "all" | "attention" | "unapproved" | "approved";

export function mergeForms(current: WorkForm[], incoming: WorkForm[]): WorkForm[] {
  const merged = new Map(current.map(entry => [JSON.stringify([entry.run_id, entry.form.form_id]), entry]));
  for (const entry of incoming) merged.set(JSON.stringify([entry.run_id, entry.form.form_id]), entry);
  return [...merged.values()];
}

export function commitmentRows(entries: WorkForm[], activeRun?: string,
  drafts: Record<string, Drafts> = {}, saved: Record<string, Drafts> = {}): CommitmentRow[] {
  return entries.flatMap(entry => {
    const changes = entry.run_id === activeRun && drafts[entry.form.form_id]
      ? draftChanges(drafts[entry.form.form_id], saved[entry.form.form_id]) : undefined;
    const cells = Object.fromEntries(entry.form.fields.map(field => {
      const draft = changes?.[field.field_name];
      let values = field.review_status === "unreviewed" ? field.provider_values.map(v => v.raw) : [...field.reviewed_values];
      if (draft?.action === "correct_and_approve") values = [...draft.values];
      if (draft?.action === "approve_as_extracted") values = field.provider_values.map(v => v.raw);
      if (draft?.action === "acknowledge_blank" || draft?.action === "reject") values = [];
      // Tentative unresolved text remains in the draft/editor, never business values.
      return [field.field_name, { values, field, attention: needsAttention(field), draft: Boolean(draft?.action) }];
    })) as Record<FieldName, WorkCell>;
    const commitments: (string | null)[] = distinctCommitments(cells.commitment_numbers.values);
    return commitments.map((commitment, index) => ({
      key: JSON.stringify([entry.run_id, entry.form.form_id, commitment, index]),
      run_id: entry.run_id, form_id: entry.form.form_id, commitment,
      revision: entry.form.revision, review_status: entry.form.review_status, finalized: entry.form.finalized,
      cells: { ...cells, commitment_numbers: { ...cells.commitment_numbers, values: commitment === null ? [] : [commitment] } },
    }));
  });
}

export function distinctCommitments(values: string[]): (string | null)[] {
  // Exact text only; never trim, normalize, zip with samples, or deduplicate across forms.
  return values.length ? [...new Set(values)] : [null];
}

export function matchesWorkFilter(row: CommitmentRow, filter: WorkFilter): boolean {
  if (filter === "attention") return fields.some(name => row.cells[name].attention);
  if (filter === "unapproved") return row.finalized !== undefined
    ? !row.finalized || fields.some(name => row.cells[name].draft)
    : fields.some(name => !humanApproved(row.cells[name].field) || row.cells[name].draft);
  if (filter === "approved") return row.finalized !== undefined
    ? row.finalized && fields.every(name => !row.cells[name].draft)
    : fields.every(name => humanApproved(row.cells[name].field) && !row.cells[name].draft);
  return true;
}

export function exceptionQueue(rows: CommitmentRow[]): { run_id: string; form_id: string; field: FieldName }[] {
  const queue = new Map<string, { run_id: string; form_id: string; field: FieldName }>();
  for (const row of rows) for (const field of fields) if (row.cells[field].attention) {
    queue.set(JSON.stringify([row.run_id, row.form_id, field]), { run_id: row.run_id, form_id: row.form_id, field });
  }
  return [...queue.values()];
}
