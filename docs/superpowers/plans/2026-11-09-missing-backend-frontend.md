# Missing Backend and Frontend Features Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the locally achievable gaps the repository records: an admin settings island, bounded JSON request bodies, an alert count that stays honest above 100, and the admin page wiring and browser checks for them.

**Architecture:** Server-rendered pages stay complete without JavaScript; the new admin island (like the match-review island) replaces the server table only once its script runs. Every write goes through the existing admin guard and Origin check. Backend changes are small and sit beside the routes that already own them.

**Tech Stack:** FastAPI + Jinja2 + SQLAlchemy (Python 3.13, pytest); TypeScript strict + Vite + Vitest/jsdom (frontend); Playwright journeys (`e2e/journeys.js`), `e2e/axe-check.js`, `e2e/reflow-check.js`.

**Spec:** No separate spec document exists for these items. The requirements come from `CLAUDE.md` (Rules that must not be broken), the "Not built yet" table in `frontend/README.md`, `docs/NOTIFICATIONS.md` (Limitations), and `docs/SECURITY_REVIEW.md`. Executors read those with this plan.

## Global Constraints

- Admin is fail-closed: `WATCHTOWER_ADMIN_TOKEN` must be at least 20 characters; no route may bypass `require_admin`.
- State-changing requests keep the Origin check (`security.same_origin`); the admin cookie stays `SameSite=strict`.
- A missing price is `NULL`, never `0`; no currency is converted without a sourced rate.
- The scheduler stays disabled by default; no route may enable it (`tests/test_admin_settings_api.py::test_there_is_no_scheduler_write_route` pins this).
- The notifications migration is never applied to the live database; tests and journeys use disposable copies only.
- No deployment, no push, no PR changes without the owner's explicit approval.
- Commits are local; attribution trailers follow the repository's existing commits, with no model name.

## Review Focus

1. A JSON body larger than the limit must be refused with 413 before it is parsed, not read in full and then rejected as invalid (Task 4).
2. When more than 100 alerts are unread, the badge must not silently disagree with the list (Task 5).
3. An admin whose session has expired while the settings island is open must see a sign-in prompt, not an empty table or a raw error code (Task 2).
4. Two admins toggling the same source: the island must show the state the server stored, not the local click (Task 2).
5. A source key the server does not know must not create rows or change state (already pinned by `tests/test_admin_settings_api.py::test_unknown_and_not_yet_registered_sources_are_refused`; Task 3 keeps it passing).

## Deviations recorded during execution

- **Task 3 (server table kept):** the plan said the island hides the server sources table. It does not: that table holds the Collect actions and run history, which the island does not replace. Hiding it removed those controls whenever JavaScript ran. Commit `99ab8fd` keeps the table; the plan's "server table hidden" check is replaced by "server table kept" in Task 6.
- **Task 5 (additive JSON keys):** the notifications JSON gained `unread_total`, `listed` and `more`; the existing `unread` key is kept with the full count.

## Scope decisions (not tasks)

- **Not planned, owner decision:** the `create_app()` no-argument default database (`main.py` relies on it). Options are recorded in `docs/PROJECT_PROGRESS.md`.
- **Not planned, external:** Ollama real-model run (no binary or server here); eBay live calls (no credentials; license unread); landed-cost duty and tax rules (need owner-verified data); live migration and collection (approval, contact address, legal review).
- **Not planned, by design:** price forecasting (stays off until history justifies it); a JSON route for scheduler control (reported only).
- **Not planned, YAGNI:** a frontend island for the comparison or alerts pages (the server pages already work and are populated-state tested); splitting the home page out of `create_app` (`app.py` is about 300 lines and not blocking).

---

### Task 1: Admin settings client types and API functions

**Files:**
- Modify: `frontend/src/api/types.ts` (append types)
- Modify: `frontend/src/api/client.ts` (add two entries to `api`)
- Create: `frontend/src/api/client.test.ts`

