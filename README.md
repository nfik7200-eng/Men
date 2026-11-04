# BIP — Blockchain Intelligence Platform

BIP is a multi-chain blockchain intelligence platform. It gathers blockchain and public ecosystem data, stores it efficiently, finds relationships, flags emerging opportunities and risks, and uses AI agents selectively to investigate significant findings and produce evidence-backed reports.

It is **not** a block explorer, token tracker or dashboard. It is the data and intelligence infrastructure those things could be built on.

- **Initial chains:** Ethereum, Solana
- **Planned:** Base, BNB Chain, Arbitrum, other EVM and non-EVM networks (via chain adapters)
- **Cost target:** ~$0/month initially. Free tiers, public datasets, open-source tooling, self-managed compute.

## Status

**Stage 3, wallet intelligence.** BIP can retrieve, validate, store and display Ethereum and Solana data from JSON-RPC providers (Stage 2). It then analyses what it stored:
- scope-labelled wallet profiles and descriptive behaviour rules;
- deterministic, explainable anomaly detectors;
- capital flows and a bounded, evidence-backed relationship graph;
- research leads (never investment advice);
- cross-provider corroboration of evidence (corroborated / conflicted / invalidated / unverified).

See [`docs/INTELLIGENCE_METHODOLOGY.md`](docs/INTELLIGENCE_METHODOLOGY.md).

**Stage 5, ML classification and AI research.**
- **Classification:** a hierarchical, multi-label wallet classifier combines a deterministic, evidence-backed rules baseline with scikit-learn models. Models are trained only on provenance-backed labels and their thresholds are validated against measured precision. **No real model exists yet, because no real labels exist**; a labelled synthetic dataset exercises the pipeline.
- **Investigations:** bounded investigations use typed read-only tools, and record ML predictions, observations, AI interpretations and hypotheses as separately labelled claims.
- **LLM:** an optional local Ollama model only synthesizes over these claims.

See [`docs/ML_WALLET_CLASSIFICATION.md`](docs/ML_WALLET_CLASSIFICATION.md) and [`docs/AI_RESEARCH_METHODOLOGY.md`](docs/AI_RESEARCH_METHODOLOGY.md).

**Stage 4, provider intelligence.** A budgeted, manually started capability prober records, per provider and capability, what was tested and with what result. It keeps documented, configured, supported, unsupported, inconclusive and untested capabilities apart, and separates local network restrictions from provider failures. It also produces reliability observations with sample-size rules, and cross-provider consistency checks. See [`docs/PROVIDER_CAPABILITY_METHODOLOGY.md`](docs/PROVIDER_CAPABILITY_METHODOLOGY.md). To probe real providers from a machine with internet access:

```bash
make probe
```

Not yet proven:
- **No real provider has answered a request yet.** The authoring sandbox's network policy blocks RPC hosts, so every live result so far is a failure to connect. Run `make live-smoke` where those hosts are reachable.
- **No evidence has been corroborated live.** Corroboration needs two providers on independent infrastructure; only one keyless provider per chain is configured, and the sandbox cannot reach any.
- There is no authentication: local use only.
- AI research is not implemented. No prices or USD values exist.

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
| [docs/ML_WALLET_CLASSIFICATION.md](docs/ML_WALLET_CLASSIFICATION.md) | Taxonomy, features, labels, training, calibration, abstention |
| [docs/AI_RESEARCH_METHODOLOGY.md](docs/AI_RESEARCH_METHODOLOGY.md) | Orchestration, tools, claims, LLM safety, budgets |
| [docs/PROVIDER_CAPABILITY_METHODOLOGY.md](docs/PROVIDER_CAPABILITY_METHODOLOGY.md) | Capability probing, budgets, failure classification, reliability, consistency |
| [docs/INTELLIGENCE_METHODOLOGY.md](docs/INTELLIGENCE_METHODOLOGY.md) | How profiles, rules, detectors, flows, graphs, leads and verification are computed |

## Proposed stack (awaiting approval — see DECISIONS.md)

**In use:** Python 3.12, uv, pydantic, FastAPI, SQLAlchemy Core with SQLite, stdlib HTTPS JSON-RPC client, scikit-learn (CPU), React, TypeScript, Vite. Ollama is optional and local-only.

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
