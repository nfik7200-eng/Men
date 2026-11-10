# Current State

_Last updated: 2026-11-08 (backend completion pass implemented and tested offline; for owner review)_

## Phase
**Backend completion pass (2026-11-08): implemented and verified offline. Stopped for owner review. Nothing is committed.**
- **Ingestion hardening** (Stage 1 and its independent review): Solana v1, token accounts, placeholder and partial-row repair with attempt limits, oversized-log splitting, hash-checked finality upgrades, **orphan marking and exclusion from all derived reads**, with analyses marked stale and signals and relationships invalidated on a reorg.
- **Discovery** over stored canonical data: ten detectors, explainable scores, deduplication and cooldown, expiry, in-app alerts, promotion to a watchlist or case, detector review statistics. It does not scan the chain (see Blocked).
- **Entities**: evidence-backed relationships (`observed`/`hypothesis` only), search, transaction, token and contract detail, case notes and hypotheses, case timeline.
- **Security**: authentication (`BIP_AUTH__MODE=required`), server-side sessions, CSRF, origin checks, roles, throttling, audit log, a refusal to start unauthenticated on a reachable address. **The bundled frontend has no sign-in screen**, so it runs only with `mode=disabled` on loopback.
- **Operations**: detailed health and RPC usage, API metrics, verified backups, an in-process scheduler (off by default), retention.
- **ML**: feature drift monitoring and detector review statistics. Models are still only rules plus a synthetic-trained demo model; no real reviewed labels exist.
- **API reference** generated from OpenAPI and guarded by a test: `docs/API_REFERENCE.md`.
- Migrations v7 to v14 (additive; v11 `pool_events`, v12 `token_stats` and `pool_reserves`, v13 `solana_discoveries`, v14 `entity_labels`, v15 `entity_labels.requested_by`). ADR-032 to ADR-038 (all Proposed).

Earlier in this pass: **Stage 1 ingestion correctness (2026-11-08)**
- Solana transaction versions legacy, 0 and 1 are requested and parsed. Previously, v1 transactions (live on mainnet since 2026-09-15) produced placeholder rows.
- Placeholder rows from earlier runs are re-fetched and replaced by a real parse (bounded per run).
- Solana token accounts (SPL Token and Token-2022) are discovered per wallet run and scanned under a cap. Coverage reports discovery status, counts and what remains uncovered.
- Ethereum: an oversized `eth_getLogs` response splits the window instead of aborting the run. Stored transactions are upgraded to `finalized` only after their block hash is confirmed canonical (at most 5 blocks per run). Mismatches are reported as `reorg_detected`.
- Left open by Stage 1 and **done afterwards**: orphan exclusion in intelligence, partial Ethereum row repair, stale-label fixes in listings. Still open: provisional (`observed`) rows are used by intelligence and labelled, not excluded; provider retry and rate tuning is unchanged; no live provider was contacted.

Earlier phase: **UI integration (2026-11-06/07): implemented and verified offline.** See the capability matrix below and `IMPLEMENTATION_LOG.md`.

Earlier in this phase:
**UI integration, Milestone A (2026-11-06): specification only. No code changed.**
- Produced `docs/FRONTEND_ARCHITECTURE.md` (design system, grouped navigation, global search, wallet workspace, research persistence and traceability, responsive and touch rules, states) and `docs/FRONTEND_API_MAP.md` (page → endpoint map, gaps).
- Investigated the audit's console 404. It is the expected `404 not_found` for an unstored wallet, handled by the UI. A latent defect was found: unmatched routes return the same code. A fix is proposed in ADR-031.
- Milestones B–E are not started.

Previous phase:
**Stage 5: ML wallet classification and AI research**, on top of Stage 4. Built and verified offline.
- **No real ML model is trained.** No real, provenance-backed labelled wallets exist, so training on stored labels honestly returns `insufficient_data`. Classification uses the deterministic `bip-rules` baseline, plus an `experimental_synthetic` model only where someone explicitly trained the labelled synthetic demo set (this is enabled only on the fixture backend).
- **No LLM is configured** (`BIP_AI__PROVIDER=none`). Investigations produce deterministic reports with that limitation stated. LLM synthesis is tested with a deterministic fake provider only.

