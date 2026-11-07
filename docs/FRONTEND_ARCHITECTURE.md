# Frontend Architecture and Design Specification

_Status (2026-11-07): **implemented** as specified, except for the deviations below (rulings in `IMPLEMENTATION_LOG.md`). Plan: `docs/superpowers/plans/2026-11-06-bip-ui-integration.md`. Owner decisions: dark theme only; grouped navigation. Architectural choices are in ADR-030 and ADR-031 (Proposed)._

**Deviations from this specification:**
- **Search placement:** the global search sits at the top of the main column on every width, not in the mobile top bar, to keep the sticky header under 96 px.
- **Old wallet URLs:** `/flows?…`, `/graph?…` and `/research?…` keep working as standalone pages instead of redirecting into the workspace.
- **Research history:** it is listed under the `/research` wallet picker rather than in its own tab.
- **Fonts:** system fonts are used, since no font decision was made.
- **Not yet implemented:** the graph's list-view alternative (§7), sticky table headers, and a 2-second poll pause when the browser tab is hidden. Run polling is 1 second while the page is open.

Companion document: `docs/FRONTEND_API_MAP.md` (page → component → endpoint map, gaps, planned endpoints).

## 1. Scope and constraints

- Keep the existing stack: React 19, Vite, TypeScript strict, react-router 7, Vitest, Playwright e2e (`frontend/e2e/smoke.mjs`). No UI framework, CSS framework, state library or chart library is added.
- Keep the existing `useApi` hook, `api` client, `ApiError` and the shared `Loading` / `EmptyState` / `ErrorState` components. Extend them; do not replace them.
- Every existing route keeps working (old URLs redirect, never 404). Every existing Vitest and e2e check keeps passing or is updated with an explanation in `IMPLEMENTATION_LOG.md`.
- No database record is changed or removed. Backend additions are new endpoints or additive migrations only.
- Honesty rules carry over unchanged: missing is shown as missing (never 0), fixtures carry the demo banner, model outputs are never shown as facts, and nothing is attributed to a real-world identity.

## 2. Information architecture

### 2.1 Grouped navigation (approved)

| Group | Items | Purpose |
|---|---|---|
| Monitor | Overview, Chains, Providers | State of this BIP instance and its data sources |
| Explore | Wallets, Flows, Graph | Look at stored on-chain evidence |
| Intelligence | Research, Opportunities | Classification, investigations, research leads |
| Cases | Investigations, Watchlists | Analyst-curated work |
| — | Settings | Read-only configuration |

- **Desktop (≥ 900 px):** a fixed left sidebar, 232 px wide, with group headings. A group heading is a plain text label in sentence case; it is not a link and not collapsible. The current item carries `aria-current="page"`, an accent bar and stronger text.
- **Tablet and mobile (< 900 px):** a top bar with the brand, the global search and a "Menu" button. The menu opens a full-height drawer with the same groups. The drawer traps focus while open, closes on Escape, on route change and when the backdrop is tapped, and returns focus to the "Menu" button.
- Labels are shortened to fit (the "Wallet Intelligence" label becomes "Wallets", "Relationship Graph" becomes "Graph", "Capital Flows" becomes "Flows", "AI Research" becomes "Research"). Page headings keep their full names.
- The version label in the sidebar comes from `/health` (`version`), not a hard-coded string. Today the sidebar says "0.5" while the backend reports `0.1.0`.

### 2.2 Routes

Existing routes are kept. The wallet workspace gains tab sub-paths; standalone pages that need a wallet stay as entry points.

| Route | Page | Change |
|---|---|---|
| `/` | Overview | Fix overflow; findings link to their case |
| `/chains` | Chains | Unchanged content; mobile layout |
| `/providers`, `/providers/:id` (new) | Providers, provider detail | Detail page shows the Stage 4 reliability, consistency and capability history |
| `/wallets` | Wallets (search, stored-wallet list) | Becomes the "recent and stored wallets" index |
| `/wallets/:chain/:address` | Wallet workspace, Overview tab | Restructured (§4) |
| `/wallets/:chain/:address/:tab` (new) | Wallet workspace tabs | `activity`, `flows`, `graph`, `classification`, `research`, `cases`, `evidence` |
| `/flows`, `/graph` | Flows, Graph | Without a wallet: a "choose a wallet" state plus recent wallets. With `?chain=&address=`: redirect to the wallet tab (old links keep working) |
| `/research` | Research index | Research history across all wallets (persisted runs), models and AI provider |
| `/research/runs/:runKey` (new) | Research run and report | Deep-linkable, survives reloads (§5) |
| `/opportunities` | Opportunities | Detail drawer per lead |
| `/investigations`, `/investigations/:id` | Cases | Case shows attached findings and linked research runs |
| `/watchlists` | Watchlists | Batch "analyse watchlist" action |
| `/settings` | Settings | Unchanged |
| `/research?chain=&address=` | (existing deep link) | Redirect to `/wallets/:chain/:address/research` |

