// The Alerts page and the navigation badge live in different parts of the tree. A window event
// tells the badge to refetch after alerts are marked read, without a shared store.
const EVENT = "bip:alerts-changed";

export function notifyAlertsChanged(): void {
  window.dispatchEvent(new Event(EVENT));
}

/** Subscribe to alert changes; returns the unsubscribe function. */
export function onAlertsChanged(listener: () => void): () => void {
  window.addEventListener(EVENT, listener);
  return () => window.removeEventListener(EVENT, listener);
}
