# Implementation Roadmap (Deliverable I)

Version 0.1 (2026-11-03). Status: **Draft, awaiting approval.**

Each stage is small, independently testable, and ends with green checks recorded in `IMPLEMENTATION_LOG.md`. "Live" means touching real providers. Stages 1–3 need **no network and no accounts**.

| # | Stage | Delivers | Depends on | Network? | Acceptance tests |
|---|---|---|---|---|---|
| 0 | **Blueprint** (this task) | Docs, ADRs, roadmap | — | research only | Docs consistent; owner review |
| 1 | **Repository skeleton & core contracts** | `pyproject` (uv), ruff/pyright/pytest config, package layout `bip/{core,config,router,chains,storage,ingest,quality,process,intel}`, typed settings with `SecretStr`, provenance envelope and natural-key models, URL/SSRF guard, logging with redaction, disk-guard utility, `.env.example`, pre-commit (ruff, gitleaks) | 0 | No | Unit tests: settings load/redaction, SSRF guard rejects private/metadata IPs and odd schemes, envelope validation, disk guard thresholds |
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