Each route sets `document.title` to `"<Page> · BIP"` (wallet pages use `"<short address> · <chain> · BIP"`). On every route change, focus moves to the page `<h1>` (`tabIndex={-1}`) so screen-reader users hear the new page.

## 3. Global wallet search

### 3.1 Behaviour

- **Placement:** a search field sits in the top bar on mobile and at the top of the main column on desktop. The `/` key focuses it unless focus is already in a text field. Its label is "Search wallet address".
- **Recognising the chain:** the chain is inferred from the input's format with the existing `frontend/src/lib/address.ts` rules, and validated by the backend:
  - Ethereum: `0x` followed by 40 hex digits, any case, normalised to lowercase for routing. BIP has no Keccak implementation, so an EIP-55 mixed-case checksum is **not** verified. The hint under the field says so, rather than implying the address was checksum-checked. Adding checksum verification would need a Keccak-256 implementation; deferred.
  - Solana: base58 public key of 32–44 characters that decodes to 32 bytes.
  - The two formats cannot overlap, so no chain picker is needed. A chain selector stays available for the explicit case and for future chains.
- **Recognised but not supported:** a 64-hex `0x` string (Ethereum transaction hash) or an 87–88 character base58 string (Solana signature) gets the message "This looks like a transaction, not a wallet. Transaction lookup is not available yet." There is no navigation and no guessing.
- **Result:** pressing Enter navigates to `/wallets/:chain/:address`.
  - Search **never** triggers ingestion. Retrieval uses provider quota and stays an explicit action on the wallet page.
  - Before navigating, the client calls the new wallet status endpoint (§3.2). The dropdown then shows one line: "Stored: 2 transactions, last retrieved 2026-11-05" or "Nothing stored yet". This is information only, so it can never be mistaken for a result.
- **Recent wallets:** the last 8 wallets opened are kept in `localStorage` as a per-browser convenience.
  - Wrapped in try/catch; works with storage unavailable.
  - Visible "Clear recent" button.
  - The list is shown on focus, on `/wallets`, `/flows` and `/graph`.
  - Addresses only, never data.
- **Errors:** invalid input shows an inline error under the field (`aria-describedby`, `role="alert"`). An empty submit does nothing.

### 3.2 Backend support (ADR-031)

`GET /api/v1/wallets/{chain}/{address}/status` (new) returns `200` for any **valid** address, stored or not:

```json
{
  "chain": "ethereum",
  "address": "0x…",
  "stored": false,
  "transaction_count": null,
  "transfer_count": null,
  "first_observed_at": null,
  "last_observed_at": null,
  "last_ingested_at": null,
  "data_origin": "live|offline_fixture",
  "classifications": 0,
  "research_runs": 0,
  "watchlists": [],
  "investigations": []
}
```

- `null` means "not stored", never 0. Counts of related records (classifications, research runs) are real counts.
- An invalid address returns `422 invalid_input`. An unsupported chain returns `422 invalid_input`.
- The wallet workspace uses this endpoint for its first render, which removes the expected-404 console error (§9).
- `GET /wallets/{chain}/{address}` keeps returning `404 not_found` for an unstored wallet (backwards compatible).

## 4. Wallet workspace

The wallet page becomes the hub that connects every capability. Today it is one 3,500 px column (5,400 px on mobile) with the raw tables at the bottom.

### 4.1 Layout

