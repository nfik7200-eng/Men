// Tiny DOM helpers: build elements with text content (never innerHTML with data), so server or
// merchant strings cannot inject markup.

type Child = Node | string | null | undefined | false;
type Attr = string | number | boolean | null | undefined | (() => void);

export function h<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  attrs: Record<string, Attr> = {},
  ...children: Child[]
): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (typeof v === 'function') {
      // Event handlers are listeners, never markup: 'onclick' -> addEventListener('click', ...).
      el.addEventListener(k.replace(/^on/, ''), v);
    } else if (k === 'class') el.className = String(v);
    else if (k.startsWith('data-')) el.setAttribute(k, String(v));
    else el.setAttribute(k, v === true ? '' : String(v));
  }
  for (const c of children) {
    if (c === null || c === undefined || c === false) continue;
    el.append(typeof c === 'string' ? document.createTextNode(c) : c);
  }
  return el;
}

/** Status message for screen readers and sighted users alike. */
export function status(message: string, kind: 'info' | 'error' = 'info'): HTMLElement {
  return h('p', { class: `state state-${kind}`, role: kind === 'error' ? 'alert' : 'status' }, message);
}

export function clear(el: HTMLElement): void {
  while (el.firstChild) el.removeChild(el.firstChild);
}
