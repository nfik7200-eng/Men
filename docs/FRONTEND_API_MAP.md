# Frontend ↔ API Map

_Status: Milestone A audit (2026-11-06), taken from `frontend/src/api/client.ts`, `frontend/src/pages/*` and `bip/api/routes/*` at commit `8e7fc29`. All paths are under `/api/v1`. **Planned** rows are not implemented._

## 1. Summary

- **Backend:** 79 routes (method + path pairs, counted from the route decorators).
- **Frontend client:** 58 methods. 53 of them are called by a page.
- **Defined in the client but never called:** `provider`, `network`, `job`, `researchRuns`, `attachFinding`.
- **Biggest gaps:**
  - The research history list and rerun (no UI, so a report is lost to the user after reload even though it is persisted).
  - Label curation (blocks real model training).
  - Attaching findings to cases (Overview tells the user to do it, but there is no UI for it).
  - Provider history and consistency detail.

## 2. Current pages

Legend: **used** = called by the page today; **unused** = endpoint exists, no UI; **planned** = new in Milestones C/D.

### Monitor

| Page (file) | Endpoint | Method | Status |
|---|---|---|---|
| App shell (`App.tsx`) | `/health` | GET | used (connection badge) |
| Overview (`Overview.tsx`) | `/health` | GET | used |
| | `/dashboard/summary` | GET | used |
| | `/intelligence/status` | GET | used |
| Chains (`Chains.tsx`) | `/chains` | GET | used |
| | `/chains/{chain}/latest-block` | POST | used ("Fetch latest block") |
| | `/chains/{chain}/latest-block` | GET | unused |
| | `/chains/{chain}/network` | GET | unused (client `network`) |
| Providers (`Providers.tsx`) | `/providers` | GET | used |
| | `/providers/{id}` | PATCH | used (enable/disable) |
| | `/providers/{id}/health-check` | POST | used |
| | `/providers/{id}` | GET | unused (client `provider`) |
| | `/providers/status` | GET | unused |
| Provider intelligence (`ProviderIntel.tsx`, inside Providers) | `/providers/profiles` | GET | used |
| | `/providers/{id}/profile` | GET | used (embeds latest capability results and reliability state) |
| | `/providers/probe/preview` | GET | used |
| | `/providers/probe-runs` | POST, GET | used |
| | `/providers/probe-runs/{run_id}` | GET | unused |
| | `/providers/capabilities` | GET | unused (cross-provider matrix) |
| | `/providers/{id}/capabilities/history` | GET | unused |
| | `/providers/{id}/reliability` | GET | unused (only state shown via profile) |
| | `/providers/{id}/consistency` | GET | unused |

### Explore

| Page (file) | Endpoint | Method | Status |
|---|---|---|---|
| Wallets index (`Wallets.tsx`) | `/wallets?limit&offset` | GET | used |
| Wallet page (`WalletProfilePage`) | `/wallets/{chain}/{address}` | GET | used. **Returns 404 for an unstored wallet; this is the audit's console 404** (see `FRONTEND_ARCHITECTURE.md` §9) |
| | `/wallets/{chain}/{address}/transactions` | GET | used |
| | `/wallets/{chain}/{address}/transfers` | GET | used |
| | `/wallets/{chain}/{address}/balance-changes` | GET | used |
| | `/ingestion/wallet/{chain}/{address}` | POST | used ("Retrieve recent activity") |
| | `/ingestion/jobs?chain&address` | GET | used |
| | `/ingestion/jobs/{id}` | GET | unused (client `job`) |
| Wallet intelligence (`WalletIntel.tsx`, inside wallet page) | `/intelligence/wallets/{c}/{a}/profile` | GET | used (includes counterparties and contract interactions) |
| | `/intelligence/wallets/{c}/{a}/behaviour` | GET | used |
| | `/intelligence/analyze/{c}/{a}` | POST | used |
| | `/intelligence/anomalies?chain&address` | GET | used |
| | `/intelligence/verification/{c}/{tx}` | POST | used (per-transaction "Verify") |
| | `/intelligence/verification/{c}/wallet/{a}` | POST | used ("Corroborate recent") |
| | `/intelligence/verification/{c}/{tx}` | GET | unused |
| | `/intelligence/wallets/{c}/{a}/activity` | GET | unused |
| | `/intelligence/wallets/{c}/{a}/counterparties` | GET | unused (paged; the profile carries the first page) |
| | `/intelligence/anomalies/{finding_id}` | GET | unused |
| | `/intelligence/findings/{finding_id}/attach` | POST | unused (client `attachFinding`) |
| Flows (`CapitalFlows.tsx`) | `/intelligence/flows/{c}/{a}` | GET | used (needs `?chain&address`) |
| Graph (`Graph.tsx`) | `/intelligence/graph/{c}/{a}?depth` | GET | used (needs `?chain&address`) |