```
┌ header ─────────────────────────────────────────────────────────────┐
│ Wallet  0x1234…abcd  [copy] [explorer ↗]   Ethereum                 │
│ Stored: 42 tx · observed 2025-10-01 → 2025-10-23 · 1 ingestion run  │
│ [demo-data banner when data_origin = offline_fixture]               │
├ action bar ─────────────────────────────────────────────────────────┤
│ [Retrieve activity] [Analyse] [Classify] [Start research]  [More ▾] │
│                                      More: Add to watchlist, Add to case
├ tabs ───────────────────────────────────────────────────────────────┤
│ Overview  Activity  Flows  Graph  Classification  Research  Cases  Evidence
├─────────────────────────────────────────────────────────────────────┤
│ tab content                                                         │
└─────────────────────────────────────────────────────────────────────┘
```

**Mobile (< 600 px):**
- The header wraps: the address is truncated in the middle and the full value is in the copy button.
- The action bar keeps the primary action ("Retrieve activity", or "Start research" once data exists) and moves the rest into "More".
- The tab list scrolls horizontally with scroll-snap and keeps the active tab in view.
- Tabs are links (`/wallets/:chain/:address/:tab`), so the back button, reloads and shared links work. They use `role="tablist"` semantics only if they behave as in-page tabs. Because they navigate, they are rendered as a `nav` with `aria-current`, which is simpler and correct.

### 4.2 Tab contents and sources

| Tab | Content | API (see map) |
|---|---|---|
| Overview | Coverage summary, verification counts, top behaviour findings, latest classification summary, latest research run, cases and watchlists containing this wallet; each block links to its tab | status (new), `intelligence/wallets/…/profile`, `ml/classifications/…?limit=1`, `research/runs?chain&address&limit=1` |
| Activity | Transactions, transfers, balance changes; ingestion panel and run history | `wallets/…/transactions`, `transfers`, `balance-changes`, `ingestion/jobs` |
| Flows | Existing Capital Flows view, scoped | `intelligence/flows/…` |
| Graph | Existing graph view, scoped; depth control | `intelligence/graph/…` |
| Classification | Latest classification, history, features with availability, model card (name, version, feature version, data kind, status, dataset version) | `ml/classify`, `ml/classifications/…`, `ml/features/…`, `ml/models/{name}/{version}` |
| Research | Runs for this wallet (persisted), start new, open report | `research/runs?chain&address`, `research/runs` POST |
| Cases | Cases and watchlists containing the wallet; "Add to case" / "Add to watchlist"; attach a finding | `investigations`, `watchlists`, `intelligence/findings/{id}/attach` |
| Evidence | Behaviour rules, anomalies, findings, counterparties, contract interactions, verification (corroborate), limitations | `intelligence/wallets/…/behaviour`, `anomalies`, `verification/*` |

- The existing `WalletIntel` component is split along these tab lines. Its sections are reused, not rewritten.
- Each tab loads only its own data, so the page stops issuing every request on first load.

### 4.3 Rules

- **Unstored wallet:** if the wallet has nothing stored, every tab shows the same empty state. It says "Nothing has been retrieved for this address yet" and offers "Retrieve activity". Tabs whose data does not depend on ingestion (Research history, Cases) still show their real lists.
- **Provenance in the header:** the header coverage line is the single place where "what this page is based on" is stated: retrieved window, number of runs, source providers, verification counts. Tabs refer back to it rather than repeating it.

## 5. Research history, reports and traceability

### 5.1 What exists today

Research runs, plans, tool calls, claims, reports and model usage are already persisted (migration v5: `research_runs`, `research_reports`, `research_claims`, …). A run survives a restart (`recover()` marks interrupted runs failed).

The gap is in the UI:
- the history endpoint `GET /research/runs` is never called;
- rerun has no client method;
- a report is only reachable while the page that started it is open.

### 5.2 Target

- **Research index (`/research`):** a paginated table of all runs, newest first, filterable by chain, address and state.
  - Columns: started, wallet, question (truncated), state, claim counts by status, data origin.
  - A row opens `/research/runs/:runKey`.
- **Run page (`/research/runs/:runKey`):**
  - Live progress while a run is active: poll `GET /research/runs/{key}` every 2 s; stop polling when the run is terminal or the tab is hidden.
  - Cancel button.
  - The report, once complete.
  - Rerun: `POST …/rerun` creates a new run. The page shows lineage ("Rerun of …", "Rerun as …") from `parent_run_key`. The original report is never replaced.
