# BIP UI Integration (Milestones B–D) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the existing BIP frontend into a cohesive, mobile-first intelligence console backed by typed endpoints. The work covers grouped navigation, global wallet search, a tabbed wallet workspace, persisted research history with provenance, case links, label curation and the provider detail view.

**Architecture:** This extends the existing React 19, Vite and TypeScript app (`frontend/src`) and the FastAPI and SQLite backend (`bip/`). It reuses `useApi`, `api`, `ApiError` and the existing page components; nothing is rewritten from scratch. Backend changes are new endpoints and one additive migration (v6).

**Tech Stack:** React 19, react-router 7, Vitest and Testing Library, Playwright (`frontend/e2e/smoke.mjs`), FastAPI, SQLAlchemy Core and SQLite, pytest.

**Spec:** `docs/FRONTEND_ARCHITECTURE.md` and `docs/FRONTEND_API_MAP.md`. Decisions are recorded in ADR-030 and ADR-031.

## Global Constraints
- **Theme and dependencies:** dark theme only. No new runtime npm dependency. System font stack (owner gave no font decision; system is the default in the spec, §6.3).
- **Navigation:** groups are Monitor (Overview, Chains, Providers), Explore (Wallets, Flows, Graph), Intelligence (Research, Opportunities), Cases (Investigations, Watchlists), then Settings.
- **Mobile target:** Galaxy S24 FE viewport, 412×915 CSS px. Also verify at 390×844 and 1360×900.
- **Layout invariants:** no page-level horizontal scroll on any route. Touch targets are ≥ 44 px under `(pointer: coarse)` and ≥ 32 px otherwise.
- **Text contrast:** ≥ 4.5:1. `--faint` is raised to `#7a8c92` or above; the new `--control-border` is ≥ 3:1 on `--surface`.
- **Honesty:** missing is shown as "Not retrieved" or "—", never 0. Fixture data carries the demo banner. Model predictions are never shown as facts.
- **Search:** it never triggers ingestion. Ethereum addresses are lowercased, and no EIP-55 claim is made.
- **Compatibility:** old URLs (`/flows?chain=&address=`, `/graph?…`, `/research?chain=&address=`) keep working.
- **Backend:** additive only. `GET /wallets/{c}/{a}` keeps its 404 behaviour.

## Review Focus
- **Address case and whitespace:** pasting an address with surrounding whitespace or in mixed case must still open the right wallet. Covered by `classifyQuery` tests in Task 3.
- **Storage unavailable:** `localStorage` throwing (private mode) must not break search or the shell. Covered by a `recent.ts` test in Task 3.
- **Reports from before the traceability fields:** they must render "Not recorded in this report version". Covered by a ReportView test in Task 9.
- **Backend offline on any page:** a single "Backend unreachable" state, never a crash. Covered by an App test in Task 2.
- **A deep link to a non-existent research run:** a clear not-found state, not an endless spinner. Covered by a run page test in Task 9.

---

### Task 1: Design tokens and CSS structure
**Files:** create `frontend/src/styles/tokens.css`, `layout.css` and `components.css`. Make `frontend/src/styles.css` import them (keeping its existing rules, migrated). Fix the `.shell` mobile row gap with `grid-template-rows: auto 1fr`.
- [ ] Tokens: `--s1`..`--s7` = 4/8/12/16/24/32/48 px; type scale 12/13/14/16/20/26; `--radius-control: 4px`; `--radius-panel: 8px`; `--faint: #7a8c92`; `--control-border: #566a72`.
- [ ] `h2` and `th` move to sentence case (no `text-transform` or tracking). Coarse-pointer rule: `.btn`, `.nav-link`, inputs and the check rows get `min-height: 44px`.
- [ ] Verify: Vitest, `tsc` and ESLint stay green. The browser sweep (Task 12 script) shows the fixed mobile header gap.

### Task 2: App shell and grouped navigation
**Files:**
- Modify: `frontend/src/App.tsx`.
- Create: `frontend/src/components/shell.tsx`.
- Test: `frontend/src/App.test.tsx`.

**Interfaces:**
- Produces: `NAV_GROUPS: {label: string; items: {to: string; label: string}[]}[]`.
- Keeps: `NAV` as the flattened list (existing tests use it).
- Produces: `usePageTitle(title: string): void`, which sets `document.title` to `` `${title} · BIP` ``.

Steps:
- [ ] Tests first:
  - Group headings "Monitor", "Explore", "Intelligence" and "Cases" render.
  - The Menu button opens the drawer, and Escape closes it and returns focus to Menu.
  - Navigating sets `document.title` (Overview → "Overview · BIP").
  - The sidebar version comes from `/health`, not "0.5".
  - A skip link "Skip to content" exists.