### Intelligence

| Page (file) | Endpoint | Method | Status |
|---|---|---|---|
| Research (`Research.tsx`), wallet tab | `/ml/classify/{c}/{a}` | POST | used |
| | `/ml/classifications/{c}/{a}` | GET | used |
| | `/research/config` | GET | used |
| | `/research/preview` | POST | used |
| | `/research/runs` | POST | used |
| | `/research/runs/{key}` | GET | used (polling while open) |
| | `/research/runs/{key}/cancel` | POST | used |
| | `/research/runs/{key}/report` | GET | used |
| | `/research/runs?chain&address` | GET | **unused** (client `researchRuns`): no history after reload |
| | `/research/runs/{key}/rerun` | POST | **unused** (no client method) |
| Research, models tab | `/ml/models` | GET | used |
| | `/ml/models/{name}/{version}` | GET | used |
| | `/ml/training-runs` | GET, POST | used |
| | `/ml/taxonomy` | GET | unused |
| | `/ml/classifications/{id}` | GET | unused |
| | `/ml/features/{c}/{a}` | GET | unused |
| | `/ml/labels` | GET, POST | **unused**: no label curation UI |
| | `/ml/labels/{id}/reviews` | POST | **unused** |
| Opportunities (`Opportunities.tsx`) | `/intelligence/opportunities` | GET | used |
| | `/intelligence/opportunities/refresh/{chain}` | POST | used |
| | `/intelligence/opportunities/{id}` | GET | unused |

### Cases and settings

| Page (file) | Endpoint | Method | Status |
|---|---|---|---|
| Investigations (`Investigations.tsx`) | `/investigations` | GET, POST | used |
| | `/investigations/{id}` | GET, PATCH | used |
| Watchlists (`Watchlists.tsx`) | `/watchlists` | GET, POST | used |
| | `/watchlists/{id}` | GET, DELETE | used |
| | `/watchlists/{id}/entries` | POST | used |
| | `/watchlists/{id}/entries/{entry_id}` | DELETE | used |
| | `/intelligence/watchlists/{id}/analyze` | POST | unused (batch analysis) |
| Settings (`Settings.tsx`) | `/settings` | GET | used |

## 3. Target mapping (Milestones B–D)

| Target view | Endpoints | New backend work |
|---|---|---|
| Global search | `GET /wallets/{c}/{a}/status` | **Planned** (ADR-031) |
| Wallet workspace header and Overview tab | status, `/intelligence/wallets/…/profile`, `/ml/classifications/…?limit=1`, `/research/runs?chain&address&limit=1` | status endpoint only |
| Activity tab | transactions, transfers, balance-changes, ingestion POST, `/ingestion/jobs`, `/ingestion/jobs/{id}` | none |
| Flows, Graph tabs | flows, graph | none |
| Classification tab | classify, classifications, `/ml/classifications/{id}`, `/ml/features/…`, `/ml/models/{n}/{v}`, `/ml/taxonomy` | none |
| Research tab, Research index | `/research/runs` (with and without wallet filter), POST runs | none |
| Run page `/research/runs/:key` | run, report, cancel, **rerun** | report traceability fields (feature digest, taxonomy version, as-of, dataset version), additive with a `report_version` bump |
| Cases tab and case page | `/investigations*`, `/watchlists*`, `/intelligence/findings/{id}/attach`, **case ↔ research link** | **Planned**: migration v6 `investigation_research_runs` + `POST/GET/DELETE /investigations/{id}/research-runs` (ADR-030) |
| Evidence tab | behaviour, anomalies, `/anomalies/{id}`, verification (GET and POST), counterparties (paged) | none |
| Label curation (Intelligence → Research → Labels) | `/ml/labels` GET/POST, `/ml/labels/{id}/reviews`, `/ml/taxonomy` | none; shows counts against the training gates (≥ 30 wallets, ≥ 5 positives and negatives per label) |
| Provider detail `/providers/:id` | profile, capabilities history, reliability, consistency, probe-run detail | none |
| Watchlists | `/intelligence/watchlists/{id}/analyze` | none |
| Any 404 | — | **Planned**: unmatched routes return code `route_not_found` (ADR-031) |

## 4. Client conventions (carry forward)

- All calls go through `request<T>()` in `client.ts`, which unwraps the error envelope `{error:{code,message,details}}` into `ApiError(status, code, message)`.
- **Rule from Milestone C onward:** pages branch on `error.code`, not on `error.status` alone. `not_found` means a missing record; `route_not_found` means a client/server mismatch and must render as an error.
- Path segments are always passed through `enc()`. Query values are encoded.
- `useApi(load, deps)` dependencies must be primitives. A tab loads its own data on mount.
