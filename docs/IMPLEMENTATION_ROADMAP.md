# Implementation Roadmap (Deliverable I)

Version 0.1 (2026-11-03). Status: **Draft, awaiting approval.**

Each stage is small, independently testable, and ends with green checks recorded in `IMPLEMENTATION_LOG.md`. "Live" means touching real providers. Stages 1–3 need **no network and no accounts**.

## Five-stage improvement programme (owner instruction, 2026-11-13)

This programme runs on top of the existing build. Its stage numbers are **its own** and do not
renumber the original stages in the tables below.

| # | Stage | Scope | Status |
|---|---|---|---|
| 1 | **Intelligence Foundation and Control Plane** | Codebase audit, architecture alignment, provider abstractions, unified event contracts, ingestion state, provenance, coverage and resource controls | **Done offline (2026-11-13), uncommitted at the owner's instruction.** Audit found the provider registry, chain contracts, provenance, provider observations, health, budgets and finality handling already built. Added: BIP-owned checkpoints and coverage ranges (migration v16, ADR-041), a persisted-state circuit breaker (ADR-042), read-only status routes. See `IMPLEMENTATION_LOG.md` |
| 2 | Multi-chain Data Acquisition | Real Ethereum and Solana providers, historical and live workers, failover, deduplication, reorganization handling, gap recovery | **Done offline (2026-11-13), uncommitted at the owner's instruction.** Bounded backward backfill and gap recovery per chain, per-direction pause, history-floor handling with fallback to other providers, SPL and ERC-20 allowlist storage, per-provider daily usage and budgets, provider facts re-checked against official docs (ADR-043, ADR-044, migration v17). Fixture-tested only; no live provider was contacted in this stage. Not done: WebSocket streaming, BigQuery/Old Faithful sources, per-event multi-provider observations |
| 3 | Intelligence Memory and Autonomous Discovery | Compact historical memory, wallet profiles, relationship graphs, statistical detectors, discovery inbox, longer-horizon opportunity candidates | **Done offline (2026-11-13), uncommitted at the owner's instruction.** Entity registry and append-only fact history with evidence classes and reorg invalidation (ADR-045), candidate review lifecycle with required reasons and bounded relationship expansion (ADR-046), memory tab, review panels, run history and policies in the UI. Local database only; jobs off by default |
| 4 | Human Supervision and Machine Learning | Review centre, human-confirmed labels, active learning, training datasets, model evaluation, controlled retraining | Not started (reviewed entity labels exist, ADR-040) |
| 5 | Local AI, Opportunity Intelligence and Production | Ollama research agents, retrieval, opportunity analysis, reports, alerts, authentication, security hardening, production operations | Not started (auth, alerts and the deploy kit exist from earlier work; Ollama is not configured) |

## Progress

