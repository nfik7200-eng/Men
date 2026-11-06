# Current State

_Last updated: 2026-11-06 (UI integration, Milestone A: specification only)_

## Phase
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

## Status by component (honest labels)

| Component | Status | Evidence |
|---|---|---|
| Stage 1 foundation and BIP 0.1 app (settings, security, storage, contracts, API, SQLite, dashboard) | Implemented and tested | Unchanged behaviour; existing tests pass |
| Provider registry: 16 entries, read-only method allow-list, host allow-lists, unknowns kept unknown | Implemented and tested | `tests/stage2/test_registry_and_rpc.py` |
| Provider lifecycle (registered → … → healthy/degraded/unavailable/disabled), enable/disable | Implemented and tested | Registry, router and API tests |
| Router: capability gating, identity verification, bounded retries with backoff and jitter, 429/Retry-After, unsupported-method memory, call budget, throttle | Implemented and tested (offline) | `tests/stage2/test_router.py` |
| HTTPS transport: IP pinning, DNS-vs-unsafe distinction, no redirects, size cap, proxy mode | Implemented and tested | Offline tests. Against a real allowed host (pypi.org, not an RPC provider) via proxy: TLS tunnel OK, a 301 redirect was refused, the size cap triggered |
| Ethereum adapter and parsers (blocks, tx, receipts, logs, ERC-20/721 Transfer, native value) | Implemented, tested offline. **Not tested against a real provider** | Fixtures in `tests/stage2/fakes.py` |
| Solana adapter and parsers (signatures, tx legacy/v0, fees, errors, System/SPL transfers, balance changes) | Implemented, tested offline. **Not tested against a real provider** | Same |
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
| UI integration Milestones B–E (grouped navigation, global search, wallet workspace, research history page, case ↔ research links, label curation UI, wallet status endpoint) | **Specified, not implemented** | `docs/FRONTEND_ARCHITECTURE.md` |
| Real-data intelligence output | **None yet**: every analysed record so far is an offline fixture (labelled "Demo data") | — |
| AI research, historical crawler, live followers, Parquet lake | Planned | — |

## Blockers
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
1. ADRs 001–031 (all Proposed). ADR-030/031 (wallet workspace and research ↔ case link; wallet status endpoint and `route_not_found`) are specified, not implemented. ADR-027 to ADR-029 (scikit-learn plus rules baseline; deterministic planner with LLM synthesis only; in-process bounded research runs) are implemented provisionally. ADR-025/026 (budgeted manual prober; reliability sample rules) are implemented provisionally. ADR-021 to ADR-024 (provider independence groups, on-demand SQLite intelligence, reproducible runs, research leads not recommendations) are implemented provisionally. Already implemented provisionally: ADR-004, ADR-014 to ADR-016, and ADR-017 to ADR-020 (TOML registry, stdlib transport, synchronous bounded ingestion, log-based Ethereum discovery).
2. Storage target beyond the ~5 GB prototype cap.
3. Free accounts: Alchemy, Infura, Chainstack, Helius, Etherscan, GitHub PAT, Google Cloud BigQuery sandbox.
4. Where Postgres (and later Redis) run, and with what resource limits.
5. Initial scopes: contracts, programs, tokens and wallets to watch first.
6. Alert channel (Stage 12).
7. Web-fetcher User-Agent contact string.
8. Remote LLM providers allowed, or local Ollama only.
9. UI: self-hosted IBM Plex fonts or system fonts (default); confirm transaction-hash search stays out of scope (`docs/FRONTEND_ARCHITECTURE.md` §12).

## Known limitations
- Ethereum wallet history is discovered only through Transfer logs in the last 2,000 blocks (configurable). Plain ETH transfers without logs, internal transfers and ERC-1155 are not covered.
- Solana history is bounded to the most recent signatures per run. Cursors continue older history on later runs.
- Every ingested record is `unverified` until corroborated, and corroboration cannot run until a second, independent provider is configured.
- Intelligence describes the *retrieved dataset* only. On Ethereum that misses plain ETH transfers without logs, so frequency and counterparty metrics undercount.
- Detector thresholds are documented defaults, not calibrated on labelled data. No USD values (no price source).
- Graph and flows are computed per request from SQLite within record limits (ADR-022). Parquet/DuckDB is justified only once the measured triggers in ADR-022 are hit.
- Ingestion is synchronous, one run at a time. A run can take tens of seconds on rate-limited public endpoints.
- `GET /chains/{chain}/latest-block` performs a network request and stores an observation. It is also exposed as POST, which the dashboard uses.
- No authentication. The API binds to 127.0.0.1 by default and must not be exposed.
- SQLite is single-host. It is fine for one local user but not for multiple concurrent worker processes (ADR-015).
- The disk guard measures the data directory on every write request. That is acceptable at prototype scale, but it should be cached once the lake grows.
- The frontend CSP is a `<meta>` tag. Vite's dev server sends no security headers; production static hosting must add them.
- `npm audit`: one moderate advisory (minimatch ReDoS) inside ESLint's dependency tree. It affects development tooling only, not the shipped bundle. The only "fix" downgrades ESLint to v4, so it was not applied.
- Unknown `BIP_*` environment variables are ignored, so a misspelled setting falls back to its default.
- EIP-55 checksums are not verified (no Keccak in stdlib); EVM addresses are lower-cased.
- Redaction patterns are heuristics; only exact registered secret values are guaranteed to be removed.
- Provider limits in `docs/PROVIDER_REGISTRY.md` are still search-derived (`verified_at: null`).
- Context7 and the GitHub plugin failed to connect in the authoring sessions; library behaviour was confirmed by running the installed versions (pydantic 2.14.2, pydantic-settings 2.16.2).
