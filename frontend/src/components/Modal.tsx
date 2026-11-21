import { useEffect, useId, useRef, type ReactNode } from "react";

/**
 * Modal dialog: focus moves in, Tab stays inside, Escape and a click on the backdrop close it
 * (unless `busy`), and focus returns to the element that opened it.
 */
export function Modal({ title, children, onClose, busy = false }: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  busy?: boolean;
}) {
  const titleId = useId();
  const root = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  const busyRef = useRef(busy);
  useEffect(() => {
    closeRef.current = onClose;
    busyRef.current = busy;
  });

  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focusable = () =>
      [...(root.current?.querySelectorAll<HTMLElement>("input, select, textarea, button, a[href]") ?? [])].filter(
        (el) => !el.hasAttribute("disabled"),
      );
    // the first field, else the dialog's own heading region
    (root.current?.querySelector<HTMLElement>("input, select, textarea") ?? focusable()[0])?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busyRef.current) closeRef.current();
      if (e.key !== "Tab") return;
      const items = focusable();
      const first = items[0];
      const last = items[items.length - 1];
      if (!first || !last) return;
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      opener?.focus();
    };
  }, []);

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}>
      <div className="modal modal-wide" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={root}>
        <h2 id={titleId}>{title}</h2>
        {children}
      </div>
    </div>
  );
}
