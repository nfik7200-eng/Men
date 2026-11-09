import { api, ApiError } from '../api/client';
import { h, status } from '../components/dom';
import type { AdminSettingsResponse, AdminSource } from '../api/types';

/**
 * Admin source settings, interactive. Lists every source with its enabled state and lets an admin switch one on or off.
 * Scheduled collection is shown as read-only: it is set on the server, never from this page. After every change the
 * page reloads the settings and shows the state the server stored, not the state the admin clicked.
 * Signed-out visitors are told to sign in; nothing here is a secret.
 */
export function mountAdminSettings(root: HTMLElement): void {
  document.querySelector<HTMLElement>('.server-sources')?.setAttribute('hidden', '');
  root.replaceChildren(status('Loading source settings…'));
  void load(root);
}

async function load(root: HTMLElement, notice?: { text: string; kind: 'info' | 'error' }): Promise<void> {
  try {
    render(root, await api.adminSettings(), notice);
  } catch (err) {
    const denied = err instanceof ApiError && (err.status === 401 || err.status === 403);
    const msg = denied ? 'Sign in to the admin area to manage sources.'
      : err instanceof ApiError ? err.message : 'Source settings could not be loaded.';
    root.replaceChildren(
      status(msg, 'error'),
      ...(denied ? [] : [h('button', { type: 'button', class: 'secondary', onclick: () => { root.replaceChildren(status('Loading source settings…')); void load(root); } }, 'Try again')]),
    );
  }
}

function render(root: HTMLElement, data: AdminSettingsResponse, notice?: { text: string; kind: 'info' | 'error' }): void {
  const message = notice ? status(notice.text, notice.kind) : '';
  const scheduler = h('p', { class: 'small', 'data-scheduler': '' },
    `Scheduled collection: ${data.scheduler.enabled ? 'enabled' : 'disabled'}; set on the server, not here.`);
  const table = h('div', { class: 'scroll', tabindex: '0' },
    h('table', {},
      h('thead', {}, h('tr', {},
        h('th', { scope: 'col' }, 'Source'), h('th', { scope: 'col' }, 'Kind'), h('th', { scope: 'col' }, 'State'),
        h('th', { scope: 'col' }, 'Configured'), h('th', { scope: 'col' }, 'Page budget'), h('th', { scope: 'col' }, 'Action'))),
      h('tbody', {}, ...data.sources.map((s) => row(root, s)))));
  root.replaceChildren(message, scheduler, table);
}

function row(root: HTMLElement, s: AdminSource): HTMLElement {
  const toggle = async () => {
    try {
      const stored = await api.setSourceEnabled(s.key, !s.enabled);
      await load(root, { text: `${s.name} ${stored.enabled ? 'enabled' : 'disabled'}.`, kind: 'info' });
    } catch (err) {
      await load(root, { text: err instanceof ApiError ? err.message : 'The source could not be changed.', kind: 'error' });
    }
  };
  return h('tr', { 'data-source': s.key },
    h('td', {}, s.name),
    h('td', {}, s.kind),
    h('td', {}, s.enabled ? 'Enabled' : 'Disabled'),
    h('td', {}, s.configured ? 'Configured' : 'Not configured'),
    h('td', {}, s.page_budget === null ? '—' : String(s.page_budget)),
    h('td', {}, h('button', { type: 'button', class: 'secondary', onclick: toggle }, s.enabled ? 'Disable' : 'Enable')));
}
