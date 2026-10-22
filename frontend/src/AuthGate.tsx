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

export default function AuthGate({ children }: { children: (controls: ReactNode) => ReactNode }) {
  const [authenticated, setAuthenticated] = useState(false);
  const [mounted, setMounted] = useState(false);
  const [busy, setBusy] = useState(true);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [showPassword, setShowPassword] = useState(false);
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
  const controls = <div className="session-controls" aria-label="ניהול התחברות">
    <span className="session-status" role="status"><span className="status-dot" />מחובר/ת</span>
    <button className="button button-secondary" onClick={() => void logout()}>התנתקות</button>
    {authenticated && error && <span role="alert">{error}</span>}
  </div>;
  return <>
    {mounted && <div hidden={!authenticated}>{children(controls)}</div>}
    {!authenticated && <main className="demo-login" dir="rtl">
      <section className="auth-card" aria-labelledby="auth-title">
        <img className="auth-logo" src="/brand.svg" width="72" height="72" alt="" aria-hidden="true" />
        <p className="eyebrow">מהמסמך לרשומה</p>
        <h1 id="auth-title">מחויבים ל־<bdi dir="ltr">100</bdi></h1>
        <p className="auth-intro">כל ההתחייבויות. מבט אחד.</p>
        <form onSubmit={event => void login(event)} aria-busy={busy}>
          <label htmlFor="demo-password">סיסמת כניסה</label>
          <div className="auth-password">
            <input id="demo-password" type={showPassword ? "text" : "password"} autoComplete="current-password" maxLength={256}
              required disabled={busy} value={password} aria-describedby={`auth-hint${error ? " auth-error" : ""}`}
              onChange={event => setPassword(event.target.value)} />
            <button type="button" className="password-toggle" aria-controls="demo-password" aria-pressed={showPassword}
              aria-label={showPassword ? "הסתרת סיסמה" : "הצגת סיסמה"} onClick={() => setShowPassword(value => !value)}>
              {showPassword ? "הסתרה" : "הצגה"}</button>
          </div>
          <small id="auth-hint">השתמשו בסיסמה שקיבלתם ממארגן ההדגמה.</small>
          <button className="button button-primary auth-submit" type="submit" disabled={busy}>
            {busy ? "בודקים התחברות…" : "כניסה לסביבת העבודה"}</button>
        </form>
        {error && <p id="auth-error" className="auth-error" role="alert">{error}</p>}
        <p className="auth-safety">למסמכים סינתטיים בלבד, ללא מידע רפואי אישי.</p>
      </section>
    </main>}
  </>;
}
