# BIP — Blockchain Intelligence Platform

BIP is a multi-chain blockchain intelligence platform. It gathers blockchain and public ecosystem data, stores it efficiently, finds relationships, flags emerging opportunities and risks, and uses AI agents selectively to investigate significant findings and produce evidence-backed reports.

It is **not** a block explorer, token tracker or dashboard. It is the data and intelligence infrastructure those things could be built on.

- **Initial chains:** Ethereum, Solana
- **Planned:** Base, BNB Chain, Arbitrum, other EVM and non-EVM networks (via chain adapters)
- **Cost target:** ~$0/month initially. Free tiers, public datasets, open-source tooling, self-managed compute.

## Status

**Phase: Foundation / blueprint.** No application code yet. See [`CURRENT_STATE.md`](CURRENT_STATE.md).

## Documentation map

| Document | Contents |
|---|---|
| [CLAUDE.md](CLAUDE.md) | Permanent engineering rules for AI-assisted development |
| [CURRENT_STATE.md](CURRENT_STATE.md) | What exists today |
| [IMPLEMENTATION_LOG.md](IMPLEMENTATION_LOG.md) | Completed work + verification evidence |
| [DECISIONS.md](DECISIONS.md) | Architecture Decision Records and approval status |
| [docs/MASTER_SPECIFICATION.md](docs/MASTER_SPECIFICATION.md) | Product definition (Deliverable A) |
| [docs/DATA_ACQUISITION_STRATEGY.md](docs/DATA_ACQUISITION_STRATEGY.md) | Acquisition strategy, ingestion workflows (B, E) |
| [docs/PROVIDER_REGISTRY.md](docs/PROVIDER_REGISTRY.md) | Provider comparison (C) |
| [docs/DATA_ARCHITECTURE.md](docs/DATA_ARCHITECTURE.md) | Router (D), storage & processing (F), scale & cost (G), diagram (H) |
| [docs/SECURITY_ARCHITECTURE.md](docs/SECURITY_ARCHITECTURE.md) | Security requirements |
| [docs/IMPLEMENTATION_ROADMAP.md](docs/IMPLEMENTATION_ROADMAP.md) | Staged build plan (I) |
| [docs/RISK_REGISTER.md](docs/RISK_REGISTER.md) | Risks and mitigations |

## Proposed stack (awaiting approval — see DECISIONS.md)

Python 3.12 · uv · httpx/asyncio · pydantic · PostgreSQL · Apache Parquet (zstd) · DuckDB · Polars · Redis (from Stage 3) · FastAPI (from Stage 13) · NetworkX · scikit-learn · Ollama (optional) · Next.js/TypeScript (later dashboard).

Explicitly **not** used at the start: Kafka, Kubernetes, Elasticsearch, Neo4j, ClickHouse, self-hosted full/archive nodes.

## Setup

There is nothing to install yet. Setup instructions will be added in roadmap Stage 1 (repository skeleton). When added, they will:
- never require paid services;
- read secrets only from environment variables or a git-ignored `.env`;
- store all data under a configurable `BIP_DATA_ROOT` so it can live on separate storage.

## Operating constraints

- Respect every provider's quotas, terms and robots.txt.
- Do not install anything on, or delete anything from, the owner's existing VPS without explicit instruction.
- Attributing an address to a real-world identity requires independently verifiable evidence.
