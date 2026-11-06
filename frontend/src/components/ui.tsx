import type { ReactNode } from "react";
import type { ApiError } from "../api/client";
import { usePageTitle } from "../lib/usePageTitle";

export type Tone = "ok" | "warn" | "bad" | "idle" | "info";

export function Pill({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={`pill pill-${tone}`}>{children}</span>;
}

export function PageHeader({ title, lede, actions, docTitle }: {
  title: string;
  lede?: string;
  actions?: ReactNode;
  /** document.title override (defaults to the heading) */
  docTitle?: string;
}) {
  usePageTitle(docTitle ?? title);
  return (
    <header className="page-header">
      <div>
        <h1 tabIndex={-1}>{title}</h1>
        {lede ? <p className="lede">{lede}</p> : null}
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </header>
  );
}

export function Panel({ title, aside, children, className }: {
  title?: string;
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className ?? ""}`}>
      {title ? (
        <div className="panel-head">
          <h2>{title}</h2>
          {aside}
        </div>
      ) : null}
      {children}
    </section>
  );
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="stat">
      <span className="stat-value">{value}</span>
      <span className="stat-label">{label}</span>
      {hint ? <span className="stat-hint">{hint}</span> : null}
    </div>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="state state-loading" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      {label}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="state state-empty">
      <strong>{title}</strong>
      {children ? <div className="state-body">{children}</div> : null}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  return (
    <div className="state state-error" role="alert">
      <strong>{error.isConnectionError ? "Backend unreachable" : "Request failed"}</strong>
      <div className="state-body">
        {error.message}
        {error.isConnectionError ? (
          <div className="hint">
            Start it with <code>make api</code> (serves http://127.0.0.1:8000).
          </div>
        ) : null}
      </div>
      {onRetry ? (
        <button type="button" className="btn" onClick={onRetry}>
          Retry
        </button>
      ) : null}
    </div>
  );
}

export function Placeholder({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="placeholder">
      <span className="placeholder-tag">Not built yet</span>
      <strong>{title}</strong>
      <div className="state-body">{children}</div>
    </div>
  );
}

export function Mono({ children, title }: { children: ReactNode; title?: string }) {
  return (
    <code className="mono" title={title}>
      {children}
    </code>
  );
}

export function FieldError({ id, message }: { id: string; message: string | null }) {
  return message ? (
    <p className="field-error" id={id} role="alert">
      {message}
    </p>
  ) : null;
}
