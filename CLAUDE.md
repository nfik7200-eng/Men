# CLAUDE.md — Permanent Engineering Instructions for BIP

BIP (Blockchain Intelligence Platform) is a multi-chain intelligence infrastructure. These instructions apply to every session working in this repository. They are binding unless the project owner explicitly overrides them.

## 1. Source of truth

Read these before making changes. If code and documents disagree, raise it; do not silently pick one.

| Document | Purpose |
|---|---|
| `docs/MASTER_SPECIFICATION.md` | What BIP is, capabilities, non-functional requirements |
| `docs/DATA_ACQUISITION_STRATEGY.md` | How data is obtained per chain and per source |
| `docs/PROVIDER_REGISTRY.md` | Candidate providers, verified limits, check dates |
| `docs/DATA_ARCHITECTURE.md` | Bronze/Silver/Gold/Intelligence, storage, router, ingestion design |
| `docs/SECURITY_ARCHITECTURE.md` | Security requirements |
| `docs/IMPLEMENTATION_ROADMAP.md` | Staged build order |
| `docs/RISK_REGISTER.md` | Known risks and mitigations |
| `DECISIONS.md` | Architecture decisions (ADRs) and their approval status |
| `CURRENT_STATE.md` | What actually exists right now |
| `IMPLEMENTATION_LOG.md` | Completed work and test evidence |

After every task: update `CURRENT_STATE.md` and append to `IMPLEMENTATION_LOG.md`. New architectural choices go into `DECISIONS.md` with status `Proposed` until the owner approves.

## 2. Non-negotiable principles

1. Target ~$0/month. Never purchase, subscribe, provision paid infrastructure or create paid accounts without explicit owner approval.
2. Never depend on a single external provider. All external access goes through the Provider Intelligence Router.
3. Never assume a provider supports a method, network, or history depth until its capability record says so.
4. Respect quotas, ToS, rate limits, robots.txt. Never circumvent auth, CAPTCHAs, paywalls or access controls.
5. Ingestion must be resumable and idempotent. Interruptions must never lose committed progress.
6. Preserve provenance on every record (see `docs/DATA_ARCHITECTURE.md` §6).
7. A successful HTTP response is not proof of complete data. Validate.
8. Never convert missing data into zero. Missing is `NULL` + a coverage gap record.
9. Raw acquisition, normalization, analytics and intelligence are separate layers. Do not couple them.
10. AI is never in the critical path of ingestion. LLMs only see curated evidence bundles.
11. Never claim an address belongs to a real-world person without independently verifiable evidence.
12. Distinguish in all outputs: **verified observation**, **statistical signal**, **hypothesis**.
13. Avoid premature complexity: no Kafka, Kubernetes, Elasticsearch, Neo4j, or extra databases without an approved ADR.

## 3. Existing VPS constraint

The owner's Contabo VPS (8 vCPU, 24 GB RAM, ~145 GB root disk, ~94% used at last measurement) hosts other projects and AI models.
- Do not delete, move, or modify existing files, models, repositories, services or containers.
- Do not run installations, large downloads, or cleanup on it without an explicit instruction for that specific action.
- Assume only a few GB are free. All BIP storage paths are configurable and must be able to point at separate storage.

## 4. Engineering standards

- Language: Python 3.12+. Package/env manager: `uv` (proposed, ADR-004). Type hints everywhere; Pyright in strict mode for `bip/core` and `bip/router`.
- Lint/format: `ruff`. Tests: `pytest`. Tests for network code use recorded fixtures or local fakes — never live quota during CI.
- Small, independently testable modules. TDD for router, checkpointing, validation and reorg logic.
- Configuration via typed settings (pydantic-settings). Secrets only from environment / secret files; never committed; never logged.
- Every external call carries: provider id, method, cost estimate, request id; every response is validated before use.
- Chain-specific logic lives behind chain adapters (`bip/chains/<chain>/`). Intelligence code must not import provider SDKs.
- Prefer columnar files (Parquet + zstd) for bulk data; PostgreSQL only for high-value structured state.
- Commit messages: imperative, explain why. No model identifiers in commits, code or docs.

## 5. Working method

1. Read the relevant docs and `CURRENT_STATE.md`.
2. Confirm the task maps to a roadmap stage; if not, propose a roadmap change first.
3. Write tests first for logic with correctness risk.
4. Run checks (`ruff`, `pyright`, `pytest`) and record real output in `IMPLEMENTATION_LOG.md`.
5. Never claim something works without evidence. Never claim a file exists that was not written.
6. When a provider fact is used in code (limit, method support), it must come from the registry file, with `verified_at` and `source_url`.

## 6. Exploratory network usage

- Live provider probing only via the capability-probe tool (roadmap Stage 4), with a per-run request budget (default ≤ 50 requests per provider).
- No bulk downloads, node installs, or archive syncs without an approved ADR naming the storage target and size.

## 7. AI agent boundaries

- Agents use a typed, read-only tool API (query evidence, fetch entity profile, run approved analytical query templates). No raw SQL, no shell, no network egress except through approved tools.
- Every agent conclusion must cite evidence IDs. Unsupported claims are labelled hypotheses.
