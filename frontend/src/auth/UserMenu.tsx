import { useEffect, useId, useRef, useState } from "react";
import { logout } from "./actions";
import { ChangePasswordDialog } from "./ChangePassword";
import { useAuth } from "./useAuth";

/** Header menu: username, role badge, Change password, Sign out. Renders nothing unless signed in. */
export function UserMenu() {
  const { mode, user } = useAuth();
  const [open, setOpen] = useState(false);
  const [dialog, setDialog] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        button.current?.focus();
      }
    };
    const onDown = (e: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, [open]);

  if (mode !== "required" || !user) return null;

  const signOut = async () => {
    setSigningOut(true);
    await logout();
  };

  return (
    <div className="user-menu" ref={wrap}>
      <button
        ref={button}
        type="button"
        className="btn user-menu-button"
        aria-expanded={open}
        aria-controls={menuId}
        aria-label={`Account menu, ${user.username}, ${user.role}`}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="user-name">{user.username}</span>
        <span className={`role-badge role-${user.role}`}>{user.role}</span>
      </button>
      {open ? (
        <div id={menuId} className="user-menu-panel" role="group" aria-label="Account">
          <div className="user-menu-who">
            Signed in as <strong>{user.username}</strong>
            <span className={`role-badge role-${user.role}`}>{user.role}</span>
          </div>
          <button
            type="button"
            className="btn"
            onClick={() => {
              setOpen(false);
              setDialog(true);
            }}
          >
            Change password
          </button>
          <button type="button" className="btn" onClick={() => void signOut()} disabled={signingOut}>
            {signingOut ? "Signing out…" : "Sign out"}
          </button>
        </div>
      ) : null}
      {dialog ? <ChangePasswordDialog onClose={() => { setDialog(false); button.current?.focus(); }} /> : null}
    </div>
  );
}