- **Provenance panel on every report:** always visible, shown above the summary on mobile.

  | Field | Source |
  |---|---|
  | Run key, parent run, created, finished, state | `research_runs` |
  | Data origin (live / offline fixture) | `research_runs.data_origin` |
  | Engine / report version | `report.report_version` |
  | Question, focus, plan steps | `report.question`, `report.plan` |
  | Budget and usage (seconds, tool calls, model calls) | `budget_json`, `usage_json` |
  | Classification id, account type status | `report.classification` |
  | Rules and model: name, version, feature version, data kind, status | `report.classification.rules`, `.model` |
  | Feature digest, taxonomy version, as-of time | **missing from the report today**; see below |
  | Observation scope and coverage | `report.observation` |
  | Evidence references (each links to the Evidence tab or transaction) | `report.evidence_references` |
  | LLM provider and model, or "No AI provider; deterministic summary" | `report.llm` |
  | Limitations | `report.limitations` |

- **Traceability gap (Milestone C):** the report's classification block omits `feature_digest`, `taxonomy_version`, `as_of` and the model's `dataset_version`.
  - The fix adds these fields to new reports and bumps `report_version`.
  - Older reports are shown with "Not recorded in this report version". The UI never fills the gaps from the current model, because that would misattribute provenance.
- **Linking research to cases (ADR-030):** a new table `investigation_research_runs (investigation_id, run_key, attached_at, note)` in an additive migration v6.
  - From a run page: "Add to case".
  - On a case page: a list of linked runs with state and links.
  - Deleting a case removes only the links; runs and reports remain.

## 6. Visual design system (dark only)

### 6.1 Direction

BIP is an analyst console for evidence of uncertain strength. The one distinctive thing in the interface is the **evidence grade**: a consistent visual language that says how strong each statement is. Everything else stays quiet: one neutral palette, one accent, dense but legible type.

This replaces two generic habits the current UI has:
- uppercase tracked-out panel headings, which become sentence case;
- monospace used for labels; monospace is kept for identifiers and numbers.

### 6.2 Colour tokens

The existing palette is kept and the accent is unchanged (`--accent #4fb8a8`). Measured WCAG contrast:

| Token | Value | On `--surface` | Verdict | Change |
|---|---|---|---|---|
| `--text` | `#d9e3e6` | 13.8:1 | pass | — |
| `--muted` | `#84969c` | 5.9:1 | pass AA | — |
| `--faint` | `#5a6b71` | 3.3:1 (3.0 on `--surface-2`) | **fails AA** for text (table headers, stat hints) | Raise to ≥ 4.5:1 on `--surface-2` (≈ `#7a8c92`), or use `--muted` |
| `--line-2` (control borders) | `#2a383f` | 1.5:1 | **fails** the 3:1 non-text minimum for inputs | New `--control-border` ≥ 3:1 (≈ `#566a72`); keep `--line-2` for dividers |
| State colours `ok/warn/bad/info/idle` | — | 5.5–8.6:1; pills 5.4–7.2:1 | pass | — |

- Colour is never the only signal. Every state pill keeps its text, and evidence grades also differ by shape (§6.4).

### 6.3 Type, space, shape

- **Typefaces:** keep the system UI stack for text and the system mono stack for identifiers.
  - Owner decision (optional): self-host IBM Plex Sans and Plex Mono via `@fontsource` (OFL, about 180 KB of woff2, no network at runtime). The design works without it.
- **Type scale** (base 14 px, ratio about 1.2): 12 / 13 / 14 / 16 / 20 / 26 px.
  - Page `h1` is 26/600.
  - Panel heading `h2` is 16/600 in sentence case. No uppercase, no letter-spacing.
  - Table header is 12/600 in sentence case, `--muted`.
  - Body is 14/1.5; data tables are 13.
- **Monospace:** only for addresses, hashes, block numbers and raw amounts. Mono is not used for timestamps; those get tabular numbers in the text face.
- **Spacing scale:** 4, 8, 12, 16, 24, 32, 48 px (`--s1` to `--s7`). Panels use 16 px padding on mobile and 16/20 px on desktop.
- **Radius by hierarchy:** controls 4 px, panels 8 px, pills fully rounded. There is one rule per role, not one radius everywhere.
- **Elevation:** none. Separation comes from surface steps and 1 px lines. No shadows, no gradients.
- **Motion:** only in response to actions (drawer open, tab change, run progress). `prefers-reduced-motion` disables it.

### 6.4 Evidence grades (the signature element)

There is one component, `<Grade kind status>`, used everywhere a claim, metric or label appears: reports, the classification tab, behaviour rules, verification.