| Stage | Status | Evidence |
|---|---|---|
| 0 Blueprint | Done; ADRs still awaiting owner approval | Commit `c3d5251`, PR #2 |
| 1 Repository skeleton & core contracts | Done (tested foundation, no integrations) | 213 unit tests passing; ruff and pyright (strict) clean on Python 3.12 and 3.13; gitleaks clean. See `IMPLEMENTATION_LOG.md`, 2026-11-03 Stage 1 |
| 1b BIP 0.1 application foundation | Done (local prototype; no chain integrations) | Backend, SQLite, REST API, React dashboard; pytest, Vitest and a 15-check end-to-end run. See `IMPLEMENTATION_LOG.md`, 2026-11-03 Stage 1b |
| 2 Provider registry **+ read-only data engine** (expanded scope, Build Prompt 03) | Done offline; **live provider verification blocked** by the authoring network policy | Registry, router, Ethereum/Solana adapters, ingestion, API, UI; 357 pytest, 30 Vitest, 21/21 e2e (Phase B on offline fixtures). Live smoke: 0 of 2 keyless providers reachable from the sandbox |
| 3-intel (Build Prompt 04): wallet intelligence, cross-provider verification, graph, flows, detectors, research leads | Done offline; **corroboration never exercised live** (network blocked; one keyless provider per chain) | `bip/intel/`, migration v3, `/api/v1/intelligence/*`, dashboard pages; 404 pytest, 38 Vitest, 30/30 e2e. Pulls part of Stages 11–12 forward on SQLite (ADR-022). Methodology in `docs/INTELLIGENCE_METHODOLOGY.md` |
| 4 Capability prober + provider reliability (Build Prompt 05) | Done offline; **no live observation** (sandbox egress denies RPC hosts) | Budgeted manual prober, capability catalogue, migration v4, provider intelligence API and console, `make probe`; see `docs/PROVIDER_CAPABILITY_METHODOLOGY.md` |
| 5 ML wallet classification + AI research (Build Prompt 06) | Done offline. **No real model is trained (no real labels)**; the pipeline, the rules baseline and research are working and tested | `bip/ml/`, `bip/research/`, migration v5, `/api/v1/ml/*`, `/api/v1/research/*`, AI Research page. Pulls part of roadmap Stages 12 and 15 forward (ADR-027 to 029) |
| UI integration (owner instruction 2026-11-06, Milestones A–E) | **Milestone A done (specification only)**; B–E not started | `docs/FRONTEND_ARCHITECTURE.md`, `docs/FRONTEND_API_MAP.md`, ADR-030/031 (Proposed). Extends the Stage 1b dashboard. Pulls part of Stage 16 forward (Playwright e2e at mobile and desktop widths) on the existing React/Vite stack, not Next.js (ADR-016) |
| 3, 4, 6–8 (partially pulled forward into 2) | Partial | Router core, capability checks and scoped wallet ingestion exist. Still missing: circuit-breaker tuning, a multi-process quota store (3b), routing on probe evidence (5), historical range crawler and live followers (6–8) |
| 5 onward (others) | Not started | — |

**Re-sequencing (ADR-016).** The owner moved the API and dashboard forward to Stage 1b. Stages 13 and 16 now extend them: metrics, backup tooling, auth, graph views. They no longer create them. The rest of the order is unchanged.

## Stages

