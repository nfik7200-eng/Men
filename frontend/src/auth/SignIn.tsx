import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { ApiError } from "../api/client";
import { usePageTitle } from "../lib/usePageTitle";
import { bootstrapAdmin, login } from "./actions";
import { useAuth } from "./useAuth";

const BAD_CREDENTIALS =
  "Invalid username or password. Repeated failures lock the account for a while.";

export default function SignIn() {
  usePageTitle("Sign in");
  const { notice, setupRequired } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [wait, setWait] = useState(0); // seconds left before another attempt (throttled)
  const passwordRef = useRef<HTMLInputElement>(null);
  const usernameRef = useRef<HTMLInputElement>(null);
  const errorId = useId();

  useEffect(() => {
    usernameRef.current?.focus();
  }, []);

  // after a failed attempt (inputs are enabled again) put the cursor where the user must act
  useEffect(() => {
    if (!error || busy) return;
    (username.trim() ? passwordRef : usernameRef).current?.focus();
    // only when an attempt finishes or the message changes, not on every keystroke
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [error, busy]);

  useEffect(() => {
    if (wait <= 0) return;
    const timer = setTimeout(() => setWait((w) => w - 1), 1000);
    return () => clearTimeout(timer);
  }, [wait]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy || wait > 0) return;
    if (!username.trim() || !password) {
      setError("Enter your username and password.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await login(username, password);
      // signed in: the gate now renders the application in place of this form
    } catch (err) {
      setBusy(false);
      setPassword("");
      if (err instanceof ApiError && err.status === 429) {
        setWait(err.retryAfter ?? 30);
        setError(err.message);
      } else if (err instanceof ApiError && err.status === 401) {
        setError(BAD_CREDENTIALS);
      } else {
        setError(err instanceof ApiError ? err.message : "Could not sign in.");
      }
    }
  };

  const label = busy ? "Signing in…" : wait > 0 ? `Try again in ${wait} s` : "Sign in";
  return (
    <div className="auth-screen">
      <main className="auth-card" id="content">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <div>
            <strong>BIP</strong>
            <span>Blockchain intelligence</span>
          </div>
        </div>
        <h1>Sign in</h1>
        <p className="muted">Sign in to use the BIP workspace.</p>
        {notice ? (
          <div className="auth-notice" role="status">
            {notice}
          </div>
        ) : null}
        {setupRequired ? <FirstAdmin /> : null}
        <form className="form" onSubmit={submit} noValidate aria-label="Sign in">
          <label className="field">
            <span>Username</span>
            <input
              ref={usernameRef}
              name="username"
              autoComplete="username"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              maxLength={64}
              disabled={busy}
              aria-describedby={error ? errorId : undefined}
            />
          </label>
          <label className="field">
            <span>Password</span>
            <input
              ref={passwordRef}
              name="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              maxLength={256}
              disabled={busy}
              aria-invalid={error ? true : undefined}
              aria-describedby={error ? errorId : undefined}
            />
          </label>
          <div id={errorId} className="field-error" role="alert">
            {error}
          </div>
          <button type="submit" className="btn btn-primary" disabled={busy || wait > 0}>
            {label}
          </button>
        </form>
      </main>
    </div>
  );
}

/**
 * First run with sign-in required and no users: claim the administrator account with the one-time
 * code the server wrote to <data root>/config/first-admin-code (only readable on the server).
 */
function FirstAdmin() {
  const [code, setCode] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    if (!code.trim() || !username.trim() || !password) {
      setError("Enter the setup code, a username and a password.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await bootstrapAdmin(code, username, password);
    } catch (err) {
      setBusy(false);
      setError(err instanceof ApiError ? err.message : "Could not create the administrator.");
    }
  };

  return (
    <section className="auth-notice" aria-label="Create the first administrator">
      <p>
        <strong>No users exist yet.</strong> Create the first administrator with the one-time setup code the
        server wrote to <code>&lt;data root&gt;/config/first-admin-code</code> (its path is also in the server
        log). You can instead run <code>python -m bip.tools.users create &lt;name&gt; --role admin</code> on the
        server.
      </p>
      <form className="form" onSubmit={submit} noValidate aria-label="Create administrator">
        <label className="field">
          <span>Setup code</span>
          <input name="setup-code" autoComplete="off" spellCheck={false} value={code} onChange={(e) => setCode(e.target.value)} disabled={busy} />
        </label>
        <label className="field">
          <span>Administrator username</span>
          <input name="admin-username" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} disabled={busy} />
        </label>
        <label className="field">
          <span>Administrator password</span>
          <input name="admin-password" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} disabled={busy} />
        </label>
        {error ? (
          <p className="form-error" role="alert">
            {error}
          </p>
        ) : null}
        <button type="submit" className="btn" disabled={busy}>
          {busy ? "Creating…" : "Create administrator"}
        </button>
      </form>
    </section>
  );
}
