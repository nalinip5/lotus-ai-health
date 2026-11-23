import { useState } from "react";
import { api, ApiError, Me, setMe } from "../api";
import { Logo, SITE_URL } from "../brand";

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setMe(await api<Me>("/auth/login", { email, password }));
      if (!window.location.hash || window.location.hash === "#/") window.location.hash = "#/dashboard";
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sign-in failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-wrap">
      <form className="login" onSubmit={submit}>
        <div className="login-brand"><Logo /><div><b>Lotus AI Health</b><span>Lotus BPM Health · Staff portal</span></div></div>
        <h1>Sign in</h1>
        <p className="dim">Claims integrity, exceptions and appeal approvals are available to Lotus admins and staff only.</p>
        <label>Work email
          <input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label>Password
          <input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {error && <div className="login-error" role="alert">{error}</div>}
        <button className="primary wide" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
        <p className="note">No account? Ask a Lotus admin to add you. Patients: <a href={SITE_URL}>go to Lotus BPM Health</a>.</p>
      </form>
    </div>
  );
}

export function ChangePassword({ forced, onDone }: { forced?: boolean; onDone?: () => void }) {
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState<string | null>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (next !== again) return setError("The new passwords don't match");
    if (next.length < 10) return setError("Use at least 10 characters");
    try {
      setMe(await api<Me>("/auth/password", { current_password: cur, new_password: next }));
      onDone?.();
    } catch (err) { setError((err as Error).message); }
  };
  return (
    <div className="login-wrap">
      <form className="login" onSubmit={submit}>
        <h1>{forced ? "Set your password" : "Change password"}</h1>
        {forced && <p className="dim">You signed in with a temporary password. Choose your own to continue.</p>}
        <label>Current password<input type="password" autoComplete="current-password" required value={cur} onChange={(e) => setCur(e.target.value)} /></label>
        <label>New password (10+ characters)<input type="password" autoComplete="new-password" required value={next} onChange={(e) => setNext(e.target.value)} /></label>
        <label>Repeat new password<input type="password" autoComplete="new-password" required value={again} onChange={(e) => setAgain(e.target.value)} /></label>
        {error && <div className="login-error" role="alert">{error}</div>}
        <button className="primary wide">Save password</button>
        {!forced && <button type="button" className="wide" onClick={onDone}>Cancel</button>}
      </form>
    </div>
  );
}
