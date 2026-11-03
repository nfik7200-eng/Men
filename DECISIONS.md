# Architecture Decision Records

Status values: **Proposed** (recommended, awaiting owner approval), **Accepted**, **Superseded**, **Rejected**.
All ADRs below were written on 2026-11-03 and are **Proposed**. None has been approved by the owner yet.

| ADR | Decision | Status |
|---|---|---|
| 001 | Scope-based acquisition, not a full-chain replica | Proposed |
| 002 | Lakehouse: Parquet+zstd lake (Bronze/Silver/Gold) + PostgreSQL for operational/high-value data | Proposed |
| 003 | DuckDB + Polars for processing; defer ClickHouse | Proposed |
| 004 | Python 3.12, uv, httpx/asyncio, pydantic; ruff, pyright, pytest | Proposed |
| 005 | All external access via the Provider Intelligence Router; registry-driven capabilities | Proposed |
| 006 | Postgres as job/lease queue (`SKIP LOCKED`) and event outbox; Redis only for rate-limit buckets and cache from Stage 3b | Proposed |
| 007 | Baseline provider set (see PROVIDER_REGISTRY §5) | Proposed |
| 008 | Finality-aware statuses and never-delete reorg handling | Proposed |
| 009 | AI out of ingestion path; read-only typed tool API; evidence-cited claims | Proposed |
| 010 | Thin httpx web fetcher; defer Scrapy/Playwright | Proposed |
| 011 | Relocatable storage root with disk guard; separate storage required before scale | Proposed |
| 012 | No self-hosted ETH/SOL nodes in Phase 1 | Proposed |
| 013 | Docker Compose with resource limits for deployment; no Kubernetes | Proposed |

---

## ADR-001 Scope-based acquisition
**Context.** A full ETH archive is about 2–3 TB on Reth/Erigon. Solana's full ledger is over 400 TB. Free quotas are millions of requests per month, and the VPS has about 8 GB free.
**Decision.** Collect by declarative scopes: contracts, programs, tokens, wallets, event signatures, depth. Chain-wide collection is limited to cheap Tier-0 datasets: headers and slot metadata, plus thresholded transfers.
**Consequences.** Coverage is honest and bounded. Chain-wide analytics, such as total holders for an arbitrary token, are unavailable unless that token is in scope, and reports must state this.
**Alternatives.** Full node (rejected for now: disk). BigQuery full export (rejected: scan quota, paid egress).

## ADR-002 Lakehouse storage
**Decision.** Immutable Bronze Parquet with a provenance envelope. Rebuildable Silver and Gold. PostgreSQL holds checkpoints, coverage, registry state, entities, aggregated edges and findings. Postgres never holds per-transaction chain-wide rows.
**Trade-off.** Parquet needs compaction and manifest discipline. In exchange, storage is cheap and compressed and scales to billions of rows.

## ADR-003 Processing engines
**Decision.** DuckDB (SQL over Parquet) and Polars. ClickHouse is deferred until there is a need for sub-second interactive queries over more than about 10B rows, or for concurrent multi-user analytics.

## ADR-004 Language and tooling
**Decision.** Python 3.12+, uv, httpx (async JSON-RPC), pydantic v2, ruff, pyright, pytest. Web3.py and solders are used only for encoding/decoding and types, not for transport.

## ADR-005 Router as the only egress for chain data
**Decision.** Every chain, dataset and indexer request goes through the router. The router applies capability gates, quotas, breakers, verification and accounting. Intelligence code depends only on chain-agnostic client interfaces.

## ADR-006 Queues
**Decision.** Postgres `FOR UPDATE SKIP LOCKED` for range tasks and leases, so they are transactional with checkpoints. A Postgres outbox table for intelligence events. Redis is introduced at Stage 3b only for shared token buckets and caching.
**Alternative.** Redis Streams for queues. This is deferred because it would split transactional state.

## ADR-007 Baseline providers
**Decision.** As listed in `docs/PROVIDER_REGISTRY.md` §5. It requires the owner to create free accounts. No paid plans.

## ADR-008 Reorgs
**Decision.** Records carry `observed → finalized | orphaned`. Orphaned data is retained. Historical ingestion runs only up to finalized.

## ADR-009 AI boundary
**Decision.** Detectors emit events. Agents consume curated evidence through a read-only typed tool API. Claims must cite evidence IDs. A verifier gates the output. Ollama comes first, through an adapter interface.

## ADR-010 Web fetching
**Decision.** Official APIs and feeds first. A thin httpx fetcher with robots/per-domain limits and change detection. Playwright is used only for allow-listed sites. Scrapy is deferred.

## ADR-011 Storage location
**Decision.** All data lives under a configurable `BIP_DATA_ROOT` (local path or S3-compatible URI). There is a hard BIP byte quota and a free-space guard. The prototype stays at ≤ 5 GB on the VPS. Any larger target requires owner approval. Candidates: an attached volume, a second disk or home server running MinIO, or R2/B2 (paid beyond about 10 GB).

## ADR-012 No self-hosted nodes in Phase 1
**Decision.** No ETH or Solana nodes. Revisit when separate storage of 3 TB+ NVMe exists: a Reth/Erigon archive node would remove most RPC quota constraints for Ethereum.

## ADR-013 Deployment
**Decision.** Docker Compose with explicit CPU and memory limits so existing VPS workloads are protected. Kubernetes is not used.