- [ ] Implement:
  - The drawer under 900 px, with focus moved into it and Escape handling.
  - `main#content`, which takes `tabIndex=-1` focus on route change.
- [ ] Verify: Vitest passes.

### Task 3: Global wallet search
**Files:**
- Create: `frontend/src/lib/search.ts`, `frontend/src/lib/recent.ts`, `frontend/src/components/WalletSearch.tsx`.
- Tests: `search.test.ts`, `recent.test.ts`, `WalletSearch.test.tsx`.

**Interfaces:**
- `classifyQuery(input: string): {kind: "wallet"; chain: "ethereum"|"solana"; address: string} | {kind: "transaction"; chain: "ethereum"|"solana"} | {kind: "invalid"; message: string} | {kind: "empty"}`.
- `recentWallets(): {chain: string; address: string}[]`, holding at most 8 entries.
- `rememberWallet(chain: string, address: string): void`.
- `clearRecent(): void`. All three never throw.

Steps:
- [ ] Test cases:
  - `" 0xABCDEF…(40 hex) "` → wallet, ethereum, lowercased.
  - A 64-hex `0x` value → transaction.
  - A valid Solana public key → wallet, solana.
  - An 88-character base58 string → transaction, solana.
  - `"hello"` → invalid.
  - `localStorage.setItem` throwing → `rememberWallet` doesn't throw and `recentWallets()` returns `[]`.
- [ ] `WalletSearch`:
  - A labelled input ("Search wallet address") with `/` as the focus shortcut.
  - Enter navigates to `/wallets/:chain/:address`.
  - Invalid input shows the inline error with `aria-describedby`.
  - A transaction hash shows "This looks like a transaction, not a wallet. Transaction lookup is not available yet."
- [ ] Mount it in the shell's top bar (mobile) and above the main content (desktop).

### Task 4: Shared data components
**Files:**
- Create: `frontend/src/components/Address.tsx`, `frontend/src/components/DataTable.tsx`, `frontend/src/components/Grade.tsx`.
- Test: `components.test.tsx`.

**Interfaces:**
- `<Address value chain? link? />`: middle-truncated as 8 characters + "…" + 6 characters, with the full value in `title` and a copy button (`aria-label="Copy address"`).
- `<DataTable<T> rows columns={[{key, header, render(row), priority: 1|2|3}]} caption />`: a table at ≥ 600 px; below that, a `ul.cards` list whose priority-1 column is the card title.
- `<Grade kind status? />`: shape class `grade-<kind>` plus a visible text label.

Steps:
- [ ] Tests:
  - Address truncates and its copy button calls `navigator.clipboard.writeText` with the full value.
  - DataTable renders the card list when `matchMedia("(max-width: 599px)")` matches, and a table otherwise.
- [ ] Apply `Address` to Overview recent activity (this fixes the overflow) and to the wallet tables.

### Task 5: Wallet status endpoint and route_not_found (backend)
**Files:**
- Modify: `bip/api/routes/wallets.py`, `bip/api/app.py` (404 handler), `bip/db/repository.py` (counts).
- Test: `tests/api/test_wallet_status.py`.

**Interfaces:** `GET /api/v1/wallets/{chain}/{address}/status` →
```
{chain, address, stored: bool,
 transaction_count, transfer_count, first_observed_at, last_observed_at, last_ingested_at,
 data_origin, classifications: int, research_runs: int,
 watchlists: [{id, name}], investigations: [{id, title}]}
```
Values are `null` when not stored. An invalid address returns `422 invalid_input`. Unmatched routes return `404 {"error":{"code":"route_not_found"}}`.

Steps:
- [ ] pytest:
  - An unstored valid address → 200 with `stored: false` and `transaction_count: null`.
  - After ingest (fake transport) → `stored: true` with counts > 0.
  - An invalid address → 422.
  - `GET /api/v1/nope` → 404 `route_not_found`.
  - `GET /wallets/{c}/{unstored}` is still 404 `not_found`.
- [ ] Client:
  - `api.walletStatus(chain, address)`.
  - Pages treat only `code === "not_found"` as "not stored"; `route_not_found` becomes `ErrorState`. Add a client test.

### Task 6: Research rerun client and report traceability (backend and client)
**Files:** modify `bip/research/engine.py` (`build_report`) and `frontend/src/api/client.ts` and `types.ts`. Test: `tests/stage5/test_research.py`.
- [ ] Test: a new report's `classification` includes `feature_digest`, `taxonomy_version`, `as_of` and `model.dataset_version` (or `null` with no model), and `report_version` is bumped.
- [ ] Implement: copy the fields from the classification and model row. Do not back-fill old reports.
- [ ] Client: `api.researchRerun(id)`, `api.researchRuns({chain?, address?, limit, offset})` with an optional filter, `api.walletFeatures`, `api.taxonomy`, `api.labels`, `api.addLabel`, `api.reviewLabel`, and the provider history, reliability and consistency endpoints.

