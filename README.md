# BIP — Blockchain Intelligence Platform

BIP is a multi-chain blockchain intelligence platform. It gathers blockchain and public ecosystem data, stores it efficiently, finds relationships, flags emerging opportunities and risks, and uses AI agents selectively to investigate significant findings and produce evidence-backed reports.

It is **not** a block explorer, token tracker or dashboard. It is the data and intelligence infrastructure those things could be built on.

- **Initial chains:** Ethereum, Solana
- **Planned:** Base, BNB Chain, Arbitrum, other EVM and non-EVM networks (via chain adapters)
- **Cost target:** ~$0/month initially. Free tiers, public datasets, open-source tooling, self-managed compute.

## Status

**Stage 2, the read-only data engine.** BIP 0.1 can retrieve, validate, store and display Ethereum and Solana data from real JSON-RPC providers:
- a provider registry and router (identity verification, bounded retries, rate-limit handling);
- adapters, plus bounded wallet ingestion with provenance;
- API endpoints and the dashboard that show it.

Not yet proven:
- **No real provider has answered a request yet.** The authoring sandbox's network policy blocks RPC hosts, so every live result so far is a failure to connect. Run `make live-smoke` where those hosts are reachable.
- There is no authentication: local use only.
- Capital flows, opportunity detection and AI research are not implemented.

See [`CURRENT_STATE.md`](CURRENT_STATE.md).

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
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Developer setup, commands, configuration, security rules |

## Proposed stack (awaiting approval — see DECISIONS.md)

**In use:** Python 3.12, uv, pydantic, FastAPI, SQLAlchemy Core with SQLite, stdlib HTTPS JSON-RPC client, React, TypeScript, Vite.

**Planned:**
- Analytics: Apache Parquet (zstd), DuckDB, Polars.
- Operational storage at scale: PostgreSQL, Redis (from Stage 3b).
- Intelligence: NetworkX, scikit-learn, Ollama (optional).

Explicitly **not** used at the start: Kafka, Kubernetes, Elasticsearch, Neo4j, ClickHouse, self-hosted full/archive nodes.

## Setup

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), and Node.js 22+ with npm. No database server, Docker, API keys or network access is needed.

Install everything:

```bash
make install
```

Run the backend (terminal 1). It serves http://127.0.0.1:8000 and creates `var/data/operational/bip.sqlite3` on first start:

```bash
make api
```

Run the dashboard (terminal 2), then open http://127.0.0.1:5173:

```bash
make web
```

To pull real data, open a wallet in the dashboard and choose **Retrieve recent activity**. The keyless PublicNode (Ethereum) and Solana public endpoints need no setup; credentialed providers are configured with `BIP_PROVIDER_*_URL` (see `.env.example`). To check providers on your network:

```bash
make live-smoke
```

Run every offline check: backend lint, pyright and pytest; frontend ESLint, tsc and Vitest; then the end-to-end test:

```bash
make check-all
``` The individual commands, the package structure, every configuration variable and the security rules for later stages are in [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md).

Configuration comes from `BIP_*` environment variables. To use a local file, copy `.env.example` to `.env` (git-ignored) and run commands with `uv run --env-file .env …`. Settings never read `.env` implicitly.

## Operating constraints

- Respect every provider's quotas, terms and robots.txt.
- Do not install anything on, or delete anything from, the owner's existing VPS without explicit instruction.
- Attributing an address to a real-world identity requires independently verifiable evidence.