| # | Stage | Delivers | Depends on | Network? | Acceptance tests |
|---|---|---|---|---|---|
| 0 | **Blueprint** | Docs, ADRs, roadmap | — | research only | Docs consistent; owner review |
| 1 | **Repository skeleton & core contracts** | `pyproject` (uv), ruff/pyright/pytest config, package layout `bip/{config,core,security,contracts,storage}` (later-stage packages are created with their stage), typed settings with `SecretStr`, provenance envelope and natural-key models, URL/SSRF guard, logging with redaction, disk-guard utility, `.env.example`, pre-commit (ruff, gitleaks) | 0 | No | Unit tests: settings load/redaction, SSRF guard rejects private/metadata IPs and odd schemes, envelope validation, disk guard thresholds |
| 1b | **BIP 0.1 application foundation** | FastAPI app (`bip/api`), SQLite via SQLAlchemy Core (`bip/db`), REST endpoints for health, chains, dashboard, providers, wallets, watchlists and investigations; React/Vite dashboard (`frontend/`) | 1 | No | API/DB tests on temporary SQLite; frontend unit tests; end-to-end run with persistence across a restart |
| 2 | **Provider registry** | YAML schema + loader + validator; initial entries for the providers in `PROVIDER_REGISTRY.md` with `verified_at: null`; capability model and request-capability derivation | 1 | No | Invalid YAML rejected; capability gates computed correctly for `eth_getLogs` (range, pre-Merge), Solana `getTransaction` (version) |
| 3 | **Router core (offline)** | Token buckets (multi-window), cost accounting, circuit breaker, cooldown, scoring and selection, retry/backoff with jitter, validation hooks, verification policy, cache interface; fake-provider test harness | 2 | No | Deterministic simulations: failover only to capable providers; quota reserve honoured; breaker transitions; 429 cooldown; no request when no eligible provider |
| 3b | **Shared state** | Postgres schema (`registry`, `ingest`) via Alembic; optional Redis bucket backend | 3 | Local DB only (**owner approval to run Postgres/Redis**) | Multi-process bucket correctness test |
| 4 | **Capability prober** | Budgeted probe tool (≤50 req/provider): methods, max log range, history floor, archive, WS; writes observations | 3 | **Yes (tiny)** + free accounts (**owner action**) | Probe report saved; registry updated with `verified_at` |
| 5 | **Storage layer** | Parquet writer (atomic, content-addressed), manifests, layout, compaction, DuckDB reader; measure bytes/row | 1 | No | Crash-safety test (kill between write and checkpoint); manifest integrity |
| 6 | **ETH historical crawler (scoped)** | Range tasks, leases, validation (continuity, counts), coverage map, gap scanner, verification sampling | 3b, 5 | Yes | Backfill 10k blocks for one contract scope; kill/resume; gap injection detected |
| 7 | **ETH live follower** | Heads (WS + poll), reorg handling, finality promotion, catch-up | 6 | Yes | Simulated reorg tests; restart without gap |
| 8 | **Solana historical + live (scoped)** | Signature paging, getTransaction, version handling, skipped slots, finalized reconciliation, logsSubscribe | 3b, 5 | Yes | Scoped program history to end; restart-resume; skipped-slot classification |
| 9 | **Silver normalization** | Chain adapters, ABI registry, decoders (ERC-20/721/1155, SPL), dedup, DQ conflicts table | 6–8 | No | Golden-file tests from recorded fixtures |
| 10 | **BigQuery & indexer sources** | Dry-run-gated BigQuery adapter (bytes budget), Etherscan V2 contract creation, DefiLlama, The Graph | 3 | Yes (**GCP sandbox: owner action**) | Query refused above budget; results land in Bronze with provenance |
| 11 | **Gold features & graph** | Wallet activity, funding edges, transfers classification, whale/CEX flows, deployments enriched; entity clustering rules; NetworkX subgraph tools | 9 | No | Feature correctness on fixtures; each edge has evidence refs |
| 12 | **Detectors, scoring, alerts** | Anomaly baselines, opportunity scoring with decomposition, intelligence event outbox, alert channel (owner chooses) | 11 | No | Synthetic anomalies detected; labels verified/signal/hypothesis set correctly |
| 13 | **API + ops** | FastAPI read API, metrics (provider health, lag, coverage, disk), backup/restore scripts and drill | 12 | No | Restore drill passes |
| 14 | **Web intelligence** | Web fetcher (robots, per-domain limits, change detection), GitHub collector, governance and announcement feeds | 3, 5 | Yes | robots-disallowed URL never fetched; ETag reuse |
| 15 | **AI research agents** | LLM adapters (Ollama first), tool API, evidence bundles, verifier, reports | 12, 14 | Local LLM | Claims without evidence rejected; prompt-injection fixtures neutralized |
| 16 | **Dashboard** | Next.js/TS UI | 13 | No | Playwright e2e |
| 17 | **Chain expansion** | Base, Arbitrum (EVM adapter reuse), BNB | 6–9 | Yes | Adapter conformance suite |

**Order rationale:** Contracts and safety come first (1). The router comes before any ingestion, because every ingestion path goes through it (2–3). Storage correctness comes before any data (5). Scoped historical ingestion is built before live ingestion (6→7), because live ingestion reuses its validation and catch-up logic. Intelligence layers are built only once Silver data has provenance. AI comes last, because it consumes everything else.

**Gate items requiring owner action:** creating free accounts (Stage 4); approving local Postgres/Redis containers on the VPS or elsewhere (3b); a storage target beyond ~5 GB (before Stage 6 at scale); a GCP sandbox project (10).