**Interfaces:**
- Consumes: `getJson` and `sendJson` already in `client.ts`; `ApiError` (status, message).
- Produces:
  - `interface AdminSource { key: string; name: string; kind: string; enabled: boolean; configured: boolean; page_budget: number | null }`
  - `interface AdminSettingsResponse { sources: AdminSource[]; scheduler: { enabled: boolean; env: boolean; ceiling: number; error?: string } }`
  - `api.adminSettings(signal?: AbortSignal): Promise<AdminSettingsResponse>` → `GET /api/v1/admin/settings`
  - `api.setSourceEnabled(key: string, enabled: boolean): Promise<{ key: string; enabled: boolean }>` → `POST /api/v1/admin/sources/{encodeURIComponent(key)}/enabled` with body `{ enabled }`

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/api/client.test.ts
import { describe, it, expect, vi, afterEach } from 'vitest';
import { api, ApiError } from './client';

afterEach(() => vi.restoreAllMocks());

describe('admin settings client', () => {
  it('posts a strict boolean to the per-source enabled route', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ key: 'adafruit', enabled: false }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const out = await api.setSourceEnabled('adafruit', false);
    expect(out).toEqual({ key: 'adafruit', enabled: false });
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/v1/admin/sources/adafruit/enabled');
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({ enabled: false });
  });

  it('turns a 403 into an ApiError with the status kept', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'Admin authentication required.' }), { status: 403 })));
    await expect(api.adminSettings()).rejects.toMatchObject({ status: 403 });
    await expect(api.adminSettings()).rejects.toBeInstanceOf(ApiError);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/api/client.test.ts`
Expected: FAIL with "api.setSourceEnabled is not a function".

- [ ] **Step 3: Add the types and the two `api` entries**

Add the two interfaces to `types.ts` exactly as listed under Produces. In `client.ts`, add the two entries to the `api` object, using the existing `getJson` / `sendJson` helpers and `encodeURIComponent` for the key.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/api/client.test.ts && npm run typecheck`
Expected: 2 tests pass; typecheck exits 0.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/types.ts frontend/src/api/client.ts frontend/src/api/client.test.ts
git commit -m "Frontend: admin settings client functions and types"
```

---

### Task 2: Admin settings island

**Files:**
- Create: `frontend/src/pages/adminSettings.ts`
- Create: `frontend/src/pages/adminSettings.test.ts`

**Interfaces:**
- Consumes: `api.adminSettings`, `api.setSourceEnabled`, `ApiError`, `AdminSettingsResponse`, `AdminSource` (Task 1); `h` and `state` helpers as used in `watchlist.ts`.
- Produces: `export function mountAdminSettings(root: HTMLElement): void`

Behaviour: loads settings on mount; one row per source with a single button labelled "Disable" or "Enable"; a click calls `setSourceEnabled`, then calls `api.adminSettings` again and re-renders every row from that response (the server's state is the only state the island displays); the scheduler is shown as read-only text ("Scheduled collection: disabled; set on the server.") with no control. A 401 or 403 renders `role="alert"` text "Sign in to the admin area to manage sources." with no retry button. Other failures render `role="alert"` plus a "Try again" button.

- [ ] **Step 1: Write the failing tests**

```ts
// frontend/src/pages/adminSettings.test.ts  (same jsdom and stub style as matchReview.test.ts)
it('renders one row per source and the scheduler as read-only text', async () => {
  stubApi({ 'GET /api/v1/admin/settings': () => json(settings) });
  mountAdminSettings(root); await flush(); await flush();
  expect(root.querySelectorAll('tbody tr')).toHaveLength(settings.sources.length);
  expect(root.textContent).toContain('Scheduled collection: disabled');
  expect(root.querySelector('[data-scheduler] button')).toBeNull();
});

