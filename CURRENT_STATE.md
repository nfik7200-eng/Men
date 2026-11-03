# Current State

_Last updated: 2026-11-03 (Stage 1b, BIP 0.1)_

## Phase
**Stage 1b — BIP 0.1 application foundation: done.**
- BIP is a locally runnable prototype: FastAPI backend, SQLite database, REST API and a connected React dashboard.
- Watchlists and investigations work end to end.
- **No chain, provider or AI integration exists.** Every chain shows "configured, no adapter" and zero data.
- **There is no authentication**: local use only.

## What exists in code

### Stage 1 foundation (unchanged, reused)
| Component | Module | Status |
|---|---|---|
| Typed settings (now with `api` and `database` groups) | `bip/config/settings.py` | Implemented and tested |
| Secrets and redaction; redacting logging | `bip/security/secrets.py`, `bip/core/logging.py` | Implemented and tested |
| URL validation (first SSRF layer) | `bip/security/url_validation.py` | Implemented and tested. Not used yet, because the API fetches no URLs |
| Storage root, disk guard | `bip/storage/` | Implemented and tested. Now gates every API write |
| Data contracts (chain records, provenance, findings) | `bip/contracts/` | Implemented and tested. Now used by the database layer and API validation |

### BIP 0.1 application
| Component | Module | Status |
|---|---|---|
| SQLite schema v1, versioned migrations, connectivity check | `bip/db/schema.py`, `bip/db/engine.py` | Implemented and tested |
| Repository: watchlists, investigations, findings with evidence, wallets, activity log | `bip/db/repository.py` | Implemented and tested |
| Chain-data writers (`store_transaction`, `store_transfer`, wallet observation) | `bip/db/repository.py` | Implemented and tested with synthetic fixtures. Nothing calls them in the app yet; ingestion (Stage 6+) will |
| Provider catalog | `bip/providers/catalog.py` | Implemented and tested as metadata. Every provider reports `unavailable` because no adapter exists |
| REST API `/api/v1`: health, chains, dashboard summary, providers, settings, wallets, watchlists, investigations, findings | `bip/api/` | Implemented and tested |
| API safety: CORS allow-list, body-size limit, error envelope, security headers, disk-guard write gating | `bip/api/` | Implemented and tested |
| Dashboard pages: Overview, Chains, Wallet Intelligence (search, validation, profile), Investigations, Providers, Watchlists, Settings | `frontend/src/pages/` | Implemented and tested |
| Capital Flows and Opportunities pages | `frontend/src/pages/` | Partially implemented: page structure and honest placeholders only. No engines exist |
| Typed API client, loading / empty / error / retry states | `frontend/src/api/`, `frontend/src/lib/useApi.ts` | Implemented and tested |
| End-to-end test (real backend + built UI + Chromium, restart persistence) | `frontend/e2e/smoke.mjs` | Implemented and tested locally. Not in CI (needs Chromium) |
| CI: backend checks, frontend lint/types/tests/build, gitleaks | `.github/workflows/ci.yml` | Frontend job not yet run on GitHub |

Test evidence: `IMPLEMENTATION_LOG.md` (2026-11-03, Stage 1b).

## What does not exist yet
- Authentication and authorization.
- Provider registry YAML and verified limits (Stage 2), the router and HTTP client (Stage 3), the capability prober (Stage 4).
- Ethereum and Solana adapters and ingestion (Stages 6–8). The database therefore holds no chain data.
- Parquet lake (Stage 5), normalization, features, detectors (Stages 9–12).
- Capital-flow graphs, opportunity scoring, AI agents (Stages 11–15).
- Provider accounts or API keys.

## VPS (owner's Contabo, 8 vCPU / 24 GB RAM / ~145 GB disk, ~94% used at last measurement)
Untouched by BIP. No installs, downloads or deletions have been made. The default `BIP_MAX_DATA_BYTES` is 5 GiB, and the free-space floor is 5 GiB / 5%.

## Branches
- `claude/modest-fermi-0yklvj`: BIP work (this branch, draft PR #2).
- `claude/nifty-ptolemy-1emo3n`: separate Graphify developer tooling. Not merged, not part of BIP code.

## Awaiting owner approval
1. ADRs 001–016 (all Proposed). ADR-004 tooling, ADR-014 layout, ADR-015 SQLite and ADR-016 application stack are already implemented provisionally.
2. Storage target beyond the ~5 GB prototype cap.
3. Free accounts: Alchemy, Infura, Chainstack, Helius, Etherscan, GitHub PAT, Google Cloud BigQuery sandbox.
4. Where Postgres (and later Redis) run, and with what resource limits.
5. Initial scopes: contracts, programs, tokens and wallets to watch first.
6. Alert channel (Stage 12).
7. Web-fetcher User-Agent contact string.
8. Remote LLM providers allowed, or local Ollama only.

## Known limitations
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