Earlier phase:
**Stage 4: budgeted provider capability prober and reliability intelligence**, on top of Stage 3 (wallet intelligence, cross-provider verification, relationship graphs). Built and verified offline.
- **No live probe has been run.** On 2026-11-04 the proxy status showed the same egress policy that refused CONNECT to the RPC hosts on 2026-11-03, so, as instructed, no identical failing request was repeated. Every provider capability is therefore `not_tested` in a real database. Fixture runs exist only on the offline fixture backend and are labelled.
- **Live provider verification is still blocked.** The bounded live retry on 2026-11-03 at 17:25Z reached 0 of 2 keyless providers (DNS fails direct; the proxy refuses CONNECT with 403). No real Ethereum or Solana provider has answered a BIP request yet.
- **No evidence has been corroborated live.** Corroboration needs two providers on independent infrastructure. Only one keyless provider per chain is configured, so a corroboration request reports `could_not_run`.
- BIP is a local prototype with **no authentication**.

## Capability matrix (2026-11-07)

Statuses:
- **Complete and verified:** implemented, with automated tests and a real-browser or real-process check.
- **Implemented, needs verification:** works offline, never exercised against real external systems.
- **Partial:** some parts exist.
- **Not implemented.**
- **Blocked:** needs an external dependency.

| Capability | Status | Evidence / blocker |
|---|---|---|
| Frontend styling in `vite` dev (the phone's unstyled-page bug) | Complete and verified | Root cause: the CSP blocked Vite's inline dev styles. Per-command CSP (`src/csp.ts`), Chromium check |
| Backend-offline handling (proxy 502s) | Complete and verified | `client.test.ts`; single banner; Chromium with no backend |
| Grouped navigation, skip link, titles, focus on navigation | Complete and verified | `App.test.tsx`, e2e |
| Global wallet search (Ethereum and Solana) | Complete and verified | `search.test.ts`, `WalletSearch.test.tsx`, e2e |
| Responsive layout: Galaxy S24 FE 412x915 touch and 1360x900 | Complete and verified | `e2e/layout.mjs` (all routes, seeded data): no overflow, targets ≥ 44/32 px |
| Wallet workspace (8 tabs) driven by `/wallets/{c}/{a}/status` | Complete and verified | `workspace.test.tsx`, `test_wallet_status.py`, e2e |
| `route_not_found` vs `not_found` | Complete and verified | `test_wallet_status.py`, `App.test.tsx` |
| Research history and run page with report provenance (report v1.1.0) | Complete and verified | `runpage.test.tsx`, `test_research.py`, e2e incl. backend restart |
| Research runs linked to cases (migration v6, additive) | Complete and verified | `test_case_research_links.py` (v5→v6 with data), e2e |
| Finding → case attachment from the Evidence tab | Complete and verified | `task10.test.tsx` (the endpoint existed in Stage 3) |
| Label curation and training-gate progress | Complete and verified | `test_ml.py`, `task10.test.tsx` |
| Provider detail with capability history | Complete and verified | `task10.test.tsx`, layout |
| Watchlist filter and batch analysis | Complete and verified | `watchlists.test.tsx`, real-backend click |
| Backend without scikit-learn (Android path) | Implemented, needs verification on the device | `test_ml_optional.py` (subprocess with ML imports blocked), a venv built with `--no-group ml --no-dev`. **Not yet run on the owner's ARM64 proot** |
| Ethereum and Solana adapters, ingestion, provenance, idempotency | Implemented, needs verification | Offline fakes only. **Blocked:** the sandbox egress policy denies RPC hosts |
| Cross-provider corroboration | Blocked | Needs a second independent provider per chain (free account, owner approval) |
| Capability prober / live provider evidence | Blocked | Network policy; run `make probe` from an allowed network |
| Wallet intelligence, flows, graph, detectors, research leads | Implemented, needs verification | Offline fixtures only; no real-data output yet |
| ML classification pipeline | Partial | Pipeline, rules baseline and synthetic demo work. **No real model** (no real labels) |
| AI research with an LLM | Implemented, needs verification | Fake LLM only; no Ollama configured here |
| Authentication / multi-user | Not implemented | Local prototype (documented) |
| Graph list-view alternative, sticky table headers | Not implemented | Deferred (see `docs/FRONTEND_ARCHITECTURE.md` status) |
| Transaction-hash search | Not implemented | Recognised and declined in the UI |
| Production deployment, PostgreSQL | Not implemented | ADR-gated; not requested yet |

## Status by component (honest labels)

| Component | Status | Evidence |
|---|---|---|
| Stage 1 foundation and BIP 0.1 app (settings, security, storage, contracts, API, SQLite, dashboard) | Implemented and tested | Unchanged behaviour; existing tests pass |
| Provider registry: 16 entries, read-only method allow-list, host allow-lists, unknowns kept unknown | Implemented and tested | `tests/stage2/test_registry_and_rpc.py` |
| Provider lifecycle (registered → … → healthy/degraded/unavailable/disabled), enable/disable | Implemented and tested | Registry, router and API tests |
| Router: capability gating, identity verification, bounded retries with backoff and jitter, 429/Retry-After, unsupported-method memory, call budget, throttle | Implemented and tested (offline) | `tests/stage2/test_router.py` |
| HTTPS transport: IP pinning, DNS-vs-unsafe distinction, no redirects, size cap, proxy mode | Implemented and tested | Offline tests. Against a real allowed host (pypi.org, not an RPC provider) via proxy: TLS tunnel OK, a 301 redirect was refused, the size cap triggered |
| Ethereum adapter and parsers (blocks, tx, receipts, logs, ERC-20/721 Transfer, native value) | Implemented, tested offline. **Not tested against a real provider** | Fixtures in `tests/stage2/fakes.py` |
| Solana adapter and parsers (signatures, tx legacy/v0/v1, fees, errors, System/SPL transfers, balance changes, token accounts, placeholder repair) | Implemented, tested offline. **Not tested against a real provider** | `tests/stage2/test_stage1_ingestion.py`, `tests/stage2/test_parsers.py` |
| Wallet ingestion (bounded, resumable, idempotent, provenance, raw payloads, errors recorded) | Implemented and tested (offline) | `tests/stage2/test_ingestion_api.py` |
| Migration v2 (in place, preserves v1 data) | Implemented and tested | v1 fixture upgrade test |
| API: providers, health check, chain network and latest block, wallet transactions/transfers/balance changes, ingestion jobs | Implemented and tested (offline transport) | API tests |
| Dashboard: Providers (health check, details, enable/disable), Chains (fetch head), Wallet retrieval and observations, Overview metrics | Implemented and tested | Vitest, plus e2e Phase B on fixture data |
| Live smoke command (`make live-smoke`) | Implemented. **Runs: 0/2 keyless providers reachable** (DNS blocked direct; proxy CONNECT refused, 403) | 2026-11-03 runs, latest 16:59 UTC |
| Migration v3 (provider observations, evidence verifications, analysis runs, anomaly observations, research leads, `transactions.created_contract`) | Implemented and tested (additive, idempotent, v1 data preserved) | `tests/stage3/test_findings_opportunities.py` |
| Cross-provider corroboration (infrastructure groups, corroborated / conflicted / invalidated / unverified, per transaction) | Implemented, tested offline with a second fake provider. **Never run live** | `tests/stage3/test_intel_api.py` |
| Wallet profile (scope-labelled), behaviour rules, anomaly detectors (5), flows, bounded graph, research leads, findings | Implemented and tested on synthetic data and fake-RPC ingestion | `tests/stage3/test_analytics.py`, API tests |
| Dashboard: Wallet intelligence sections, Capital Flows, Relationship Graph (SVG: zoom, pan, details), Opportunities, Overview verification metrics, demo-data banner | Implemented and tested | Vitest, e2e Phase B on offline fixtures |
| Migration v4 (probe runs, capability observations, reliability snapshots, consistency checks) | Implemented and tested (additive; v3 evidence survives) | `tests/stage4/test_prober.py` |
| Capability prober: catalogue (Ethereum 12, Solana 14 capabilities), server-enforced budgets, environment/provider failure classification, historical coverage labels | Implemented, tested offline. **Never run against a real provider** | `tests/stage4/test_prober.py` |
| Provider reliability (sample-size rules) and cross-provider consistency (like-for-like, independence-aware) | Implemented and tested | Same |
| Provider intelligence console (profiles, capability matrix, preview, bounded run, results, history, reliability) | Implemented and tested | Vitest, e2e Phase B |
| `make probe` (opt-in live probe CLI) | Implemented; not run here (network policy) | — |
| Migration v5 (features, labels, reviews, model registry, training and evaluation, classifications, research runs, plans, tool calls, claims, reports, model usage) | Implemented and tested (additive) | `tests/stage5/` |
| ML classification: taxonomy (10 account types, 25 roles), 27 versioned features (missing ≠ zero, as-of leakage control), rules baseline, scikit-learn training (out-of-time split, calibration, validated thresholds, Isolation Forest, HDBSCAN), abstention, contracts | Implemented and tested. **Only synthetic-trained models exist; no real-world metrics** | `tests/stage5/test_ml.py` |
| AI research: interpreter, fixed planner, 10 typed tools with permissions, 8 specialists, claims with statuses, LLM synthesis (validated and screened), budgets, cancellation, recovery, rerun, reports | Implemented and tested with a fake LLM. **No real LLM used** | `tests/stage5/test_research.py`, `test_pipeline.py` |
| AI Research page (classification, plan preview, progress and cancel, report, model registry and evaluation, AI provider status) | Implemented and tested | Vitest, e2e Phase B |
| UI integration Milestones B–D (grouped navigation, global search, wallet workspace, research history page, case ↔ research links, label curation UI, wallet status endpoint) | Implemented and tested | See capability matrix |
| Ethereum finality upgrade (hash-checked, bounded) and oversized log splitting | Implemented and tested offline. Downstream intelligence does **not** yet filter provisional events | `tests/stage2/test_stage1_ingestion.py` |
| Orphan handling: marking, exclusion from intelligence, invalidation of analyses, signals and relationships | Implemented and tested offline. Trusts one provider's canonical hash at a finalized height | `tests/stage2/test_stage1_review.py`, `tests/discovery/test_pipeline.py`, `tests/entities/test_relationships.py` |
| Discovery: 10 detectors, scoring, dedupe, cooldown, expiry, alerts, promotion, review statistics (migration v7) | Implemented and tested. Works over stored data only; **no chain-wide collector** | `tests/discovery/` |
| Entity relationships, search, transaction/token/contract detail, case notes and timeline (migration v10) | Implemented and tested | `tests/entities/` |
| Authentication, sessions, CSRF, roles, throttling, audit log (migration v8) | Implemented and tested, **including the frontend sign-in** (in-memory CSRF token, role-aware controls, 116 browser checks at phone, tablet and desktop sizes) | `tests/security/` |
| Health, usage, metrics, backups, scheduler, retention (migration v9) | Implemented and tested. Not measured on the VPS | `tests/ops/`, `docs/OPERATIONS.md` |
| VPS deployment kit (`deploy/`, `python -m bip.tools.preflight`): not applied to any host | Implemented; pre-flight tested; the kit itself is unexercised on a real host | `tests/unit/test_preflight.py` |
| Dashboards: Signals (feed, filters, detail with explanation, evidence, provenance and attribution, dismiss and promote), Alerts (with unread badge and rules), Coverage (both collectors, gaps, recognised programs, detector catalogue, admin-only "Run once" behind a confirm dialog), Attribution (propose, review, approved labels), System (health, jobs, backups) | Implemented; 238 unit tests and 217 browser checks at phone, tablet and desktop sizes. **Populated signal and alert states are covered by unit tests with SYNTHETIC data only**; the browser suite shows honest empty states there | `frontend/src/pages/*.test.tsx`, `frontend/e2e/smoke.mjs` |
| Reviewed entity labels (migration v14, `/entities/labels*`, attribution on signals) | Implemented and tested offline with synthetic data | `tests/entities/test_labels.py` |
| Solana finalized-slot collector (migration v13, detector `solana_new_entities`, `POST /discovery/collect/solana`, job `solana_scan` off by default) | Implemented and tested offline with synthetic fixtures | `tests/collector/test_solana.py` |
| Collector feeds (migration v12: `token_stats`, `pool_reserves`; detectors `new_token_activity`, `pool_reserve_change`, `holder_concentration`) | Implemented and tested offline with synthetic fixtures | `tests/collector/test_events.py` |
| Bounded Ethereum block collector: contract creations, large native transfers and Uniswap v2/v3-style pool-creation events (migration v11, detector `new_liquidity_pool`) from finalized blocks, no supplied address needed. **Off by default; never run live**, tested against fakes only | Implemented and tested offline | `tests/collector/`, ADR-038 |
| ML feature drift monitor | Implemented and tested on simulated data. Needs real model and feature history to mean anything | `tests/stage5/test_drift.py` |
| Real-data intelligence output | **None yet**: every analysed record so far is an offline fixture (labelled "Demo data") | — |
| AI research, historical crawler, live followers, Parquet lake | Planned | — |

## BLOCKED register (goal items not built, and why)

Nothing below is claimed as working. Each needs an owner decision; none was attempted live.

| Goal item | State | Blocked by | Owner decision needed |
|---|---|---|---|
| Ethereum discovery without addresses | **Partial**: creations, large native transfers, v2/v3-style pool creation (finalized blocks, bounded, off by default) | Never run live | Approve live provider use and quota |
| Solana discovery without addresses | **Built, bounded; live-checked on solana-public 2026-11-09** (142 slots, about 31,000 transactions, versions legacy/0/1 all parsed, 2,680+ failed transactions correctly skipped, no continuity breaks, 3 to 101 requests per invocation): `SolanaCollector` reads finalized slots (`getBlock`, versions 0 and 1) for new mints, upgradeable-loader program deployments, Raydium AMM v4 and Orca Whirlpools pool initialisations, and large SOL transfers. Gaps recorded; blockhash continuity checked | No mint, program or pool creation occurred in those 142 slots, so the creation layouts are verified against official sources and synthetic fixtures, not against a live occurrence; other pool programs, SPL transfers, holders and reserves are not covered | None |
| ERC-20 transfers chain-wide, new-token discovery | **Built (Ethereum, bounded)**: one topic-filtered `eth_getLogs` per collected range, counted per token; transfers of pooled tokens stored. **Measured live (publicnode-eth, 2026-11-09)**: about 285 to 750 ERC-20 `Transfer` logs per block, far under the 10,000 cap, so earlier notes saying volume exceeded provider limits were wrong. PublicNode refuses address-less `eth_getLogs` over more than one block, so the collector goes block by block (one request per block per feed) | Pool creations and `Sync` reserves were not seen in 20 blocks, so those two decoders are verified against sources and synthetic data only | None |
| Event streams (WebSocket/gRPC) | **Not built** | Provider-specific, may be paid | Provider choice |
| Liquidity/reserve changes, holder concentration | **Partial**: v2-style `Sync` reserves of known pools; holder concentration as observed net inflow of stored transfers (not balances). v3 pools and true balances not covered | `eth_call balanceOf`/indexer for real holdings | Data source for balances |
| Exchange / entity attribution | **Mechanism built**: reviewed entity labels (proposal, evidence, person-only approval, separate reviewer when auth is required), shown on signals only once approved. **No labels exist**: none were invented, so every entity is unattributed until a person approves one | Real labels and sources, supplied by analysts | Supply labels |
| Frontend sign-in and CSRF handling | **Built and verified**: sign-in gate, session restore after reload, CSRF on writes, 401/403/429 messages, role-aware controls, change password; e2e in `required` mode at three viewport sizes | Not deployed anywhere; lockout is indistinguishable from a wrong password in the UI (the API returns the same 401) | None |
| Classifier on real labels | **Not trained** | No reviewed labels exist | Supply labels |
| Live LLM synthesis / AI label proposals | **Built, not exercised**: `POST /entities/labels/suggest` grounds proposals in stored signals and drops anything uncited. Checked 2026-11-09: `BIP_AI__PROVIDER=none` and nothing listens on 127.0.0.1:11434, so no model was contacted | A reachable local Ollama and an installed model tag | Configure Ollama (optional) |
| Second-provider orphan confirmation | **Not built** | Needs a second approved provider | Provider choice |
| Commit, push, PR | **Not done** | Goal requires approval | Authorize |

## Blockers
000. **Partly built, rest BLOCKED:** discovery without supplied addresses now exists for one slice only: Ethereum contract creations and large native transfers read from finalized blocks (`ChainCollector`, admin endpoint `POST /discovery/collect/ethereum`, optional `chain_scan` job, off). New pools are now observed from factory events (event layout only; not proof of a DEX). **BLOCKED, needs an owner decision on data source, cost and storage:** a Solana collector, ERC-20 transfer activity chain-wide, liquidity/reserve changes, holder concentration and exchange flows. The collector is not a chain index and has not touched a live provider.
000. **Not built, needs a provider decision:** event streams (WebSocket/gRPC), Solana program deployment, liquidity/reserve detection. **BLOCKED.**
000. **Real reviewed labels** are still needed before any real classifier is trained; the LLM provider (Ollama) was not contacted.
00. **Real labelled wallets** (verified, corroborated or analyst-reviewed, with provenance) are needed before any real model can be trained. A local Ollama model is needed for AI synthesis, and must not change the owner's existing models.
0. **Live provider capability evidence** needs `make probe` from a machine (or environment) whose network allows the RPC hosts.
1. **Network policy (authoring sandbox):** `ethereum-rpc.publicnode.com`, `api.mainnet.solana.com` and other RPC hosts are denied. To verify live, run `make live-smoke` on a machine with internet access, or allow those hosts in the cloud environment's network settings.
2. **Credentials:** Alchemy, Infura, Ankr, QuickNode and Helius need free accounts and keys (`BIP_PROVIDER_*_URL`). None are configured. No accounts were created.
3. **Corroboration needs a second, independent provider per chain** (e.g. an Infura or Alchemy free account for Ethereum, Helius or Alchemy for Solana). This needs owner approval to create the account.
4. **PublicNode Solana hostname** is unconfirmed (sources disagree). It is left unconfigured.

## VPS (owner's Contabo, 8 vCPU / 24 GB RAM / ~145 GB disk, ~94% used at last measurement)
Untouched by BIP. No installs, downloads or deletions have been made. The default `BIP_MAX_DATA_BYTES` is 5 GiB, and the free-space floor is 5 GiB / 5%.

## Branches
- `claude/modest-fermi-0yklvj`: BIP work (this branch, draft PR #2).
- `claude/nifty-ptolemy-1emo3n`: separate Graphify developer tooling. Not merged, not part of BIP code.

## Awaiting owner approval
1. ADRs 001–031 (all Proposed). ADR-030/031 (wallet workspace and research ↔ case link; wallet status endpoint and `route_not_found`) are implemented provisionally. ADR-027 to ADR-029 (scikit-learn plus rules baseline; deterministic planner with LLM synthesis only; in-process bounded research runs) are implemented provisionally. ADR-025/026 (budgeted manual prober; reliability sample rules) are implemented provisionally. ADR-021 to ADR-024 (provider independence groups, on-demand SQLite intelligence, reproducible runs, research leads not recommendations) are implemented provisionally. Already implemented provisionally: ADR-004, ADR-014 to ADR-016, and ADR-017 to ADR-020 (TOML registry, stdlib transport, synchronous bounded ingestion, log-based Ethereum discovery).
2. Storage target beyond the ~5 GB prototype cap.
3. Free accounts: Alchemy, Infura, Chainstack, Helius, Etherscan, GitHub PAT, Google Cloud BigQuery sandbox.
4. Where Postgres (and later Redis) run, and with what resource limits.
5. Initial scopes: contracts, programs, tokens and wallets to watch first.
6. Alert channel (Stage 12).
7. Web-fetcher User-Agent contact string.
8. Remote LLM providers allowed, or local Ollama only.
9. UI: self-hosted IBM Plex fonts or system fonts (default); confirm transaction-hash search stays out of scope (`docs/FRONTEND_ARCHITECTURE.md` §12). Note: `/search` now matches stored transaction ids; confirm that is wanted.
10. ADR-032 to ADR-037 (all Proposed; implemented provisionally).
11. Switching to `BIP_AUTH__MODE=required` the reverse-proxy/VPN choice, and the first administrator.
12. Running the block collector against a live provider (`POST /discovery/collect/ethereum` or `BIP_WORKERS__COLLECTOR_ENABLED`): it spends provider quota (about one full-block call per block plus one receipt per candidate; default 20 blocks and 60 receipts per run). Enabling the scheduler (`BIP_WORKERS__ENABLED`), backups, and above all `watchlist_refresh`, which spends provider quota.
13. Whether to confirm an orphaned block with a second provider before orphaning, and whether to build a chain-wide collector (with its data source and cost).
14. A live check of Solana v1 handling, token-account discovery and `getTokenAccountsByOwner` support on a real provider.

## Known limitations
- Ethereum wallet history is discovered only through Transfer logs in the last 2,000 blocks (configurable). Plain ETH transfers without logs, internal transfers and ERC-1155 are not covered.
- Solana history is bounded to the most recent signatures per run. Cursors continue older history on later runs.
- Every ingested record is `unverified` until corroborated, and corroboration cannot run until a second, independent provider is configured.
- Intelligence describes the *retrieved dataset* only. On Ethereum that misses plain ETH transfers without logs, so frequency and counterparty metrics undercount.
- Detector thresholds are documented defaults, not calibrated on labelled data. No USD values (no price source).
- Graph and flows are computed per request from SQLite within record limits (ADR-022). Parquet/DuckDB is justified only once the measured triggers in ADR-022 are hit.
- Ingestion is synchronous, one run at a time. A run can take tens of seconds on rate-limited public endpoints.
- `GET /chains/{chain}/latest-block` performs a network request and stores an observation. It is also exposed as POST, which the dashboard uses.
- Authentication exists (`BIP_AUTH__MODE=required`) but is **off by default** and the bundled frontend cannot sign in. In `disabled` mode the API refuses to start on a non-loopback address. A cross-site request from a local browser could still call the unauthenticated local API.
- SQLite is single-host and single-writer. It is fine for one process (ADR-015, assessment in `docs/OPERATIONS.md`); it has not been load-tested on the VPS.
- The disk guard measures the data directory on every write request. That is acceptable at prototype scale, but it should be cached once the lake grows.
- The frontend CSP is a `<meta>` tag. Vite's dev server sends no security headers; production static hosting must add them.
- `npm audit`: one moderate advisory (minimatch ReDoS) inside ESLint's dependency tree. It affects development tooling only, not the shipped bundle. The only "fix" downgrades ESLint to v4, so it was not applied.
- Unknown `BIP_*` environment variables are ignored, so a misspelled setting falls back to its default.
- EIP-55 checksums are not verified (no Keccak in stdlib); EVM addresses are lower-cased.
- Redaction patterns are heuristics; only exact registered secret values are guaranteed to be removed.
- Provider limits in `docs/PROVIDER_REGISTRY.md` are still search-derived (`verified_at: null`).
- Context7 and the GitHub plugin failed to connect in the authoring sessions; library behaviour was confirmed by running the installed versions (pydantic 2.14.2, pydantic-settings 2.16.2).
- Discovery, relationships and signals describe **stored** data. A wallet BIP never ingested is invisible to them, and "new", "dormant" and "normal" mean *in the stored data*.
- Provisional (`observed`) rows are used by intelligence and labelled; only orphaned rows are excluded.
- ML features and classifications already stored are not recomputed after an orphan event; stored analyses are marked stale instead.
- Orphaning trusts a single provider's canonical hash. Dashboard counters still count orphaned transactions.
- Token-account history is bounded (5 accounts, 10 signatures each per run) and only the public Solana endpoint is declared to support `getTokenAccountsByOwner`.
- Resource figures in `IMPLEMENTATION_LOG.md` come from this sandbox, not from the production VPS.