it('shows the state the server stored after a toggle, not the local click', async () => {
  // server answers "enabled: true" even though the admin clicked Disable
  stubApi({
    'GET /api/v1/admin/settings': () => json(settings),
    'POST /api/v1/admin/sources/adafruit/enabled': () => json({ key: 'adafruit', enabled: true }),
  });
  mountAdminSettings(root); await flush(); await flush();
  (root.querySelector('[data-source="adafruit"] button') as HTMLButtonElement).click();
  await flush(); await flush();
  expect(root.querySelector('[data-source="adafruit"]')?.textContent).toContain('Enabled');
});

it('asks a signed-out visitor to sign in, with no retry button', async () => {
  stubApi({ 'GET /api/v1/admin/settings': () => json({ detail: 'Admin authentication required.' }, 403) });
  mountAdminSettings(root); await flush(); await flush();
  expect(root.querySelector('[role="alert"]')?.textContent).toContain('Sign in to the admin area');
  expect(root.querySelector('button')).toBeNull();
});
```

`settings` is a fixture with two sources (`adafruit`, `books_toscrape`) and `scheduler: { enabled: false, env: false, ceiling: 500 }`; `stubApi`, `json`, `flush` are the helpers already used in `matchReview.test.ts` (copy them, do not import from a test file).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/pages/adminSettings.test.ts`
Expected: FAIL with "Cannot find module './adminSettings'".

- [ ] **Step 3: Implement `mountAdminSettings` in `frontend/src/pages/adminSettings.ts`**

Build the DOM with the same `h()` helper used by the other islands. Each row carries `data-source="<key>"`; the scheduler block carries `data-scheduler`. Do not put any raw HTML from the server into the page (use text nodes).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/pages/adminSettings.test.ts && npm run typecheck`
Expected: 3 tests pass; typecheck exits 0.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/adminSettings.ts frontend/src/pages/adminSettings.test.ts
git commit -m "Frontend: admin settings island (per-source enable switch, read-only scheduler)"
```

---

### Task 3: Wire the island into the admin page

