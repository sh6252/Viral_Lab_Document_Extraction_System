import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { APIError, request, setCSRFToken } from "./api.ts";

async function authenticate(path: string, password?: string) {
  const result = await (await request(path, password === undefined ? {} : { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password }) })).json();
  if (result.authenticated !== true || typeof result.csrf_token !== "string" || !result.csrf_token) {
    throw new Error("INVALID_SESSION_RESPONSE");
  }
  setCSRFToken(result.csrf_token);
}

export default function AuthGate({ children }: { children: ReactNode }) {
  const [authenticated, setAuthenticated] = useState(false);
  const [mounted, setMounted] = useState(false);
  const [busy, setBusy] = useState(true);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    void authenticate("/api/v1/auth/session").then(() => {
      if (active) { setAuthenticated(true); setMounted(true); }
    }).catch(() => {}).finally(() => { if (active) setBusy(false); });
    const expired = () => { setAuthenticated(false); setCSRFToken(""); setError("ההתחברות הסתיימה. התחברו שוב כדי להמשיך; הטיוטות נשמרו במסך."); };
    window.addEventListener("demo-session-expired", expired);
    return () => { active = false; window.removeEventListener("demo-session-expired", expired); };
  }, []);
  async function login(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      await authenticate("/api/v1/auth/login", password);
      setAuthenticated(true); setMounted(true); setPassword("");
    } catch (failure) {
      setError(failure instanceof APIError && failure.status === 429 ? "יותר מדי ניסיונות. המתינו חמש דקות ונסו שוב."
        : failure instanceof APIError && failure.status === 401 ? "פרטי ההזמנה אינם תקינים."
        : "ההתחברות לא הושלמה. ייתכן שהשרת מתעורר; נסו שוב בעוד כדקה.");
    } finally { setBusy(false); }
  }
  async function logout() {
    if (!window.confirm("להתנתק ולסגור את הטיוטות שלא נשמרו?")) return;
    try { await request("/api/v1/auth/logout", { method: "POST" }); window.location.reload(); }
    catch { setError("ההתנתקות לא אושרה בשרת. נסו שוב."); }
  }
  return <>
    <aside className="demo-access" dir="rtl">
      <span>הדגמה למוזמנים · מסמכים סינתטיים בלבד</span>
      {authenticated && <button onClick={() => void logout()}>התנתקות</button>}
      {authenticated && error && <span role="alert">{error}</span>}
    </aside>
    {mounted && <div hidden={!authenticated}>{children}</div>}
    {!authenticated && <main className="demo-login" dir="rtl">
      <h1>מחויבים ל־100</h1><h2>כניסת שופטים ומוזמנים</h2>
      <p>הזינו את הסיסמה שקיבלתם ממארגן ההדגמה. אין להעלות מסמכי מטופלים אמיתיים או מידע רפואי אישי. חילוץ באמצעות Gemini מחייב הסכמה מפורשת.</p>
      <form onSubmit={event => void login(event)}>
        <label htmlFor="demo-password">סיסמת הזמנה</label>
        <input id="demo-password" type="password" autoComplete="current-password" maxLength={256}
          required value={password} onChange={event => setPassword(event.target.value)} />
        <button type="submit" disabled={busy}>{busy ? "בודקים התחברות…" : "כניסה"}</button>
      </form>
      {error && <p role="alert">{error}</p>}
    </main>}
  </>;
}