| Grade | Meaning (from `AI_RESEARCH_METHODOLOGY.md`) | Mark |
|---|---|---|
| Verified observation | Stored on-chain record, corroborated by independent providers | Solid filled dot, `--ok` |
| Corroborated | Two or more independent sources agree | Filled dot with ring, `--ok` |
| Observation (unverified) | Single-source on-chain record | Filled dot, `--idle` |
| Rule-based | Deterministic rule over stored evidence | Square, `--info` |
| Model prediction | Trained or experimental model output | Diamond, `--info`, plus calibrated score and status text |
| Statistical signal | Computed signal with a stated method | Triangle, `--info` |
| AI interpretation | LLM text citing claims | Open circle, `--warn` |
| Hypothesis | Unconfirmed explanation | Dashed open circle, `--warn` |
| Missing / insufficient | Could not be established | Hatched square, `--faint` (raised), with text "not established" |
| Conflicted / invalidated | Sources disagree / disproved | Crossed circle, `--bad` |

- The text label is always present. The shape adds a second channel for colour-blind users and for scanning.
- A legend sits at the top of each report and is linked from every grade via `title`/`aria-describedby`.

### 6.5 Components to build or extend (Milestone B)

- **`AppShell`:** grouped sidebar, mobile top bar and drawer, skip link "Skip to content", search slot.
- **`WalletSearch`:** §3.
- **`DataTable`:** column definitions with `priority` (1 = always shown). From 600 px up it renders a table inside `.table-wrap` with a sticky header. Below 600 px it renders a list of cards:
  - the priority-1 column is the card title;
  - the other visible columns form a key/value grid;
  - lower-priority columns sit behind "Show details".
  - Sorting, if any, is announced with `aria-sort`.
- **`Address`:** middle-truncated mono text, the full value in `title` and in a visually hidden span for screen readers, a copy button (≥ 44 px on touch) and an optional explorer link. This fixes the Overview overflow.
- **`Grade`:** §6.4.
- **`StatusLine`:** for runs and jobs (`queued → … → completed`); `aria-live="polite"`.
- **States:** the existing `Loading`, `EmptyState` and `ErrorState`, plus three new ones:
  - `NotStoredState`;
  - `InsufficientEvidence` (with the reason and what would resolve it);
  - `DemoBanner`, which is the existing banner made into a component.
- **Tabs-as-nav:** `WorkspaceTabs`.

## 7. Responsive and touch rules

- Mobile first. Breakpoints are 600 px (cards to tables), 900 px (drawer to sidebar) and 1200 px (two-column panels).
- **Invariant:** no horizontal page scroll at any route at 390 px or 1360 px (`document.documentElement.scrollWidth <= innerWidth`). Only `.table-wrap` and the graph canvas may scroll internally. The e2e script checks this on every route.
- **Touch targets:**
  - At least 44 × 44 px under `@media (pointer: coarse)`, with padding or an expanded hit area rather than a larger visual.
  - At least 32 px for fine pointers.
  - Checkboxes are wrapped in labels whose whole row is the target.
  - Today 30 targets on Providers and 8 each on Graph and Research are below 32 px.
- Long identifiers always go through `Address` or `overflow-wrap: anywhere`. No raw unbroken strings in flowing text.
- **Graph:**
  - On touch screens it keeps pan and zoom inside the canvas only (`touch-action: none` only on the SVG).
  - It offers a list view of nodes and edges as an accessible alternative.
  - Below 600 px it opens with the list view.
- Sticky elements: the mobile top bar only. The action bar does not stick, to save vertical space.

## 8. States and copy

| Situation | Shown as | Never shown as |
|---|---|---|
| Loading | `Loading` with what is loading ("Loading transactions…") | Blank area |
| Valid wallet, nothing stored | `NotStoredState` + "Retrieve activity" | Error, or zeros |
| Stored, field not retrieved | "Not retrieved" / "—" with a tooltip saying why | `0` |
| Endpoint error | `ErrorState` with the backend message and Retry | Generic "Something went wrong" |
| Backend unreachable | `ErrorState` (connection) + sidebar badge "Backend offline" | Stale data without a warning |
| Provider unavailable or blocked | The router's reason, verbatim and short; capability stays "not tested" | "Provider down" or "unsupported" |
| Insufficient evidence or labels | `InsufficientEvidence` with counts against thresholds ("12 of 30 labelled wallets") | A score, or a hidden section |
| Offline fixture data | `DemoBanner` on every page showing such data | Unlabelled |
| Run in progress | `StatusLine` with state and elapsed / budget | A spinner with no state |