**Files:**
- Modify: `frontend/src/main.ts` (add `'admin-settings': mountAdminSettings` to `islands`)
- Modify: `shopping/web/templates/admin.html` (add an island slot before the existing sources table; add class `server-sources` to that existing table's wrapper)
- Create: `tests/test_admin_island_markup.py`

**Interfaces:**
- Consumes: `mountAdminSettings` (Task 2); `islands` template flag already passed by `routes_admin.admin_page`.
- Produces: the page contains `data-island="admin-settings"` (hidden) and `.server-sources` (the server copy, hidden by the island once it runs).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_admin_island_markup.py
def test_admin_page_offers_the_settings_island_and_keeps_the_server_copy(client_signed_in):
    html = client_signed_in.get("/admin").text
    assert 'data-island="admin-settings"' in html
    assert html.count('class="server-sources') == 1
```

`client_signed_in` is a TestClient with the admin token set and `/admin/login` already posted (copy the `client` fixture pattern from `tests/test_admin_settings_api.py`).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_admin_island_markup.py -v`
Expected: FAIL (`data-island="admin-settings"` not in page).

- [ ] **Step 3: Make the template and entry-point edits**

Add the slot next to the existing `match-review` slot in `admin.html`, guarded by `{% if islands %}`, with `hidden` set. Wrap the existing sources table in an element with class `server-sources`. In `main.ts`, import `mountAdminSettings` and add the key.

- [ ] **Step 4: Run tests, rebuild the bundle, verify**

Run: `pytest tests/test_admin_island_markup.py -v && cd frontend && npm test`
Expected: pytest passes; `npm test` runs vitest (all pass), builds, and reports "bundle matches a fresh build of the sources".

- [ ] **Step 5: Commit**

```bash
git add frontend/src/main.ts shopping/web/templates/admin.html shopping/web/static/app tests/test_admin_island_markup.py
git commit -m "Admin page: mount the settings island and keep the server table as the fallback"
```

---

### Task 4: Bound the JSON request body

**Files:**
- Modify: `shopping/web/routes_watchlist.py` (`json_body`, lines 17-25)
- Test: `tests/test_admin_settings_api.py` (append)

**Interfaces:**
- Consumes: none new.
- Produces: `MAX_JSON_BODY = 16 * 1024` (module constant in `routes_watchlist.py`); `json_body(request: Request) -> dict` unchanged in signature; raises `HTTPException(413, "request body too large")` when the body exceeds the limit.

- [ ] **Step 1: Write the failing tests**

```python
def test_an_oversized_json_body_is_refused_with_413_before_parsing(client):
    big = b'{"enabled": false, "pad": "' + b"x" * (17 * 1024) + b'"}'
    r = client.post("/api/v1/admin/sources/webscraper_io_static/enabled", content=big,
                    headers={**H, "Content-Type": "application/json"})
    assert r.status_code == 413

def test_a_normal_json_body_still_works(client):
    r = client.post("/api/v1/admin/sources/webscraper_io_static/enabled", json={"enabled": True}, headers=H)
    assert r.status_code == 200
```

- [ ] **Step 2: Run tests to verify the first one fails**

Run: `pytest tests/test_admin_settings_api.py -k "oversized or normal_json" -v`
Expected: the 413 test FAILs (currently 422 or 200).

- [ ] **Step 3: Implement the limit**

Check `Content-Length` first (refuse with 413 when it exceeds `MAX_JSON_BODY`); then read the body and refuse with 413 if it is still longer, since a client can omit or lie about the header. Keep the existing 422 messages for invalid JSON and non-object bodies.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_admin_settings_api.py tests/test_api_watchlist_review.py -v`
Expected: all pass, including the existing watchlist JSON tests.

- [ ] **Step 5: Commit**

```bash
git add shopping/web/routes_watchlist.py tests/test_admin_settings_api.py
git commit -m "JSON API: refuse request bodies over 16 KiB with 413"
```

---

### Task 5: Keep the unread count honest above 100

**Files:**
- Modify: `watchtower/notifications.py` (add `list_page(s, owner, limit=100) -> tuple[list[Notification], int]`, returning the listed rows and the full unread count; `unread_count` and `list_for` stay as they are)
- Modify: `shopping/web/routes_alerts.py` (`GET /api/v1/notifications` response adds `listed`, `unread_total`, `more`)
- Modify: `shopping/web/templates/base.html` (nav badge text shows `100+` when the count exceeds 100)
- Test: `tests/test_notifications.py` (append)

**Interfaces:**
- Consumes: `unread_count(s, owner) -> int`, `list_for(s, owner, limit=100)`.
- Produces: `list_page(s: Session, owner: str, limit: int = 100) -> tuple[list[Notification], int]`; the JSON response keys `listed: int`, `unread_total: int`, `more: bool`.

- [ ] **Step 1: Write the failing test**

```python
def test_more_than_100_unread_is_reported_honestly(seeded_owner_with_101_unread, client):
    body = client.get("/api/v1/notifications").json()
    assert body["listed"] == 100
    assert body["unread_total"] == 101
    assert body["more"] is True
    assert "100+" in client.get("/").text  # nav badge (use the page that renders base.html)
```

`seeded_owner_with_101_unread` creates 101 unread notification rows for the browser's owner on a disposable in-memory database using the same helpers as the existing `tests/test_notifications.py` fixtures.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_notifications.py -k more_than_100 -v`
Expected: FAIL (`listed` key missing).

- [ ] **Step 3: Implement `list_page` and the JSON keys; update the badge text**

`list_page` runs the same query as `list_for` and `unread_count` (both owner-filtered). The badge shows `100+` only when the full count is above 100; otherwise the exact number, as today.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_notifications.py -v`
Expected: all pass, including the existing alert and CLI tests.

- [ ] **Step 5: Commit**

```bash
git add watchtower/notifications.py shopping/web/routes_alerts.py shopping/web/templates/base.html tests/test_notifications.py
git commit -m "Alerts: report the full unread count honestly above 100"
```

---

### Task 6: Browser checks, documentation, and the full gate

**Files:**
- Modify: `e2e/journeys.js` (add to the admin section: the settings island renders rows and hides `.server-sources`; a source toggle round trip returns to the original state)
- Modify: `frontend/README.md` (the "Admin settings / source status" row moves from "Not built yet" to the built table)
- Modify: `docs/PROJECT_PROGRESS.md` (one entry for this plan)

**Interfaces:**
- Consumes: everything above.
- Produces: no code interfaces.

- [ ] **Step 1: Add the journey checks**

In the admin section of `e2e/journeys.js`, after the existing match-review checks, add: `settings island renders one row per source`, `server sources table is hidden once the island runs`, and a toggle that disables then re-enables one source, checking the row text after each step. Use the same disposable-copy environment the journeys already use.

- [ ] **Step 2: Run the full gate on a disposable copy**

Run:
```bash
ruff check shopping watchtower tests scripts main.py
pyright watchtower shopping
python -m pytest -q
cd frontend && npm test && cd ..
bash e2e/run.sh
```
Expected: ruff clean; pyright 0 errors; pytest all pass except the optional sklearn skips; `npm test` passes and reports the bundle fresh; `e2e/run.sh` prints `ALL E2E CHECKS PASSED`.

- [ ] **Step 3: Run the accessibility and reflow checks**

Run the server on a disposable copy, then:
```bash
AXE_PATH=<path to axe.min.js> E2E_BASE=<base> node e2e/axe-check.js
NO_ISLANDS=1 AXE_PATH=<path> E2E_BASE=<base> node e2e/axe-check.js
E2E_BASE=<base> node e2e/reflow-check.js
```
Expected: 0 violations on every page and width in both modes; 0 px page scroll at 320 px. Axe-core is installed outside the repository (see `docs/UX_AUDIT.md`).

- [ ] **Step 4: Update the docs and commit**

Edit the README row and the progress entry, then:
```bash
git add e2e/journeys.js frontend/README.md docs/PROJECT_PROGRESS.md
git commit -m "Docs and journeys: admin settings island is built, tested in populated states and in the fallback"
```

---

## Self-review (completed)

1. **Spec coverage:** the four backend/frontend gaps the repository records map to tasks (admin settings island: Tasks 1–3; body limit: Task 4; alert count above 100: Task 5; browser and doc closure: Task 6). The decision-gated and external items are listed under "Scope decisions", not left implicit.
2. **Step scan:** every step names its files, signatures, test names and the command that proves it. Bodies are left to the implementer where the signature and test determine the code.
3. **Type consistency:** `AdminSettingsResponse`, `AdminSource`, `api.adminSettings`, `api.setSourceEnabled`, `mountAdminSettings`, `MAX_JSON_BODY`, `list_page` are each defined once and used with the same names later.
4. **Review Focus:** each of the five lines has an owning task (1, 2, 4, 5 and the existing test for 5 kept green in Task 3).
5. **Proportion:** six tasks, each with one deliverable and one test cycle.

---

**Plan complete and saved to `docs/superpowers/plans/2026-11-09-missing-backend-frontend.md`. Please review the plan. Which execution approach would you prefer?**

- **Subagent-driven** — a fresh subagent implements each task and a fresh reviewer checks it before the next one starts, then a whole-branch review at the end. Most thorough; costs a fresh context per task and per review.
- **Native** — I implement every task myself in this session, then one fresh reviewer on the most capable model checks the whole branch. Cheapest and fastest; no independent review until the end.

**For this plan I recommend Native**, because the six tasks are small, run in order, and share only three files (`main.ts`, `admin.html`, the bundle); a shipped mistake would be a local commit that is easy to revert, and nothing is pushed. Does the plan capture what you want, and which approach should we use?
