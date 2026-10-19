import { useEffect, useRef, useState } from "react";
import { APIError, draftChanges, getRecords, getRun, getSavedSource, initialDrafts, makeCommand, postReview, request, savedDrafts } from "./api.ts";
import type { Action, DemoRun, Draft, Drafts, FieldName, RecordsFilters, RecordsPage, ReviewCommand, SavedRecord } from "./api.ts";

const actionLabels: [Action, string][] = [
  ["approve_as_extracted", "Approve whole provider list"], ["correct_and_approve", "Correct and approve"],
  ["acknowledge_blank", "Acknowledge blank"], ["reject", "Reject — unresolved"],
  ["leave_unresolved", "Leave unresolved"],
];

export default function App() {
  const [file, setFile] = useState<File | null>(null);
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
  const [savedPreview, setSavedPreview] = useState<{ url: string; mime: string } | null>(null);
  const [filters, setFilters] = useState<RecordsFilters>({ q: "", status: "", sender: "", receipt_date: "" });
  const [appliedFilters, setAppliedFilters] = useState(filters);
  const [records, setRecords] = useState<RecordsPage | null>(null);
  const [recordsBusy, setRecordsBusy] = useState(false);
  const [recordsError, setRecordsError] = useState("");
  const recordsRequest = useRef(0);
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
  function unsavedChoices() {
    return Object.entries(drafts).some(([id, d]) => Object.values(draftChanges(d, saved[id])).some(f => f.action));
  }
  async function reopen(record: SavedRecord) {
    if (busy || pending) return;
    if (unsavedChoices() && !window.confirm("פתיחת רשומה אחרת תבטל בחירות שטרם נשמרו. להמשיך?")) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const next = await getRun(record.run_id, record.form_id);
      if (next.run_id !== record.run_id || next.selected_form?.form_id !== record.form_id) throw new Error("Record mismatch");
      const blob = await getSavedSource(record.run_id);
      const url = URL.createObjectURL(blob);
      setFile(null); setSavedPreview({ url, mime: blob.type }); setConsent(false);
      setRun(next); setDrafts({ [record.form_id]: initialDrafts(next.selected_form) });
      setSaved({}); setPending(null); setOperator("");
      setNotice("הרשומה והמסמך נטענו מהמאגר. לא בוצעה פנייה ל-Gemini.");
    } catch (e) {
      setError(e instanceof APIError ? `פתיחת הרשומה נכשלה (${e.code}). הבדיקה הנוכחית נשמרה במסך.`
        : "פתיחת הרשומה או התצוגה נכשלה. הבדיקה הנוכחית נשמרה במסך.");
    } finally { setBusy(false); }
  }
  function accept(value: DemoRun) {
    setRun(value);
    if (value.selected_form) {
      const form = value.selected_form;
      setDrafts(old => ({ ...old, [form.form_id]: initialDrafts(form) }));
    }
  }
  async function upload() {
    if (!file || !consent) return;
    setBusy(true); setError(""); setNotice(""); setRun(null); setDrafts({}); setSaved({}); setPending(null);
    const body = new FormData(); body.append("file", file); body.append("allow_external_processing", "true");
    try {
      accept(await (await request("/api/v1/demo-runs", { method: "POST", body })).json());
      if (records) void loadRecords(0, appliedFilters);
    }
    catch (e) { setError(e instanceof APIError ? e.code : "UPLOAD_FAILED — no automatic retry"); }
    finally { setBusy(false); }
  }
  async function selectForm(form: string) {
    if (!run) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const next = await getRun(run.run_id, form);
      setRun(next);
      if (!Object.hasOwn(drafts, form) && next.selected_form) setDrafts(old => ({ ...old, [form]: initialDrafts(next.selected_form!) }));
    } catch (e) { setError(e instanceof APIError ? e.code : "LOAD_FAILED"); }
    finally { setBusy(false); }
  }
  const form = run?.selected_form;
  const active = form ? drafts[form.form_id] : undefined;
  function edit(name: FieldName, change: Partial<Draft>) {
    if (!form || !active) return;
    setNotice("");
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
      setDrafts(old => ({ ...old, [persisted.form_id]: confirmed }));
      setSaved(old => ({ ...old, [persisted.form_id]: confirmed }));
      setPending(null);
      setNotice(`Saved ${command.command.actions.length} review actions for ${persisted.form_id}. Persisted review: ${persisted.review_status} (revision ${persisted.revision}).`);
      if (records) void loadRecords(records.offset, appliedFilters);
    } catch (e) {
      setError(e instanceof APIError && e.status < 500
        ? `Save failed (${e.code}). Your decisions, values, operator and reasons are preserved. Correct the issue and save again.`
        : "Save result unknown. Your decisions, values, operator and reasons are preserved. Retry SAME review request to confirm the saved state.");
      if (e instanceof APIError && e.status < 500) setPending(null);
    } finally { setBusy(false); }
  }
  async function download() {
    if (!run) return;
    if (unsavedChoices() &&
        !window.confirm("Download the SAVED server state? Unsaved choices are excluded; unresolved fields stay unresolved.")) return;
    setBusy(true); setError("");
    try {
      const response = await request(`/api/v1/demo-runs/${encodeURIComponent(run.run_id)}/export.xlsx`);
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a"); link.href = url; link.download = "reviewed-demo.xlsx";
      document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) { setError(e instanceof APIError ? e.code : "DOWNLOAD_FAILED"); }
    finally { setBusy(false); }
  }
  const choices = active && form ? Object.values(draftChanges(active, saved[form.form_id])).filter(d => d.action) : [];
  const reviewReady = choices.length > 0 && operator.trim() && choices.every(d => d.reason.trim());
  return <main>
    <header><h1>Commitment form review</h1><p>Local/private hackathon demo. Saved records require the durable PostgreSQL launcher.</p></header>
    <section aria-label="רשומות שמורות" dir="rtl" className="saved-records">
      <h2>רשומות שמורות</h2>
      <p>פתיחת רשומה קיימת אינה מפעילה חילוץ מחדש. ערכים ברשומות ממתינות הם הצעות שטרם הושלמה בדיקתן.</p>
      <div className="record-filters">
        <label>חיפוש לפי שם, תעודת זהות, מספר מרכזי, מספר התחייבות או שולח
          <input aria-label="חיפוש רשומות" value={filters.q} maxLength={200} disabled={recordsBusy}
            onChange={e => setFilters(old => ({ ...old, q: e.target.value }))} /></label>
        <label>מצב בדיקה<select aria-label="סינון מצב בדיקה" value={filters.status} disabled={recordsBusy}
          onChange={e => setFilters(old => ({ ...old, status: e.target.value as RecordsFilters["status"] }))}>
          <option value="">כל המצבים</option><option value="pending">ממתינה לבדיקה</option><option value="completed">הבדיקה הושלמה</option>
        </select></label>
        <label>גורם שולח — התאמה מלאה<input aria-label="סינון שולח" value={filters.sender} maxLength={200} disabled={recordsBusy}
          onChange={e => setFilters(old => ({ ...old, sender: e.target.value }))} /></label>
        <label>תאריך קבלת דגימה<input type="date" aria-label="סינון תאריך קבלת דגימה" value={filters.receipt_date} disabled={recordsBusy}
          onChange={e => setFilters(old => ({ ...old, receipt_date: e.target.value }))} /></label>
      </div>
      <button onClick={() => loadRecords(0)} disabled={recordsBusy}>חיפוש / רענון</button>
      {recordsBusy && <p role="status">טוען רשומות…</p>}
      {recordsError && <p role="alert">{recordsError}</p>}
      {records && <>
        <p>{records.total} רשומות נמצאו. {records.items.length > 0 ? `${records.offset + 1}–${records.offset + records.items.length}` : "אין תוצאות בעמוד זה"}</p>
        <div className="records-scroll"><table><caption>כל שורה היא טופס לוגי אחד; כמה טפסים יכולים להשתייך לאותו מסמך.</caption>
          <thead><tr><th>שם המטופל/ת</th><th>תעודת זהות</th><th>מספרים מרכזיים</th><th>מספרי התחייבות</th>
            <th>תאריך קבלת דגימה</th><th>גורם שולח</th><th>מצב בדיקה</th><th>טופס</th><th>נוצרה</th><th>עודכנה</th><th>פעולה</th></tr></thead>
          <tbody>{records.items.map(record => <tr key={`${record.run_id}:${record.form_id}`}>
            <td><bdi>{[...record.summary.first_name, ...record.summary.last_name].join(" ")}</bdi></td>
            <td><bdi dir="ltr">{record.summary.israeli_id.join(" · ")}</bdi></td>
            <td><bdi dir="ltr">{record.summary.central_numbers.join(" · ")}</bdi></td>
            <td><bdi dir="ltr">{record.summary.commitment_numbers.join(" · ")}</bdi></td>
            <td><bdi>{record.summary.sample_receipt_date.join(" · ")}</bdi></td><td><bdi>{record.summary.sender.join(" · ")}</bdi></td>
            <td>{record.review_status === "completed" ? "הבדיקה הושלמה" : "ממתינה לבדיקה"}</td><td><bdi>{record.form_id}</bdi></td>
            <td>{new Date(record.created_at).toLocaleString("he-IL")}</td><td>{new Date(record.updated_at).toLocaleString("he-IL")}</td>
            <td><button aria-label={`פתיחת רשומה ${record.run_id} ${record.form_id}`} onClick={() => reopen(record)} disabled={busy || !!pending || recordsBusy}>פתיחה לבדיקה</button></td>
          </tr>)}</tbody></table></div>
        <div className="records-pagination">
          <button onClick={() => loadRecords(Math.max(0, records.offset - records.limit), appliedFilters)} disabled={recordsBusy || records.offset === 0}>הקודם</button>
          <button onClick={() => loadRecords(records.offset + records.limit, appliedFilters)} disabled={recordsBusy || records.offset + records.limit >= records.total}>הבא</button>
        </div>
      </>}
    </section>
    <section aria-label="Upload">
      <h2>1. Choose a source</h2>
      <input aria-label="Document" type="file" accept="application/pdf,image/png,image/jpeg" disabled={busy || !!pending}
        onChange={e => { setFile(e.target.files?.[0] ?? null); setSavedPreview(null); setRun(null); setDrafts({}); setSaved({}); setNotice(""); setConsent(false); }} />
      <label><input type="checkbox" checked={consent} disabled={busy || !!pending}
        onChange={e => setConsent(e.target.checked)} /> I explicitly permit sending this approved demo document to Gemini.</label>
      <button onClick={upload} disabled={!file || !consent || busy || !!pending}>Upload / Extract</button>
      <p>No automatic retry or historical fallback. Gemini may time out or hit quota. Review and export make no further provider call.</p>
      <details><summary>Historical replay fallback</summary><p>Historical replay is CLI-only in this slice. Use the documented
        <code> backend.tools.review_export </code> command with an explicitly preserved snapshot and
        <code> data_origin="historical_replay"</code>. This screen never substitutes replay for live extraction.</p></details>
    </section>
    {busy && <p role="status">Working… Extraction can take about a minute. Please do not submit again.</p>}
    {error && <p role="alert" className="error">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {run && <section>
      <p className="badge">{savedPreview ? "SAVED" : "LIVE"} · {run.provider ?? "unavailable"} / {run.model ?? "unknown model"} · {run.status}</p>
      {run.failure && <div role="alert"><h2>Extraction failed</h2><p>{run.failure.code}: {run.failure.message}</p>
        <p>No commitment row exists. You may download a separate failure report. There is no automatic fallback.</p></div>}
      <button onClick={download} disabled={busy || !!pending || run.status === "processing"}>
        {run.status === "failed" ? "Download failure report" : "Download Excel"}</button>
      {run.form_ids.length > 0 && <label>Logical form ({run.form_ids.length} total — all are exported)
        <select value={form?.form_id ?? ""} disabled={busy || !!pending} onChange={e => selectForm(e.target.value)}>
          {run.form_ids.map(id => <option key={id} value={id}>{id}</option>)}
        </select></label>}
    </section>}
    <div className="workspace">
      {((file && preview) || savedPreview) && <aside><h2>{savedPreview ? "תצוגת המסמך השמור" : "Local source preview"}</h2>
        <p>{savedPreview ? "המסמך נטען מהמאגר הפרטי ללא פנייה לספק החילוץ." : "This browser object URL is not server artifact access."}</p>
        {(savedPreview?.mime ?? file?.type) === "application/pdf" ? <iframe src={savedPreview?.url ?? preview} title="Local PDF source" referrerPolicy="no-referrer" />
          : ["image/png", "image/jpeg"].includes(savedPreview?.mime ?? file?.type ?? "") ? <img src={savedPreview?.url ?? preview} alt="Local selected source" />
            : <p>Preview unavailable. Inspect the selected source locally.</p>}</aside>}
      {form && active && <section aria-label="Seven-field review">
        <h2>2. Inspect all seven fields</h2><p>Saved state: {form.review_status} · revision {form.revision}. Present does not mean approved.</p>
        <label>Operator declaration (not authenticated)<input value={operator} disabled={busy || !!pending}
          onChange={e => setOperator(e.target.value)} /></label>
        {form.fields.map(field => {
          const draft = active[field.field_name]; const multi = ["central_numbers", "commitment_numbers"].includes(field.field_name);
          return <fieldset key={field.field_name} disabled={busy || !!pending}>
            <legend>{field.field_name}</legend>
            <p>Provider: {field.provider_value_status} / {field.provider_review_status ?? "not supplied"} · business review: {field.review_status}</p>
            <ul className="provider-values">{field.provider_values.map((v, i) => <li key={i} dir="auto">{v.raw}
              {v.normalized !== null && <small>Normalized (separate): {v.normalized}</small>}</li>)}</ul>
            {!field.provider_values.length && <p>No provider value.</p>}
            <label>Explicit disposition<select value={draft.action} onChange={e => edit(field.field_name, { action: e.target.value as Action | "" })}>
              <option value="">No action — remain unchanged</option>
              {actionLabels.map(([value, label]) => <option key={value} value={value}
                disabled={value === "approve_as_extracted" && field.provider_value_status !== "present"}>{label}</option>)}
            </select></label>
            {draft.values.map((value, i) => <div className="value-row" key={i}>
              <input aria-label={`${field.field_name} value ${i + 1}`} dir="auto" value={value}
                onChange={e => edit(field.field_name, { values: draft.values.map((v, j) => j === i ? e.target.value : v) })} />
              {(multi || draft.values.length > 1) && <button type="button" onClick={() => edit(field.field_name, { values: draft.values.filter((_, j) => j !== i) })}>Remove</button>}
            </div>)}
            {(multi || !draft.values.length) && <button type="button" onClick={() => edit(field.field_name, { values: [...draft.values, ""] })}>Add value</button>}
            <label>Reason for this action<input value={draft.reason} onChange={e => edit(field.field_name, { reason: e.target.value })} /></label>
          </fieldset>;
        })}
        <p>Approve-as-extracted uses the entire original raw list. Corrections use exact input rows. Reject/unresolved never approve values.</p>
        <button onClick={submit} disabled={busy || (!pending && !reviewReady)}>{pending ? "Retry SAME review request" : "Save explicit review actions"}</button>
        <p>{choices.length} unsaved review actions. Saved selections remain visible; only changed decisions are submitted.</p>
        <p>Unresolved forms remain exportable. Unsaved choices are not exported. Source values remain unchanged.</p>
      </section>}
    </div>
  </main>;
}