### Task 7: Migration v6, research ↔ case links (backend)
**Files:** modify `bip/db/schema.py` (`SCHEMA_VERSION = 6`, table `investigation_research_runs`), `bip/db/engine.py` (migration), `bip/api/routes/investigations.py`. Test: `tests/api/test_case_research_links.py`.

**Interfaces:** `POST /investigations/{id}/research-runs {run_id, note?}` → 201. `GET /investigations/{id}/research-runs` → `{items:[{run_id, attached_at, note, state, question, chain, address}]}`. `DELETE /investigations/{id}/research-runs/{run_id}` → 204.

Steps:
- [ ] Tests:
  - A v5 database with data upgrades to v6 with existing rows intact.
  - Attaching the same run twice → 409.
  - An unknown run → 404 `not_found`.
  - Deleting a link leaves the run and its report intact.
- [ ] Update `tests/api/test_database.py` expectations to versions 1–6.

### Task 8: Wallet workspace with tabs
**Files:**
- Create: `frontend/src/pages/wallet/Workspace.tsx` (header, coverage line, action bar, `WorkspaceTabs`) and one file per tab (`OverviewTab`, `ActivityTab`, `ClassificationTab`, `ResearchTab`, `CasesTab`, `EvidenceTab`).
- Modify: `CapitalFlows.tsx` and `Graph.tsx` to export a view that takes `chain` and `address` props. `Wallets.tsx` keeps the index.
- Modify: `App.tsx` routes `/wallets/:chain/:address/:tab?`, with redirects from old query URLs.
- Test: `wallet.test.tsx`.

Steps:
- [ ] Tests:
  - An unstored wallet (status `stored: false`) shows "Nothing has been retrieved for this address yet" and "Retrieve activity", with no ErrorState.
  - Tab links change the URL.
  - `/flows?chain=ethereum&address=0x…` redirects to `/wallets/ethereum/0x…/flows`.
  - The Cases tab lists watchlists and cases from the status endpoint.
  - "Add to watchlist" posts an entry.
- [ ] Reuse the existing `IngestPanel`, `WalletIntel` sections, `ClassificationView` and the flows and graph views. Each tab loads only its own data.

### Task 9: Research history and run page
**Files:** create `frontend/src/pages/research/History.tsx` and `RunPage.tsx`. Modify `Research.tsx` (index = history + models + labels tabs). Route `/research/runs/:runId`. Test: `research.test.tsx`.
- [ ] Tests:
  - History lists persisted runs from `/research/runs`.
  - The run page polls until the run is terminal.
  - Rerun navigates to the new run and shows "Rerun of …".
  - The provenance panel shows run id, data origin, report version, model name and version, and feature digest. An old report (no digest) shows "Not recorded in this report version".
  - An unknown run id → "Research run not found".
  - "Add to case" lists cases and posts the link.

### Task 10: Cases, findings, labels, provider detail
**Files:** modify `Investigations.tsx` (linked runs, attached findings). Modify `WalletIntel`/`EvidenceTab` ("Attach to case" on findings). Create `pages/research/Labels.tsx` and `pages/ProviderDetail.tsx` (`/providers/:id`). Tests in the existing page test files.
- [ ] Tests:
  - A case shows linked runs.
  - Attaching a finding calls `/intelligence/findings/{id}/attach`.
  - The labels page shows counts against the gates ("12 of 30 labelled wallets") and adds a label with status and provenance, then a review.
  - Provider detail renders capability history and the reliability state, or "not tested".

### Task 11: Watchlist batch analysis and search/filter
**Files:** modify `Watchlists.tsx`.
- [ ] Tests: a filter box narrows entries. "Analyse watchlist" calls `/intelligence/watchlists/{id}/analyze` and shows the per-entry result counts.

### Task 12: End-to-end and visual verification
**Files:** modify `frontend/e2e/smoke.mjs`.
- [ ] Add checks on the fixture backend:
  - For every route at 412×915 and 1360×900: `scrollWidth <= innerWidth`, and no coarse-pointer target under 44 px (with `hasTouch` emulation).
  - The workflow: search → retrieve → classify → start research → report provenance visible → reload → the run is still listed → add to case → the case shows the run. Then restart the backend process and confirm the run is still listed.
- [ ] Run the full suites (pytest, Vitest, ESLint, tsc, build, e2e) and record the output in `IMPLEMENTATION_LOG.md`.

### Task 13: Documentation
- [ ] Update `CURRENT_STATE.md` (capability matrix), `IMPLEMENTATION_LOG.md`, `README.md` (start commands, including the phone), `docs/DEVELOPMENT.md` (new endpoints) and `docs/FRONTEND_ARCHITECTURE.md` status markers.
