import { useEffect, useRef, useState } from "react";
import { APIError, draftChanges, exportRows, fields, getRecords, getRun, getSavedSource, initialDrafts, makeCommand, postFinalization, postReview, request, savedDrafts } from "./api.ts";
import type { Action, DemoRun, Draft, Drafts, FieldName, FinalizeCommand, RecordsFilters, RecordsPage, ReviewCommand, SavedRecord } from "./api.ts";
import { attentionReason, commitmentRows, distinctCommitments, eligibilityKnown, exceptionQueue, fieldState, finalizationBlockers, humanApproved, matchesWorkFilter, mergeForms, needsAttention } from "./workbench.ts";
import type { CommitmentRow, WorkFilter, WorkForm } from "./workbench.ts";

const actionLabels: [Action, string][] = [
  ["approve_as_extracted", "אישור כל ערכי המקור"], ["correct_and_approve", "תיקון ואישור"],
  ["acknowledge_blank", "אישור שדה ריק"], ["reject", "דחייה והעברה לבירור"],
  ["leave_unresolved", "השארה לבירור"],
];

const fieldLabels: Record<FieldName, string> = {
  first_name: "שם פרטי", last_name: "שם משפחה", israeli_id: "תעודת זהות",
  central_numbers: "מספרים מרכזיים", sample_receipt_date: "תאריך קבלת דגימה",
  sender: "גורם שולח", commitment_numbers: "מספרי התחייבות",
};
const reviewLabels: Record<string, string> = {
  unreviewed: "טרם נבדק", unresolved: "ממתין לבדיקה", rejected_unresolved: "ממתין לבירור",
  approved_provider: "ערכי המקור אושרו", approved_corrected: "תוקן ואושר",
  acknowledged_blank: "אושר כשדה ריק", complete: "הבדיקה הושלמה",
  accepted_by_form: "נכלל בהצהרת אישור הטופס",
};
const valueLabels: Record<string, string> = {
  present: "נמצא במקור", missing: "חסר במקור", unreadable: "לא קריא",
  ambiguous: "לא חד־משמעי", not_processed: "טרם עובד",
};
const approvedStates = ["approved_provider", "approved_corrected", "acknowledged_blank"];
const numericFields = ["israeli_id", "central_numbers", "commitment_numbers", "sample_receipt_date"];