Copy rules: sentence case, active voice, a button says what happens ("Retrieve activity", "Start research", "Add to case"), errors say what happened and what to do, no apologies.

## 9. Investigation: the unexplained 404 (audit item)

**Reproduced** on 2026-11-06 against the offline fixture backend. Steps: run the browser sweep and log every response with status ≥ 400.

**Request:** `GET /api/v1/wallets/ethereum/0x…aa`. It fires when the wallet page opens for an address whose data is not stored yet. In the sweep this was the first visit, before "Retrieve activity".

**Cause:**
- `WalletProfilePage` (`frontend/src/pages/Wallets.tsx:215`) loads the stored profile.
- The backend raises `RecordNotFoundError`, which becomes `404 {"error":{"code":"not_found","message":"no data is stored for ethereum address …"}}`.
- The page treats status 404 as "No data stored for this address yet" and renders an empty state.
- The browser still logs every 404 as a console error.

**Verdict:** this is expected behaviour, not a broken request. The UI handles it correctly in the normal case. There is no data loss and no user-visible error.

**A related latent defect:**
- An unknown route returns the same `404 not_found` (`GET /api/v1/nonexistent` returns `{"error":{"code":"not_found","message":"Not Found"}}`).
- The UI checks only the status code, so a missing or renamed endpoint (for example after a backend version mismatch) would be shown as "nothing stored for this address" instead of an error.

**Fix (Milestone C, ADR-031):**
1. The wallet workspace uses the new status endpoint (§3.2), which returns 200 with `stored: false`, so normal use produces no 404.
2. The FastAPI fallback for unmatched routes returns `404` with code `route_not_found`, distinct from `not_found` for missing records.
3. The client treats only `not_found` as "not stored"; any other 404 renders `ErrorState`. A regression test covers both.

## 10. Accessibility checklist

- Landmarks: `header`, `nav[aria-label="Main"]`, `main#content`, and a skip link.
- Focus moves to `h1` on navigation; the focus ring is visible on every control (`:focus-visible`, the accent, 2 px).
- Forms: every control has a `<label>`; errors are linked with `aria-describedby` and announced.
- Live regions: run and job progress (`polite`), errors (`alert`).
- Graph: a list alternative; SVG has `role="img"` with a summary label.
- Contrast: all text ≥ 4.5:1, controls and focus ≥ 3:1 (§6.2).
- Motion: honour `prefers-reduced-motion`.
- Checked by: Vitest + Testing Library role queries, and an e2e pass at 390 px and 1360 px. Axe is not added (it would be a new dependency); this can be reconsidered.

## 11. Delivery and verification per milestone

| Milestone | Delivers | Verified by |
|---|---|---|
| B Foundation | `AppShell` + grouped nav + drawer, `WalletSearch` (client-side validation), `DataTable`, `Address`, `Grade`, state components, token and CSS restructure (`tokens.css`, `layout.css`, `components.css`), titles and focus, overflow and touch fixes, redirects for old URLs | Vitest for each component; e2e no-overflow and target-size checks on all routes at 390 and 1360 px; all existing checks green |
| C Backend | Wallet status endpoint, `route_not_found`, rerun client, report traceability fields, migration v6 (case ↔ research link), label and review client methods | pytest (TDD for the status endpoint, migration v6 on a v5 database with data, report fields), pyright, ruff |
| D Workflows | Wallet workspace tabs, research index and run page, case linking, findings attach, label curation page, provider detail | Vitest per page; e2e workflows on the fixture backend (search → retrieve → classify → research → report → reload → add to case) |
| E QA | Full suites, security review of new routes, independent code review, simplification pass | Recorded output in `IMPLEMENTATION_LOG.md` |

## 12. Open decisions for the owner

1. Self-hosted IBM Plex fonts via `@fontsource` (about 180 KB, OFL), or stay on system fonts (default).
2. Approve ADR-030 (wallet workspace, grouped navigation, research ↔ case link in migration v6) and ADR-031 (wallet status endpoint and `route_not_found`).
3. Transaction-hash search is out of scope for now (it is recognised and declined). Confirm, or schedule it.
