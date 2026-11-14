import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { ApiError } from "../api/client";
import { changePassword } from "./actions";

const MIN_LENGTH = 12;

/** Modal dialog for POST /auth/password. On success the server ends every session of the user. */
export function ChangePasswordDialog({ onClose }: { onClose: () => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const titleId = useId();
  const errorId = useId();
  const first = useRef<HTMLInputElement>(null);
  const root = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  useEffect(() => {
    closeRef.current = onClose;
  });

  useEffect(() => {
    first.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeRef.current();
      if (e.key !== "Tab" || !root.current) return;
      const items = [...root.current.querySelectorAll<HTMLElement>("input, button")].filter((el) => !el.hasAttribute("disabled"));
      const firstEl = items[0];
      const last = items[items.length - 1];
      if (!firstEl || !last) return;
      if (e.shiftKey && document.activeElement === firstEl) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        firstEl.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    if (!current || !next || !again) return setError("Fill in all three fields.");
    if (next.length < MIN_LENGTH) return setError(`The new password needs at least ${MIN_LENGTH} characters.`);
    if (next !== again) return setError("The new password and its repetition differ.");
    if (next === current) return setError("Choose a password different from the current one.");
    setBusy(true);
    setError(null);
    try {
      await changePassword(current, next);
      // the session ended: the gate shows the sign-in form with a notice and this dialog unmounts
    } catch (err) {
      setBusy(false);
      setError(err instanceof ApiError ? err.message : "Could not change the password.");
    }
  };

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={root}>
        <h2 id={titleId}>Change password</h2>
        <p className="muted">You will be signed out everywhere and must sign in again with the new password.</p>
        <form className="form" onSubmit={submit} noValidate aria-label="Change password">
          <label className="field">
            <span>Current password</span>
            <input ref={first} type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} maxLength={256} disabled={busy} />
          </label>
          <label className="field">
            <span>New password (at least {MIN_LENGTH} characters)</span>
            <input type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} maxLength={256} disabled={busy} />
          </label>
          <label className="field">
            <span>Repeat new password</span>
            <input type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} maxLength={256} disabled={busy} />
          </label>
          <div id={errorId} className="field-error" role="alert">
            {error}
          </div>
          <div className="modal-actions">
            <button type="button" className="btn" onClick={onClose} disabled={busy}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={busy}>
              {busy ? "Changing…" : "Change password"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