function Icon({ name, size = 20 }: { name: "upload" | "records" | "check" | "arrow" | "file" | "shield" | "download" | "search" | "alert" | "plus"; size?: number }) {
  const paths = {
    upload: "M12 16V4m-5 5 5-5 5 5M4 16v4h16v-4",
    records: "M8 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2h-3M8 3h8v4H8zM7 12h10M7 16h7",
    check: "m5 12 4 4L19 6",
    arrow: "M19 12H5m6-6-6 6 6 6",
    file: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8ZM14 2v6h6M8 13h8M8 17h5",
    shield: "m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6ZM8 12l3 3 5-6",
    download: "M12 3v12m-5-5 5 5 5-5M4 17v4h16v-4",
    search: "m21 21-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
    alert: "m12 3 10 18H2ZM12 9v4m0 4h.01",
    plus: "M12 5v14M5 12h14",
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>;
}

export default function App() {
  const [view, setView] = useState<"workspace" | "records">("workspace");
  const [dragging, setDragging] = useState(false);
  const [workForms, setWorkForms] = useState<WorkForm[]>([]);
  const [workFilter, setWorkFilter] = useState<WorkFilter>("all");
  const [workPage, setWorkPage] = useState(0);
  const [focusedField, setFocusedField] = useState<FieldName | null>(null);
  const [uploadExpanded, setUploadExpanded] = useState(true);
  const [file, setFile] = useState<File | null>(null);
  const [uploadQueue, setUploadQueue] = useState<{ id: string; file: File; status: "queued" | "processing" | "succeeded" | "failed"; code?: string }[]>([]);
  const [failedRuns, setFailedRuns] = useState<DemoRun[]>([]);
  const [includeFailures, setIncludeFailures] = useState(false);
  const [selectedRows, setSelectedRows] = useState<Set<string>>(new Set());
  const [preview, setPreview] = useState("");
  const [consent, setConsent] = useState(false);
  const [run, setRun] = useState<DemoRun | null>(null);
  const [drafts, setDrafts] = useState<Record<string, Drafts>>({});
  const [saved, setSaved] = useState<Record<string, Drafts>>({});
  const [operator, setOperator] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pending, setPending] = useState<{ form: string; command: ReviewCommand } | null>(null);
  const [pendingFinal, setPendingFinal] = useState<{ form: string; command: FinalizeCommand } | null>(null);
  const [attestation, setAttestation] = useState(false);
  const [refreshRequired, setRefreshRequired] = useState(false);
  const [approvalOpen, setApprovalOpen] = useState(false);
  const [blockersOnly, setBlockersOnly] = useState(false);
  const approvalDialog = useRef<HTMLDialogElement | null>(null);
  const approvalTrigger = useRef<HTMLElement | null>(null);
  const finalRequestActive = useRef(false);
  const [savedPreview, setSavedPreview] = useState<{ url: string; mime: string } | null>(null);
  const [filters, setFilters] = useState<RecordsFilters>({ q: "", status: "", sender: "", receipt_date: "" });
  const [appliedFilters, setAppliedFilters] = useState(filters);
  const [records, setRecords] = useState<RecordsPage | null>(null);
  const [recordsBusy, setRecordsBusy] = useState(false);
  const [recordsError, setRecordsError] = useState("");
  const recordsRequest = useRef(0);
  useEffect(() => setAttestation(false), [run?.run_id, run?.selected_form?.form_id, run?.selected_form?.revision]);
  useEffect(() => {
    const dialog = approvalDialog.current;
    if (!approvalOpen || !dialog) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialog.showModal();
    return () => {
      dialog.close(); document.body.style.overflow = previousOverflow;
      const target = approvalTrigger.current;
      if (target?.isConnected && !target.matches(":disabled")) target.focus();
      else document.getElementById("commitment-workbench")?.focus();
    };
  }, [approvalOpen]);
  useEffect(() => {
    if (!file) { setPreview(""); return; }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);
  useEffect(() => () => { if (savedPreview) URL.revokeObjectURL(savedPreview.url); }, [savedPreview]);
  useEffect(() => { void loadRecords(0); return () => { recordsRequest.current++; }; }, []);
  async function loadRecords(offset = 0, query = filters) {
    const requestId = ++recordsRequest.current;
    setRecordsBusy(true); setRecordsError("");
    try {
      const page = await getRecords(query, offset);
      if (requestId === recordsRequest.current) { setRecords(page); setAppliedFilters({ ...query }); }
    } catch (e) {
      if (requestId === recordsRequest.current) {
        setRecords(null);
        setRecordsError(e instanceof APIError && e.code === "DURABLE_RECORDS_UNAVAILABLE"
          ? "רשומות שמורות זמינות בהפעלת השרת עם PostgreSQL בלבד."
          : "טעינת הרשומות נכשלה. אפשר לנסות שוב; נתוני הבדיקה לא שונו.");
      }
    } finally { if (requestId === recordsRequest.current) setRecordsBusy(false); }
  }
  function unsavedFields(id: string): FieldName[] {
      const group = drafts[id];
      if (!group) return [];
      const original = workForms.find(entry => entry.run_id === run?.run_id && entry.form.form_id === id)?.form;
      const baseline = saved[id] ?? (original ? initialDrafts(original) : undefined);
      return fields.filter(name => {
        const draft = group[name], previous = baseline?.[name];
        return !previous ? Boolean(draft.action || draft.reason) : draft.action !== previous.action || draft.reason !== previous.reason ||
          draft.values.length !== previous.values.length || draft.values.some((value, i) => value !== previous.values[i]);
      });
  }
  function unsavedChoices(id?: string) {
    return (id ? [id] : Object.keys(drafts)).some(key => unsavedFields(key).length > 0);
  }
  function remember(value: DemoRun) {
    if (value.status === "succeeded" && value.selected_form) {
      setWorkForms(old => mergeForms(old, [{ run_id: value.run_id, form: value.selected_form! }]));
    }
  }
  async function documentForms(value: DemoRun) {
    remember(value);
    const ids = value.form_ids.filter(id => id !== value.selected_form?.form_id);
    let incomplete = false;
    for (let i = 0; i < ids.length; i += 4) {
      const loaded = await Promise.allSettled(ids.slice(i, i + 4).map(async id => {
        const next = await getRun(value.run_id, id);
        if (next.run_id !== value.run_id || next.selected_form?.form_id !== id || next.status !== "succeeded") throw new Error("Form mismatch");
        return { run_id: next.run_id, form: next.selected_form };
      }));
      const entries = loaded.flatMap(result => result.status === "fulfilled" ? [result.value] : []);
      setWorkForms(old => mergeForms(old, entries));
      incomplete ||= loaded.some(result => result.status === "rejected");
    }
    if (incomplete) setError("חלק מהטפסים לא נטענו לטבלה. מוצגים רק טפסים שהתקבלו מהשרת; אפשר לטעון אותם שוב בבחירת הטופס.");
  }
  async function loadRecordsIntoWorkspace() {
    if (!records || busy || pending || pendingFinal || recordsBusy) return;
    setBusy(true); setError(""); setNotice("");
    let loaded = 0, failed = 0;
    for (let i = 0; i < records.items.length; i += 4) {
      const batch = await Promise.allSettled(records.items.slice(i, i + 4).map(async record => {
        const next = await getRun(record.run_id, record.form_id);
        if (next.run_id !== record.run_id || next.selected_form?.form_id !== record.form_id || next.status !== "succeeded") throw new Error("Record mismatch");
        return { run_id: next.run_id, form: next.selected_form };
      }));
      const entries = batch.flatMap(result => result.status === "fulfilled" ? [result.value] : []);
      loaded += entries.length; failed += batch.length - entries.length;
      setWorkForms(old => mergeForms(old, entries));
    }
    setBusy(false); setView("workspace"); setWorkPage(0);
    setNotice(`נטענו ${loaded} טפסים קיימים לשולחן העבודה. לא בוצע חילוץ חדש.`);
    if (failed) setError(`${failed} טפסים לא נטענו. נתוני הביקורת הקיימים לא שונו.`);
  }
  async function reopen(record: Pick<SavedRecord, "run_id" | "form_id">) {
    if (busy || pending || pendingFinal) return false;
    if (unsavedChoices() && !window.confirm("פתיחת רשומה אחרת תבטל בחירות שטרם נשמרו. להמשיך?")) return false;
    setBusy(true); setError(""); setNotice("");
    try {
      const next = await getRun(record.run_id, record.form_id);
      if (next.run_id !== record.run_id || next.selected_form?.form_id !== record.form_id) throw new Error("Record mismatch");
      const blob = await getSavedSource(record.run_id);
      const url = URL.createObjectURL(blob);
      setFile(null); setSavedPreview({ url, mime: blob.type }); setConsent(false);
      setRun(next); setRefreshRequired(false); setDrafts({ [record.form_id]: initialDrafts(next.selected_form) });
      setSaved({}); setPending(null); setOperator("");
      setView("workspace"); setUploadExpanded(false);
      setFocusedField(next.selected_form.fields.find(needsAttention)?.field_name ?? null);
      setNotice("הרשומה והמסמך נטענו מהמאגר. לא בוצעה פנייה ל-Gemini.");
      await documentForms(next);
      return next;
    } catch (e) {
      setError(e instanceof APIError ? `פתיחת הרשומה נכשלה (${e.code}). הבדיקה הנוכחית נשמרה במסך.`
        : "פתיחת הרשומה או התצוגה נכשלה. הבדיקה הנוכחית נשמרה במסך.");
      return false;
    } finally { setBusy(false); }
  }
  function accept(value: DemoRun) {
    setRun(value);
    setRefreshRequired(false);
    remember(value);
    setFocusedField(value.selected_form?.fields.find(needsAttention)?.field_name ?? null);
    if (value.status === "succeeded") setUploadExpanded(false);
    if (value.selected_form) {
      const form = value.selected_form;
      setDrafts(old => ({ ...old, [form.form_id]: initialDrafts(form) }));
    }
  }
  async function upload() {
    if (!file || !consent || !uploadQueue.some(item => item.status === "queued")) return;
    setBusy(true); setError(""); setNotice(""); setRun(null); setDrafts({}); setSaved({}); setPending(null);
    try {
      for (const item of uploadQueue.filter(item => item.status === "queued")) {
        setUploadQueue(old => old.map(entry => entry.id === item.id ? { ...entry, status: "processing" } : entry));
        const body = new FormData(); body.append("file", item.file); body.append("allow_external_processing", "true");
        try {
          const next = await (await request("/api/v1/demo-runs", { method: "POST", body })).json() as DemoRun;
          if (!["succeeded", "failed"].includes(next.status) || !next.run_id) throw new Error("Unexpected upload response");
          setFile(item.file); setSavedPreview(null); setDrafts({}); setSaved({}); accept(next);
          setUploadQueue(old => old.map(entry => entry.id === item.id ? { ...entry, status: next.status as "succeeded" | "failed", code: next.failure?.code } : entry));
          if (next.status === "succeeded") await documentForms(next);
          else setFailedRuns(old => [...old.filter(run => run.run_id !== next.run_id), next]);
          if (next.failure?.code === "PROVIDER_RATE_LIMIT") {
            setError("התור הושהה עקב מגבלת קצב. לא בוצע ניסיון חוזר. יתר הקבצים ממתינים להמשך יזום."); break;
          }
        } catch (e) {
          setUploadQueue(old => old.map(entry => entry.id === item.id ? { ...entry, status: "failed", code: e instanceof APIError ? e.code : "UNKNOWN_UPLOAD_RESULT" } : entry));
          if (e instanceof APIError && e.failedRun) {
            const failed = e.failedRun;
            setFile(item.file); setSavedPreview(null); accept(failed);
            setFailedRuns(old => old.some(value => value.run_id === failed.run_id) ? old : [...old, failed]);
            setError(`החילוץ נכשל בשרת (${e.code}, HTTP ${e.status}). דוח הכשל זמין לייצוא. התור הושהה ללא ניסיון חוזר.`);
            break;
          }
          setError("העלאת קובץ נכשלה או שתוצאתה אינה ידועה. התור הושהה; אין ניסיון חוזר אוטומטי. קבצים שהושלמו נשמרו בנפרד."); break;
        }
      }
      if (records) void loadRecords(0, appliedFilters);
    }
    catch (e) { setError(e instanceof APIError ? `העלאת המסמך נכשלה (${e.code}). לא בוצע ניסיון חוזר אוטומטי.` : "העלאת המסמך נכשלה. לא בוצע ניסיון חוזר אוטומטי."); }
    finally { setBusy(false); }
  }
  async function selectForm(form: string) {
    if (!run) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const next = await getRun(run.run_id, form);
      if (next.run_id !== run.run_id || next.selected_form?.form_id !== form || next.status !== "succeeded") throw new Error("Form mismatch");
      setRun(next);
      setRefreshRequired(false);
      remember(next); setFocusedField(next.selected_form.fields.find(needsAttention)?.field_name ?? null);
      if (!Object.hasOwn(drafts, form) && next.selected_form) setDrafts(old => ({ ...old, [form]: initialDrafts(next.selected_form!) }));
      return next;
    } catch (e) { setError(e instanceof APIError ? `טעינת הטופס נכשלה (${e.code}).` : "טעינת הטופס נכשלה. אפשר לנסות שוב."); return false; }
    finally { setBusy(false); }
  }
  const form = run?.selected_form;
  const active = form ? drafts[form.form_id] : undefined;
  function edit(name: FieldName, change: Partial<Draft>) {
    if (!form || !active) return;
    setNotice("");
    setAttestation(false);
    setDrafts(old => ({ ...old, [form.form_id]: { ...active, [name]: { ...active[name], ...change } } }));
  }
  async function submit() {
    if (!run || !form || !active) return;
    // Keep this SAME payload/IDs/time across an ambiguous network failure.
    const command = pending ?? { form: form.form_id, command: makeCommand(run, draftChanges(active, saved[form.form_id]), operator) };
    setPending(command); setBusy(true); setError(""); setNotice("");
    try {
      const response = await postReview(run.run_id, command.form, command.command);
      const persisted = response.selected_form;
      if (response.run_id !== run.run_id || !persisted || persisted.form_id !== command.form) {
        throw new Error("Unexpected review response");
      }
      const confirmed = savedDrafts(persisted, active);
      setRun(response);
      setRefreshRequired(false);
      remember(response);
      setDrafts(old => ({ ...old, [persisted.form_id]: confirmed }));
      setSaved(old => ({ ...old, [persisted.form_id]: confirmed }));
      setPending(null);
      const count = command.command.actions.length;
      setNotice(`${count === 1 ? "נשמרה החלטת ביקורת אחת" : `נשמרו ${count} החלטות ביקורת`}. ${reviewLabels[persisted.review_status] ?? "מצב הביקורת עודכן"} · גרסה ${persisted.revision}.`);
      if (records) void loadRecords(records.offset, appliedFilters);
    } catch (e) {
      setError(e instanceof APIError && e.status < 500
        ? `השמירה נכשלה (${e.code}). ההחלטות, הערכים, פרטי הבודק והנימוקים נשמרו במסך. יש לתקן ולנסות שוב.`
        : "תוצאת השמירה אינה ידועה. ההחלטות, הערכים, פרטי הבודק והנימוקים נשמרו במסך. יש לשלוח שוב את אותה בקשה כדי לאמת את המצב השמור.");
      if (e instanceof APIError && e.status < 500) setPending(null);
    } finally { setBusy(false); }
  }
  async function download() {
    if (!run) return;
    if (unsavedChoices() &&
        !window.confirm("לייצא את המצב השמור בשרת? בחירות שטרם נשמרו לא ייכללו. שדות שלא אושרו יישארו לא מאושרים.")) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const response = await request(`/api/v1/demo-runs/${encodeURIComponent(run.run_id)}/export.xlsx`);
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a"); link.href = url; link.download = "reviewed-demo.xlsx";
      document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      setNotice(run.status === "failed" ? "דוח הכשל התקבל מהשרת והועבר להורדה בדפדפן." : "קובץ ה־Excel התקבל מהשרת והועבר להורדה בדפדפן.");
    } catch (e) { setError(e instanceof APIError ? `הורדת הקובץ נכשלה (${e.code}).` : "הורדת הקובץ נכשלה. לא התקבל קובץ מהשרת."); }
    finally { setBusy(false); }
  }
  async function downloadRows(onlySelected = false) {
    const chosen = onlySelected ? filteredRows.filter(row => selectedRows.has(row.key)) : filteredRows;
    const failures = includeFailures ? failedRuns : [];
    if ((!chosen.length && !failures.length) || busy || pending || pendingFinal) return;
    if (chosen.some(row => row.cells.commitment_numbers.draft)) {
      setError("יש לשמור או לבטל טיוטות של מספרי התחייבות לפני בחירת שורות לייצוא. לא ניתן לשייך בבטחה שורת טיוטה למספר שמור."); return;
    }
    if (unsavedChoices() && !window.confirm("לייצא רק את המצב השמור בשרת? טיוטות לא ייכללו וערכים שלא אושרו יישארו ריקים.")) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const blob = await exportRows(chosen, failures);
      const url = URL.createObjectURL(blob), link = document.createElement("a");
      link.href = url; link.download = "workbench-records.xlsx"; document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setNotice(`קובץ משותף התקבל מהשרת עבור ${chosen.length} שורות נבחרות מכל עמודי הסינון. לא בוצעו אישורים או תיקונים.`);
    } catch (e) {
      setError(e instanceof APIError && e.code === "STALE_EXPORT_REVISION" ? "הייצוא נחסם כי רשומה השתנתה בשרת. יש לרענן את הרשומות ולבחור מחדש."
        : "הייצוא המשותף נכשל. לא התקבל קובץ ולא שונו רשומות. יש לבדוק גם שגרסת השרת תומכת בייצוא משותף.");
    } finally { setBusy(false); }
  }
  async function finalize() {
    if (!approvalOpen || finalRequestActive.current || !run || !form || busy || pending || form.finalized || (!pendingFinal &&
        (!attestation || !operator.trim() || unsavedChoices(form.form_id) || refreshRequired ||
         !eligibilityKnown(form) || finalizationBlockers(form).length > 0))) return;
    const submitted = pendingFinal ?? { form: form.form_id, command: {
      review_token: run.review_token, expected_revision: form.revision, decision_id: crypto.randomUUID(),
      operator_declaration: operator, declared_at: new Date().toISOString(), attest_all_values: true as const,
    } };
    finalRequestActive.current = true;
    setPendingFinal(submitted); setBusy(true); setError(""); setNotice("");
    try {
      const response = await postFinalization(run.run_id, submitted.form, submitted.command);
      const persisted = response.selected_form;
      if (response.run_id !== run.run_id || !persisted || persisted.form_id !== submitted.form || persisted.finalized !== true) {
        throw new Error("Unexpected finalization response");
      }
      setRun(response); remember(response);
      setDrafts(old => ({ ...old, [persisted.form_id]: initialDrafts(persisted) }));
      setSaved(old => { const next = { ...old }; delete next[persisted.form_id]; return next; });
      setPendingFinal(null); setAttestation(false);
      setApprovalOpen(false);
      setNotice(`הטופס אושר סופית בשרת · גרסה ${persisted.revision}. נשמר אירוע הצהרה אנושית אחד לכל הטופס.`);
      try {
        const refreshed = await getRun(run.run_id, submitted.form);
        if (refreshed.run_id !== run.run_id || refreshed.selected_form?.form_id !== submitted.form ||
            refreshed.selected_form.revision < persisted.revision) throw new Error("Unexpected refresh");
        setRun(refreshed); remember(refreshed);
      } catch {
        setRefreshRequired(true);
        setError("האישור נשמר בשרת, אך הרענון הנוסף נכשל. מוצגת תשובת השמירה; יש לרענן לפני פעולה נוספת.");
      }
      if (records) void loadRecords(records.offset, appliedFilters);
    } catch (e) {
      if (e instanceof APIError && e.status < 500) {
        setPendingFinal(null);
        setRefreshRequired(true); setAttestation(false);
        setError(e.code === "FORM_HAS_UNRESOLVED_FIELDS"
          ? "האישור נחסם: יש לפתור ולשמור את השדות המסומנים לפני אישור סופי. הערכים והבחירות נשמרו במסך."
          : e.status === 409
            ? "גרסת הטופס השתנתה או שהוא כבר אושר. יש לפתוח מחדש את הרשומה מהמאגר ולבדוק את המצב העדכני. הטיוטות נשמרו במסך."
            : `האישור הסופי נכשל (${e.code}). הערכים והבחירות נשמרו במסך.`);
      } else if (e instanceof APIError && e.code === "FORM_APPROVAL_MIGRATION_REQUIRED") {
        setPendingFinal(null);
        setError("האישור הסופי אינו זמין: נדרשת התאמת מסד הנתונים בשרת. לא נשמר אישור.");
      } else {
        setError("תוצאת האישור הסופי אינה ידועה. שלחו שוב את אותה בקשה לאימות; אין אישור הצלחה ואין בקשת חילוץ חדשה.");
      }
    } finally { finalRequestActive.current = false; setBusy(false); }
  }
  async function refreshForm() {
    if (!run || !form || busy || pending || pendingFinal) return;
    const hasDrafts = unsavedChoices(form.form_id);
    setBusy(true); setError(""); setNotice(""); setAttestation(false);
    try {
      const next = await getRun(run.run_id, form.form_id);
      if (next.run_id !== run.run_id || next.selected_form?.form_id !== form.form_id || next.status !== "succeeded") throw new Error("Form mismatch");
      setRun(next); remember(next); setRefreshRequired(false);
      if (!hasDrafts) {
        setDrafts(old => ({ ...old, [form.form_id]: initialDrafts(next.selected_form!) }));
        setSaved(old => { const copy = { ...old }; delete copy[form.form_id]; return copy; });
      }
      setNotice(hasDrafts ? "מצב השרת רוענן. הטיוטות נשמרו במסך ואינן כלולות באישור הסופי."
        : `מצב השרת רוענן · גרסה ${next.selected_form.revision}. לא בוצע חילוץ חדש.`);
      return next;
    } catch {
      setRefreshRequired(true);
      setError("רענון מצב השרת נכשל. הטיוטות נשמרו; יש לרענן בהצלחה לפני אישור סופי.");
    } finally { setBusy(false); }
  }
  function focusReviewField(name: FieldName) {
    setFocusedField(name);
    setTimeout(() => document.getElementById?.(`review-field-${name}`)?.focus(), 0);
  }
  const blockers = form ? finalizationBlockers(form) : [];
  const dirtyFields = form ? unsavedFields(form.form_id) : [];
  const knownEligibility = form ? eligibilityKnown(form) : false;
  const relatedCommitments = rowsForFormCount();
  function rowsForFormCount() {
    if (!run || !form) return 0;
    return commitmentRows([{ run_id: run.run_id, form }]).filter(row => row.commitment !== null).length;
  }
  const choices = active && form ? Object.values(draftChanges(active, saved[form.form_id])).filter(d => d.action) : [];
  const reviewReady = choices.length > 0 && operator.trim() && choices.every(d => d.action === "correct_and_approve" || d.reason.trim());
  const reviewedCount = form?.fields.filter(field => approvedStates.includes(field.review_status)).length ?? 0;
  const rows = commitmentRows(workForms, run?.run_id, drafts, saved);
  const queue = exceptionQueue(rows);
  const filteredRows = rows.filter(row => matchesWorkFilter(row, workFilter));
  const page = Math.min(workPage, Math.max(0, Math.ceil(filteredRows.length / 20) - 1));
  const visibleRows = filteredRows.slice(page * 20, (page + 1) * 20);
  const reviewFields = form?.fields.filter(field => !blockersOnly || needsAttention(field) || dirtyFields.includes(field.field_name)) ?? [];
  const editorField = reviewFields.find(field => field.field_name === focusedField);
  function rowActionLabel(row: CommitmentRow) {
    const entry = workForms.find(entry => entry.run_id === row.run_id && entry.form.form_id === row.form_id);
    const dirty = row.run_id === run?.run_id && unsavedChoices(row.form_id);
    if (row.finalized && !dirty) return "אושר סופית";
    return !entry || dirty || !eligibilityKnown(entry.form) || finalizationBlockers(entry.form).length ||
      (refreshRequired && row.run_id === run?.run_id && row.form_id === form?.form_id) ? "בדיקת חריגות" : "אישור סופי";
  }
  async function openRowAction(row: CommitmentRow, trigger?: HTMLElement) {
    if (busy || pending || (pendingFinal && (run?.run_id !== row.run_id || pendingFinal.form !== row.form_id))) return;
    approvalTrigger.current = trigger ?? null;
    if (pendingFinal) { setApprovalOpen(true); return; }
    const sameRun = run?.run_id === row.run_id;
    const next = sameRun && form?.form_id === row.form_id ? await refreshForm()
      : sameRun ? await selectForm(row.form_id) : await reopen(row);
    if (!next || !next.selected_form) return;
    const selected = next.selected_form;
    const dirty = sameRun && unsavedChoices(row.form_id);
    setAttestation(false);
    if (!dirty && selected.finalized) { setNotice("הטופס כבר אושר סופית בשרת."); return; }
    if (!dirty && eligibilityKnown(selected) && !finalizationBlockers(selected).length) {
      setApprovalOpen(true);
    } else {
      setBlockersOnly(true);
      setFocusedField(selected.fields.find(needsAttention)?.field_name ?? dirtyFields[0] ?? null);
      setTimeout(() => document.getElementById?.("focused-review-title")?.focus(), 0);
    }
  }
  async function openCell(row: Pick<CommitmentRow, "run_id" | "form_id">, field: FieldName) {
    if (busy || pending || pendingFinal) return;
    const focus = () => { setBlockersOnly(false); setFocusedField(field); setTimeout(() => document.getElementById?.("focused-review-title")?.focus(), 0); };
    if (run?.run_id === row.run_id && form?.form_id === row.form_id) { focus(); return; }
    const opened = run?.run_id === row.run_id ? await selectForm(row.form_id) : await reopen(row);
    if (opened) focus();
  }
  const previewUrl = savedPreview?.url ?? preview;
  const previewMime = savedPreview?.mime ?? file?.type;
  const mockProvider = run?.provider?.toLowerCase().includes("mock");
  function chooseFile(files: File[]) {
    if (busy || pending || pendingFinal) return;
    if (files.length > 30) { setError("ניתן לבחור עד 30 קבצים בתור אחד."); return; }
    const next = files[0];
    if (!next) return;
    if (files.some(item => !["application/pdf", "image/png", "image/jpeg"].includes(item.type) &&
        !(item.type === "" && /\.(pdf|png|jpe?g)$/i.test(item.name)))) {
      setError("סוג הקובץ אינו נתמך. יש לבחור PDF או תמונה מסוג PNG או JPEG."); return;
    }
    if (unsavedChoices() && !window.confirm("בחירת מסמך חדש תבטל בחירות שטרם נשמרו. להמשיך?")) return;
    setFile(next); setSavedPreview(null); setRun(null); setDrafts({}); setSaved({});
    setUploadQueue(files.map(file => ({ id: crypto.randomUUID(), file, status: "queued" })));
    setNotice(""); setError(""); setConsent(false); setView("workspace");
  }
  return <div className="app-shell" dir="rtl">
    <a className="skip-link" href="#main-content">דילוג לתוכן המרכזי</a>
    <aside className="sidebar" aria-label="סרגל ניווט">
      <a className="brand" href="#main-content" aria-label="מחויבים ל־100 — מסך ראשי">
        <img className="brand-mark" src="/brand.svg" width="48" height="48" alt="" aria-hidden="true" />
        <strong className="brand-wordmark"><span>מחויבים </span><span className="brand-number">ל־<bdi dir="ltr">100</bdi></span></strong>
        <small>מטופסי התחייבות לנתונים מסודרים</small>
      </a>
      <div className="sidebar-label">סביבת העבודה</div>
      <nav aria-label="ניווט ראשי">
        <button className={`nav-item ${view === "workspace" ? "is-active" : ""}`} aria-label="עיבוד וביקורת" aria-current={view === "workspace" ? "page" : undefined} onClick={() => setView("workspace")}>
          <Icon name="file" /><span>עיבוד וביקורת</span>
        </button>
        <button className={`nav-item ${view === "records" ? "is-active" : ""}`} aria-label="רשומות שמורות" aria-current={view === "records" ? "page" : undefined} onClick={() => setView("records")}>
          <Icon name="records" /><span>רשומות שמורות</span>{records && <span className="nav-count">{records.total}</span>}
        </button>
      </nav>
      <div className="sidebar-guide">
        <span className="sidebar-label">מהמסמך לרשומה</span>
        <ol><li><span>01</span> בחירת מסמך וחילוץ</li><li><span>02</span> בדיקה ואישור אנושי</li><li><span>03</span> שמירה וייצוא</li></ol>
      </div>
      <div className="sidebar-footer"><Icon name="shield" /><p>מתמקדים במה שדורש טיפול.<small>חילוץ אוטומטי אינו אישור אנושי.</small></p></div>
    </aside>

    <div className="main-shell">
      <header className="topbar"><span><strong>מחויבים ל־<bdi dir="ltr">100</bdi></strong> <span className="breadcrumb-separator">/</span> <strong>{view === "workspace" ? "שולחן ההתחייבויות" : "רשומות שמורות"}</strong></span>
        <span className="environment-badge"><span className="status-dot" />סביבת הדגמה מקומית</span>
      </header>
      <main id="main-content" tabIndex={-1}>
        <div className="page-heading"><div><p className="eyebrow">ניהול מסמכים · בקרת נתונים</p><h1>{view === "workspace" ? "כל ההתחייבויות. מבט אחד." : "רשומות שמורות"}</h1>
          <p>{view === "workspace" ? "תצוגה מרוכזת, טיפול בחריגים ומקור זמין לכל טופס." : "חיפוש, פתיחה והמשך ביקורת. ללא חילוץ מחדש."}</p></div>
          <div className="heading-note"><Icon name={view === "workspace" ? "shield" : "records"} /><span>{view === "workspace" ? "האישור נשאר בידיים שלכם" : "כל טופס, וההיסטוריה שלו"}</span></div>
        </div>

        {error && <div role="alert" className="message message-error"><Icon name="alert" /><p dir="auto">{error}</p></div>}
        {notice && <div role="status" className="message message-success"><Icon name="check" /><p>{notice}</p></div>}
        {busy && <div role="status" className="message message-progress"><span className="spinner" /><p>הבקשה בטיפול. זמן העיבוד תלוי בפעולה ובספק. אין צורך לשלוח שוב.</p></div>}

        <section aria-label="רשומות שמורות" className="panel saved-records" hidden={view !== "records"}>
          <div className="panel-heading"><div><h2>מאגר הרשומות</h2><p>הערכים ברשומות ממתינות הם הצעות שטרם הושלמה בדיקתן. דגלי שדות נטענים בפתיחה או בצירוף לשולחן.</p></div><button className="button button-secondary" onClick={loadRecordsIntoWorkspace} disabled={busy || !!pending || !!pendingFinal || recordsBusy || !records?.items.length}>צירוף העמוד לשולחן העבודה</button></div>
          <div className="record-filters" onKeyDown={event => { if (event.key === "Enter" && !recordsBusy) { event.preventDefault(); void loadRecords(0); } }}>
            <label className="search-label">חיפוש במאגר<div className="search-input"><Icon name="search" /><input aria-label="חיפוש רשומות" placeholder="שם, תעודת זהות, מספר או שולח…" value={filters.q} maxLength={200} disabled={recordsBusy}
              onChange={e => setFilters(old => ({ ...old, q: e.target.value }))} /></div></label>
            <label>מצב בדיקה<select aria-label="סינון מצב בדיקה" value={filters.status} disabled={recordsBusy} onChange={e => setFilters(old => ({ ...old, status: e.target.value as RecordsFilters["status"] }))}>
              <option value="">כל המצבים</option><option value="pending">ממתינה לבדיקה</option><option value="completed">הבדיקה הושלמה</option>
            </select></label>
            <label>גורם שולח<input aria-label="סינון שולח" placeholder="התאמה מלאה" value={filters.sender} maxLength={200} disabled={recordsBusy} onChange={e => setFilters(old => ({ ...old, sender: e.target.value }))} /></label>
            <label>תאריך קבלת דגימה<input type="date" dir="ltr" aria-label="סינון תאריך קבלת דגימה" value={filters.receipt_date} disabled={recordsBusy} onChange={e => setFilters(old => ({ ...old, receipt_date: e.target.value }))} /></label>
            <button className="button button-primary filter-button" onClick={() => loadRecords(0)} disabled={recordsBusy}><Icon name="search" />חיפוש / רענון</button>
          </div>
          {recordsBusy && <p className="loading-line" role="status"><span className="spinner" />טוען רשומות…</p>}
          {recordsError && <div className="message message-error" role="alert"><Icon name="alert" /><p>{recordsError}</p></div>}
          {records && <>
            <div className="table-summary"><p>{records.total} רשומות נמצאו</p><span>פתיחת רשומה אינה מפעילה בקשת חילוץ חדשה</span></div>
            {records.items.length === 0 ? <div className="empty-state"><span className="empty-icon"><Icon name="search" size={30} /></span><h3>אין רשומות להצגה</h3><p>אפשר לשנות את תנאי החיפוש או לבחור מסמך חדש לעיבוד.</p></div> :
            <div className="records-scroll" tabIndex={0} role="region" aria-label="טבלת רשומות — ניתן לגלול אופקית"><table>
              <caption>שורה לכל מספר התחייבות נבדל; שדות משותפים נשארים קשורים לאותו טופס. הדפדוף לפי טפסים, לא לפי שורות התחייבות.</caption>
              <thead><tr><th scope="col">שם המטופל/ת</th><th scope="col">תעודת זהות</th><th scope="col">מספרים מרכזיים</th><th scope="col">מספרי התחייבות</th><th scope="col">קבלת דגימה</th><th scope="col">גורם שולח</th><th scope="col">מצב בדיקה</th><th scope="col">מסמך וטופס</th><th scope="col">נוצרה</th><th scope="col">עודכנה</th><th scope="col">פעולה</th></tr></thead>
              <tbody>{records.items.flatMap(record => distinctCommitments(record.summary.commitment_numbers).map((commitment, index) => <tr key={JSON.stringify([record.run_id, record.form_id, index])}>
                <td className="patient-name"><bdi>{[...record.summary.first_name, ...record.summary.last_name].join(" ") || "לא צוין שם"}</bdi></td>
                <td className="numeric"><bdi dir="ltr">{record.summary.israeli_id.join(" · ") || "—"}</bdi></td>
                <td><div className="table-values">{record.summary.central_numbers.map((value, i) => <bdi dir="ltr" key={i}>{value}</bdi>)}</div></td>
                <td><bdi dir="ltr">{commitment ?? "—"}</bdi></td>
                <td className="numeric"><bdi dir="ltr">{record.summary.sample_receipt_date.join(" · ") || "—"}</bdi></td><td><bdi>{record.summary.sender.join(" · ") || "—"}</bdi></td>
                <td><span className={`badge ${record.review_status === "completed" ? "badge-success" : "badge-neutral"}`}>{record.finalized ? "הטופס אושר סופית" : record.review_status === "completed" ? "הבדיקה הושלמה" : "ממתינה לבדיקה"}</span></td>
                <td className="record-identity"><bdi dir="ltr" title={record.run_id}>{record.run_id}</bdi><bdi dir="ltr" title={record.form_id}>{record.form_id}</bdi></td>
                <td className="date-cell"><bdi dir="ltr">{new Date(record.created_at).toLocaleString("he-IL")}</bdi></td><td className="date-cell"><bdi dir="ltr">{new Date(record.updated_at).toLocaleString("he-IL")}</bdi></td>
                <td><button className="button button-table" aria-label={`פתיחת רשומה ${record.run_id} ${record.form_id}`} onClick={() => reopen(record)} disabled={busy || !!pending || !!pendingFinal || recordsBusy}>פתיחה לבדיקה<Icon name="arrow" size={16} /></button></td>
              </tr>))}</tbody>
            </table></div>}
            <div className="records-pagination"><span>{records.items.length > 0 ? `מציג ${records.offset + 1}–${records.offset + records.items.length} מתוך ${records.total}` : "אין תוצאות בעמוד זה"}</span><div>
              <button className="button button-secondary" onClick={() => loadRecords(Math.max(0, records.offset - records.limit), appliedFilters)} disabled={recordsBusy || records.offset === 0}>הקודם</button>
              <button className="button button-secondary" onClick={() => loadRecords(records.offset + records.limit, appliedFilters)} disabled={recordsBusy || records.offset + records.limit >= records.total}>הבא</button>
            </div></div>
          </>}
        </section>

        <div hidden={view !== "workspace"}>
          <section aria-label="העלאת מסמך" className="panel upload-panel">
            <div className="panel-heading"><div><h2>קליטת מסמכים</h2><p>עד 30 קבצים בתור דפדפן. כל קובץ נשלח בבקשה נפרדת, אחד בכל פעם.</p></div><button className="button button-secondary" aria-expanded={uploadExpanded} aria-controls="upload-controls" onClick={() => setUploadExpanded(old => !old)}>{uploadExpanded ? "סגירת אזור ההעלאה" : "מסמך חדש"}<Icon name="plus" size={17} /></button></div>
            <div id="upload-controls" className="upload-layout" hidden={!uploadExpanded}>
              <div className={`drop-zone ${dragging ? "is-dragging" : ""} ${file ? "has-file" : ""}`} onDragOver={e => { e.preventDefault(); if (!busy && !pending && !pendingFinal) setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={e => { e.preventDefault(); setDragging(false); chooseFile(Array.from(e.dataTransfer.files)); }}>
                <input id="source-file" className="visually-hidden file-input" aria-label="בחירת מסמך" type="file" multiple accept="application/pdf,image/png,image/jpeg" disabled={busy || !!pending || !!pendingFinal} onChange={e => { chooseFile(Array.from(e.target.files ?? [])); e.target.value = ""; }} />
                <label htmlFor="source-file" className="drop-label"><span className="upload-symbol"><Icon name={file ? "file" : "upload"} size={25} /></span>
                  <strong>{uploadQueue.length > 1 ? `${uploadQueue.length} קבצים נבחרו` : file ? <bdi>{file.name}</bdi> : "גררו לכאן מסמכים או בחרו קבצים"}</strong>
                  <span>{file ? `${(file.size / 1024).toLocaleString("he-IL", { maximumFractionDigits: 0 })} ק״ב · לחצו להחלפת המסמך` : "מסמך PDF או תמונה מסוג PNG / JPEG"}</span>
                  <span className="file-tags" aria-hidden="true"><b>PDF</b><b>PNG</b><b>JPEG</b></span>
                </label>
              </div>
              <div className="upload-actions"><span className="mini-heading"><Icon name="shield" size={18} />לפני שמתחילים</span>
                <p>החילוץ נעשה באמצעות ספק חיצוני. יש לשלוח רק מסמך שהתקבל אישור לעבדו.</p>
                <label className="consent-control"><input type="checkbox" aria-label="אישור עיבוד חיצוני לכל הקבצים" checked={consent} disabled={busy || !!pending || !!pendingFinal} onChange={e => setConsent(e.target.checked)} /><span>אני מאשר/ת במפורש להעביר את כל המסמכים שנבחרו ל־<bdi dir="ltr">Gemini</bdi>, בהתאם להרשאה לכל מסמך.</span></label>
                <button className="button button-primary upload-button" onClick={upload} disabled={!file || !consent || !uploadQueue.some(item => item.status === "queued") || busy || !!pending || !!pendingFinal}><Icon name="upload" size={18} />העלאה וחילוץ<Icon name="arrow" size={17} /></button>
                <small>ללא ניסיון חוזר אוטומטי. ביקורת וייצוא אינם מפעילים חילוץ נוסף.</small>
              </div>
            </div>
            {uploadQueue.length > 0 && <section className="upload-queue" aria-label="תור קבצים"><p aria-live="polite">הושלמו: {uploadQueue.filter(item => item.status === "succeeded").length} · נכשלו או לא אומתו: {uploadQueue.filter(item => item.status === "failed").length} · ממתינים: {uploadQueue.filter(item => item.status === "queued").length}</p>
              <p>זהו תור בדפדפן, ללא עיבוד רקע עמיד או המשך אוטומטי לאחר סגירתו. שמירה לאחר restart מחייבת את שרת PostgreSQL הקיים.</p>
              <ul>{uploadQueue.map(item => <li key={item.id} data-upload-status={item.status}><bdi>{item.file.name}</bdi><span>{{ queued: "ממתין", processing: "בעיבוד", succeeded: "הושלם", failed: "נכשל או לא אומת" }[item.status]}</span>{item.code && <bdi dir="ltr">{item.code}</bdi>}</li>)}</ul>
            </section>}
          </section>

          <section className="panel commitment-workbench" id="commitment-workbench" tabIndex={-1} aria-label="שולחן התחייבויות">
            <div className="panel-heading"><div><p className="eyebrow">שולחן העבודה הנוכחי</p><h2>התחייבויות לטיפול</h2><p>{workForms.length} טפסים נטענו · {rows.length} שורות התחייבות · {queue.length} שדות עם דגל לטיפול</p></div>
              <div className="workbench-tools"><label>תצוגה<select aria-label="סינון שולחן העבודה" value={workFilter} onChange={event => { setWorkFilter(event.target.value as WorkFilter); setWorkPage(0); }}>
                <option value="all">כל ההתחייבויות</option><option value="attention">דורש בדיקה</option><option value="unapproved">ללא אישור סופי</option><option value="approved">אישור אנושי הושלם</option>
              </select></label><button className="button button-secondary" disabled={busy || !!pending || !!pendingFinal || !queue.length} onClick={() => { const next = queue.find(item => item.run_id !== run?.run_id || item.form_id !== form?.form_id || item.field !== focusedField) ?? queue[0]; return openCell(next, next.field); }}>החריגה הבאה<Icon name="arrow" size={16} /></button></div>
            </div>
            <p className="workbench-explainer">רק דגלים שטרם טופלו מודגשים. ערך ללא דגל מדווח אינו אומדן ביטחון ואינו אישור אנושי. שדות משותפים חלים על כל התחייבויות הטופס; אין שיוך אוטומטי בין דגימות להתחייבויות.</p>
            <div className="bulk-export-actions"><button className="button button-primary" disabled={busy || !!pending || !!pendingFinal || (!filteredRows.length && !(includeFailures && failedRuns.length))} onClick={() => downloadRows()}>ייצוא כל השורות לאקסל</button>
              <button className="button button-secondary" disabled={busy || !!pending || !!pendingFinal || !filteredRows.some(row => selectedRows.has(row.key))} onClick={() => downloadRows(true)}>ייצוא שורות מסומנות</button>
              {failedRuns.length > 0 && <label><input type="checkbox" checked={includeFailures} onChange={e => setIncludeFailures(e.target.checked)} />לכלול {failedRuns.length} דוחות כשל נפרדים, ללא קשר לסינון השדות</label>}
              <p>הייצוא כולל את כל עמודי הסינון הנוכחי, גם רשומות לא פתורות. ערכים עסקיים שלא אושרו נשארים ריקים; עמודת export_selected_commitment מזהה את שורת המקור ואינה אישור. טיוטות אינן מיוצאות.</p>
            </div>
            {visibleRows.length ? <div className="commitment-scroll" role="region" aria-label="טבלת התחייבויות — ניתן לגלול" tabIndex={0}><table className="commitment-table"><caption>פעולות בתחילת כל שורה. אפשר לגלול אופקית לכל השדות. אישור חל על הטופס המשותף, לא על התחייבות יחידה.</caption><thead><tr><th scope="col" className="selection-column"><input type="checkbox" aria-label="בחירת כל השורות בסינון בכל העמודים" checked={filteredRows.length > 0 && filteredRows.every(row => selectedRows.has(row.key))} onChange={e => setSelectedRows(old => { const next = new Set(old); for (const row of filteredRows) { if (e.target.checked) next.add(row.key); else next.delete(row.key); } return next; })} /></th><th scope="col" className="actions-column">פעולות הטופס</th><th scope="col" className="identity-column">מסמך וטופס</th>{fields.map(name => <th scope="col" key={name}>{fieldLabels[name]}</th>)}<th scope="col">מצב הטופס</th></tr></thead><tbody>
              {visibleRows.map(row => <tr key={row.key} data-work-row data-run={row.run_id} data-form={row.form_id} data-commitment={row.commitment}>
                <td className="row-selection"><input type="checkbox" aria-label={`בחירת התחייבות ${row.commitment ?? "ללא מספר"} בטופס ${row.form_id} במסמך ${row.run_id}`} checked={selectedRows.has(row.key)} onChange={e => setSelectedRows(old => { const next = new Set(old); if (e.target.checked) next.add(row.key); else next.delete(row.key); return next; })} /></td>
                <td className="row-actions"><button className={`button ${rowActionLabel(row) === "אישור סופי" ? "button-primary" : "button-secondary"}`} data-row-action title="הפעולה חלה על כל ההתחייבויות באותו מסמך וטופס. נפתח אישור משותף אחד." disabled={busy || !!pending || rowActionLabel(row) === "אושר סופית" || (!!pendingFinal && (run?.run_id !== row.run_id || pendingFinal.form !== row.form_id))} onClick={event => openRowAction(row, event.currentTarget)}>{rowActionLabel(row)}</button><small>לכל התחייבויות הטופס</small></td>
                <td className="record-identity"><bdi dir="ltr" title={row.run_id}>{row.run_id}</bdi><bdi dir="ltr" title={row.form_id}>{row.form_id}</bdi><span className="visually-hidden">גרסה {row.revision}</span></td>
                {fields.map(name => { const cell = row.cells[name]; return <td key={name} data-column={name} data-attention={cell.attention || undefined} className={cell.attention ? "attention-cell" : ""}>
                  <button className="cell-editor" aria-label={`${fieldLabels[name]} — ${cell.draft ? "טיוטה שטרם נשמרה" : fieldState(cell.field)} — ${row.form_id} — ${row.commitment ?? "ללא מספר"}`} title={attentionReason(cell.field)} onClick={() => openCell(row, name)} disabled={busy || !!pending || !!pendingFinal}>
                    <span className="cell-values">{cell.values.length ? cell.values.map((value, i) => <bdi dir={numericFields.includes(name) ? "ltr" : "auto"} key={i}>{value || "ערך ריק"}</bdi>) : "—"}</span>
                    <small className={cell.draft ? "draft-caption" : humanApproved(cell.field) ? "approved-caption" : ""}>{cell.draft ? "טיוטה · טרם נשמרה" : cell.field.review_status === "accepted_by_form" ? fieldState(cell.field) : !cell.attention && !humanApproved(cell.field) ? "חולץ" : fieldState(cell.field)}</small>
                  </button></td>; })}
                <td><span className={`badge ${(row.finalized ?? row.review_status === "complete") && !Object.values(row.cells).some(cell => cell.draft) ? "badge-success" : "badge-neutral"}`}>{Object.values(row.cells).some(cell => cell.draft) ? "טיוטה · טרם נשמרה" : row.finalized ? "הטופס אושר סופית" : row.finalized === false ? "ממתין לאישור סופי" : reviewLabels[row.review_status] ?? "מצב לא מוכר"}</span></td>
              </tr>)}
            </tbody></table></div> : <div className="workbench-empty"><Icon name="records" size={24} /><p>{rows.length ? "אין שורות שתואמות לסינון. היעדר דגלים אינו משלים ביקורת בשרת." : "העלו מסמך יחיד או צרפו עמוד מרשומות שמורות. לא מוצגות רשומות לדוגמה."}</p></div>}
            <div className="records-pagination"><span>{filteredRows.length ? `שורות ${page * 20 + 1}–${Math.min((page + 1) * 20, filteredRows.length)} מתוך ${filteredRows.length}` : "אין שורות בתצוגה"} · השולחן הוא תצוגת עבודה לדפדפן זה</span><div>
              <button className="button button-secondary" onClick={() => setWorkPage(Math.max(0, page - 1))} disabled={page === 0}>עמוד קודם בטבלה</button><button className="button button-secondary" onClick={() => setWorkPage(page + 1)} disabled={(page + 1) * 20 >= filteredRows.length}>עמוד הבא בטבלה</button>
            </div></div>
          </section>

          {run && <section className={`panel run-panel ${run.failure ? "run-failed" : ""}`} aria-label="מצב עיבוד המסמך">
            <div className="run-header"><div className="run-summary"><span className={`run-icon ${run.status === "failed" ? "failure-icon" : ""}`}><Icon name={run.status === "failed" ? "alert" : "file"} /></span><div>
              <h2>{run.status === "failed" ? "החילוץ לא הושלם" : run.status === "processing" ? "המסמך בעיבוד" : "המסמך חולץ — ממשיכים לבדיקה"}</h2>
              <p>{savedPreview ? "רשומה שמורה" : "חילוץ חדש"}{mockProvider ? " · ספק מדומה · נתוני הדגמה" : ""} · <bdi dir="ltr">{run.provider ?? "ספק לא זמין"} / {run.model ?? "מודל לא ידוע"}</bdi></p>
            </div></div><button className="button button-secondary" onClick={download} disabled={busy || !!pending || !!pendingFinal || run.status === "processing"}><Icon name="download" size={18} />{run.status === "failed" ? "הורדת דוח כשל" : "ייצוא ל־Excel"}</button></div>
            {run.failure && <div role="alert" className="failure-detail"><h3>לא נוצרה רשומה עסקית מהחילוץ שנכשל</h3><p>אפשר להוריד דוח כשל נפרד. אין מעבר אוטומטי לתוצאה קודמת ואין ניסיון חוזר אוטומטי.</p><p>קוד השגיאה: <bdi dir="ltr" className="technical-code">{run.failure.code}</bdi></p></div>}
            {run.form_ids.length > 0 && <div className="form-toolbar"><label>טופס במסמך <select aria-label="בחירת טופס לוגי" value={form?.form_id ?? ""} disabled={busy || !!pending || !!pendingFinal} onChange={e => selectForm(e.target.value)}>
              {run.form_ids.map((id, index) => <option key={id} value={id}>טופס {index + 1} מתוך {run.form_ids.length}</option>)}
            </select></label><p>כל הטפסים במסמך נכללים בייצוא לפי המצב השמור בשרת.<br /><small>השולחן מציג גם ערכים שחולצו וטיוטות. שדות שלא אושרו יישארו ריקים בקובץ לפי מדיניות השרת.</small></p></div>}
          </section>}

          <div className={`workspace ${previewUrl ? "has-preview" : ""}`} id="review-workspace">
            {previewUrl && <aside className="panel source-panel" aria-label="מסמך המקור"><div className="panel-heading"><div><h2>המקור מול העיניים</h2><p>{savedPreview ? "תצוגה מהמאגר הפרטי, ללא חילוץ מחדש." : "תצוגה מקומית של המסמך שנבחר."}</p></div><Icon name="file" /></div>
              <div className="document-preview">{previewMime === "application/pdf" ? <iframe src={previewUrl} title="תצוגת מסמך המקור" referrerPolicy="no-referrer" />
                : ["image/png", "image/jpeg"].includes(previewMime ?? "") ? <img src={previewUrl} alt="מסמך המקור שנבחר לבדיקה" /> : <p>אין תצוגה מקדימה. יש לבדוק את המסמך המקומי.</p>}</div>
              {["application/pdf", "image/png", "image/jpeg"].includes(previewMime ?? "") && <a className="source-open" href={previewUrl} target="_blank" rel="noopener noreferrer">פתיחת המקור בחלון נפרד<Icon name="arrow" size={14} /></a>}
              {run && <details className="source-details"><summary>פרטי המסמך והמקור</summary><dl><dt>מזהה מסמך</dt><dd><bdi dir="ltr">{run.run_id}</bdi></dd><dt>מזהה טופס</dt><dd><bdi dir="ltr">{form?.form_id ?? "—"}</bdi></dd><dt>מקור לפי השרת</dt><dd><bdi dir="ltr">{run.data_origin}</bdi></dd></dl></details>}
            </aside>}
            {form && active && <section aria-label="ביקורת ממוקדת" className="review-panel">
              <section className="panel review-tools" aria-label="שמירת תיקונים ומצב שרת">
                <div className="review-tools-heading"><h3>תיקונים ובדיקת חריגות</h3><button className="button button-secondary" onClick={refreshForm} disabled={busy || !!pending || !!pendingFinal}>רענון מצב מהשרת</button></div>
                <label htmlFor="operator-declaration">שם הבודק/ת והצהרת הבדיקה<input id="operator-declaration" aria-required="true" placeholder="שם והצהרה על בדיקת המסמך" value={operator} disabled={busy || !!pending || !!pendingFinal} onChange={e => { setOperator(e.target.value); setAttestation(false); }} /></label>
                <div aria-live="polite" className="review-feedback">
                  {refreshRequired && <p className="source-warning">נדרש רענון מוצלח מהשרת לאחר שגיאה לפני אישור סופי.</p>}
                  {typeof form.finalized === "boolean" && !knownEligibility && <p className="source-warning">השרת לא החזיר מצב זכאות מלא. יש לרענן או לבדוק את גרסת השרת.</p>}
                  {blockers.length > 0 && <><p className="source-warning">שדות שחוסמים אישור לפי השרת:</p><ul className="finalization-blockers">{blockers.map(field => <li key={field.field_name}><button className="blocker-link" disabled={busy || !!pending || !!pendingFinal} onClick={() => focusReviewField(field.field_name)}>{fieldLabels[field.field_name]}</button><span>{attentionReason(field)}</span></li>)}</ul></>}
                  {dirtyFields.length > 0 && <p className="source-warning">טיוטות שטרם נשמרו בטופס זה: {dirtyFields.map(name => fieldLabels[name]).join(", ")}. יש לבחור החלטה ולשמור; הטיוטה אינה מחליפה את מצב השרת.</p>}
                  {!form.finalized && knownEligibility && !refreshRequired && blockers.length === 0 && dirtyFields.length === 0 && <p className="eligibility-ready">כל החריגים טופלו לפי השרת. להמשך בחרו אישור סופי בשורת התחייבות בטבלה.</p>}
                </div>
              </section>
              <div className="panel review-heading"><div className="panel-heading"><div><p className="eyebrow">טיפול בחריגים</p><h2 id="focused-review-title" tabIndex={-1}>ביקורת ממוקדת</h2></div><span className="review-counter" data-human-approved-count={reviewedCount}><strong>{reviewedCount}</strong><span>{typeof form.finalized === "boolean" ? "אישורי שדה נפרדים" : "אושרו אנושית"}</span></span></div>
                <p><span className={`badge ${(form.finalized ?? form.review_status === "complete") ? "badge-success" : "badge-neutral"}`}>{form.finalized ? "הטופס אושר סופית" : form.finalized === false ? "ממתין לאישור סופי" : reviewLabels[form.review_status] ?? "מצב ביקורת לא מוכר"}</span><span className="revision-label">גרסה {form.revision} · מצב שמור בשרת</span></p>
                <p className="review-guidance">{typeof form.finalized === "boolean"
                  ? "פותרים ושומרים רק את החריגים. לאחר בדיקת כלל הערכים מול המקור מאשרים את הטופס בפעולה אנושית אחת. אין קבלה אוטומטית ואין אישורי שדה סמויים."
                  : "אפשר לשמור החלטה לשדה יחיד. כיום השרת דורש החלטה לכל שבעת השדות להשלמת הטופס; טיפול בחריגים בלבד אינו משלים אותו אוטומטית."}</p>
                <label>בחירת שדה לביקורת<select aria-label="בחירת שדה לביקורת" value={editorField?.field_name ?? ""} disabled={busy} onChange={event => setFocusedField(event.target.value as FieldName || null)}>
                  <option value="">בחרו שדה לבדיקה לפי הצורך</option>{reviewFields.map(field => <option key={field.field_name} value={field.field_name}>{fieldLabels[field.field_name]} · {fieldState(field)}</option>)}
                </select></label>
                <div className="field-queue">{form.fields.filter(needsAttention).map(field => <button key={field.field_name} className="button button-subtle" disabled={busy || !!pending || !!pendingFinal} onClick={() => setFocusedField(field.field_name)}>לטיפול: {fieldLabels[field.field_name]}</button>)}</div>
                {!editorField && <p className="no-reported-flags">{form.fields.some(needsAttention) ? "בחרו שדה מסומן כדי להתחיל." : "אין דגלים מדווחים שלא טופלו בטופס זה. אין בכך אישור אנושי או הוכחה לתקינות; אפשר לפתוח כל תא לבדיקה."}</p>}
                <a className="source-open" href="#commitment-workbench">חזרה לטבלת ההתחייבויות<Icon name="arrow" size={14} /></a>
              </div>
              {(editorField ? [editorField] : []).map(field => {
                const index = fields.indexOf(field.field_name);
                const draft = active[field.field_name]; const multi = ["central_numbers", "commitment_numbers"].includes(field.field_name);
                const approved = approvedStates.includes(field.review_status);
                const attention = needsAttention(field);
                return <fieldset id={`review-field-${field.field_name}`} tabIndex={-1} data-field={field.field_name} key={field.field_name} className={`field-card ${attention ? "needs-attention" : ""}`} disabled={busy || !!pending || !!pendingFinal}>
                  <legend><span className="field-index">{String(index + 1).padStart(2, "0")}</span>{fieldLabels[field.field_name]}</legend>
                  <div className="field-status-line"><span className={`badge ${approved ? "badge-success" : attention ? "badge-warning" : "badge-neutral"}`}>{reviewLabels[field.review_status] ?? "מצב ביקורת לא מוכר"}</span><span className={attention ? "source-warning" : "source-status"}>{attention && <Icon name="alert" size={14} />}{valueLabels[field.provider_value_status] ?? "מצב מקור לא מוכר"}{attention && field.provider_value_status === "present" ? " · נדרשת בדיקת מקור" : ""}</span></div>
                  <p className="shared-field-note">ההחלטה חלה על השדה בטופס כולו ועל כל שורות ההתחייבות שלו.{multi ? " הרשימה נשמרת בשלמותה; אין יצירת שיוך בין מספרי דגימות להתחייבויות." : ""}</p>
                  {attention && <p className="source-warning">{attentionReason(field)}</p>}
                  {humanApproved(field) && !attention && <p className="resolved-warning">דרישת הבדיקה נפתרה בהחלטה אנושית שמורה. אי־ודאות המקור נשמרת בהיסטוריה ואינה חסם פעיל.</p>}
                  <div className="original-block"><span className="input-caption">הערכים שחולצו מהמקור</span>
                    <ul className="provider-values">{field.provider_values.map((value, i) => <li key={i}><bdi dir={numericFields.includes(field.field_name) ? "ltr" : "auto"}>{value.raw}</bdi>{value.normalized !== null && <small>ערך מנורמל: <bdi dir={numericFields.includes(field.field_name) ? "ltr" : "auto"}>{value.normalized}</bdi></small>}</li>)}</ul>
                    {!field.provider_values.length && <p className="missing-value">לא נמצא ערך. השדה יישאר ללא אישור עד להחלטה מפורשת.</p>}
                  </div>
                  <label>החלטת הביקורת<select aria-label={`החלטה עבור ${fieldLabels[field.field_name]}`} value={draft.action} onChange={e => edit(field.field_name, { action: e.target.value as Action | "" })}>
                    <option value="">ללא פעולה — המצב הקיים נשמר</option>{actionLabels.map(([value, label]) => <option key={value} value={value} disabled={value === "approve_as_extracted" && field.provider_value_status !== "present"}>{label}</option>)}
                  </select></label>
                  <div className="review-values"><span className="input-caption">ערכים לביקורת{multi ? " · כל מספר בשורה נפרדת" : ""}</span>
                    {draft.values.map((value, i) => <div className="value-row" key={i}><input aria-label={`${fieldLabels[field.field_name]} — ערך ${i + 1}`} data-value-field={field.field_name} data-value-index={i} dir={numericFields.includes(field.field_name) ? "ltr" : "auto"} value={value} onChange={e => edit(field.field_name, { values: draft.values.map((v, j) => j === i ? e.target.value : v) })} />
                      {(multi || draft.values.length > 1) && <button className="button button-subtle" type="button" aria-label={`הסרת ערך ${i + 1} מתוך ${fieldLabels[field.field_name]}`} onClick={() => edit(field.field_name, { values: draft.values.filter((_, j) => j !== i) })}>הסרה</button>}
                    </div>)}
                    {(multi || !draft.values.length) && <button className="button button-add" type="button" onClick={() => edit(field.field_name, { values: [...draft.values, ""] })}><Icon name="plus" size={15} />הוספת ערך</button>}
                  </div>
                  <label className="reason-label">נימוק להחלטה{draft.action === "correct_and_approve" ? " — אופציונלי לתיקון רגיל" : ""}<input data-review-reason aria-required={Boolean(draft.action && draft.action !== "correct_and_approve")} placeholder="מה נבדק מול המקור?" value={draft.reason} onChange={e => edit(field.field_name, { reason: e.target.value })} /></label>
                </fieldset>;
              })}
              <div className="panel review-submit"><h3>שמירת תיקון והחלטות שדה</h3>
                <p>אישור ערכי המקור שומר את הרשימה המקורית כולה. תיקון ואישור שומר את הערכים שהוזנו בדיוק. דחייה או השארה לבירור אינן אישור.</p>
                <div className="save-actions"><button className="button button-primary" onClick={submit} disabled={busy || !!pendingFinal || (!pending && !reviewReady)}><Icon name="check" size={18} />{pending ? "שליחה חוזרת של אותה בקשה" : "שמירת החלטות הביקורת"}</button><span>החלטות לשמירה: {choices.length}</span></div>
                <p className="save-footnote">הבחירות נשארות גלויות לאחר השמירה. רק החלטות ששונו נשלחות מחדש. הייצוא כולל את המצב השמור בלבד, גם אם נותרו שדות לא מאושרים.</p>
              </div>
            </section>}
          </div>
          {!run && !file && !savedPreview && <div className="workspace-placeholder"><Icon name="file" size={23} /><div><strong>כאן מתחילה הבדיקה שלכם</strong><p>לאחר החילוץ, המסמך והערכים יוצגו זה לצד זה לביקורת.</p></div><span>כל טופס נשאר מחובר למקור שלו</span></div>}
        </div>
        <footer className="page-footer"><span>מחויבים ל־<bdi dir="ltr">100</bdi> · המעבדה המרכזית לנגיפים</span><span>שמירה במאגר זמינה בהפעלת השרת עם PostgreSQL.</span></footer>
      </main>
      {approvalOpen && form && <dialog ref={approvalDialog} className="approval-dialog" aria-labelledby="approval-dialog-title" aria-describedby="approval-dialog-scope" aria-modal="true" onCancel={event => { if (busy) event.preventDefault(); else setApprovalOpen(false); }} onClose={() => setApprovalOpen(false)} onKeyDown={event => {
        if (event.key !== "Tab") return;
        const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled)"));
        const first = controls[0], last = controls.at(-1);
        if (!first || !last) { event.preventDefault(); return; }
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }}>
        <div className="dialog-heading"><h2 id="approval-dialog-title">אישור סופי של הטופס</h2><button className="button button-subtle" aria-label="סגירת חלון האישור" disabled={busy} onClick={() => setApprovalOpen(false)}>סגירה</button></div>
        <p id="approval-dialog-scope">אישור משותף לכל {relatedCommitments} מספרי ההתחייבות של טופס זה. זו פעולה אחת ברמת הטופס, לא אישור נפרד לכל שורה.</p>
        <p className="dialog-binding"><bdi dir="ltr">{run?.run_id}</bdi> · <bdi dir="ltr">{form.form_id}</bdi> · גרסה {form.revision}</p>
        {error && <p role="alert" className="source-warning">{error}</p>}
        {pendingFinal && <p>תוצאת הבקשה הקודמת אינה ידועה. שליחה חוזרת משתמשת באותה בקשה בלבד.</p>}
        <label htmlFor="final-reviewer">שם הבודק/ת והצהרת הבדיקה<input id="final-reviewer" autoFocus value={operator} disabled={busy || !!pendingFinal} onChange={event => { setOperator(event.target.value); setAttestation(false); }} /></label>
        <small>הצהרה עצמית לצורך תיעוד; אינה מהווה אימות זהות.</small>
        <label className="form-attestation"><input type="checkbox" aria-label="הצהרת אישור לכל ערכי הטופס" checked={attestation} disabled={busy || !!pendingFinal} onChange={event => setAttestation(event.target.checked)} /><span>בדקתי מול המקור ואני מאשר/ת את כלל ערכי הטופס המוצגים.</span></label>
        <button className="button final-approve-button" onClick={finalize} disabled={busy || !!pending || form.finalized || (!pendingFinal && (!attestation || !operator.trim() || dirtyFields.length > 0 || refreshRequired || !knownEligibility || blockers.length > 0))}>{busy ? "האישור נשלח…" : pendingFinal ? "שליחה חוזרת של אותו אישור סופי" : "אישור סופי לטופס"}</button>
      </dialog>}
    </div>
  </div>;
}
