# Current State

_Last updated: 2026-11-03 (Stage 2)_

## Phase
**Stage 2: provider registry and read-only data engine.** Built and verified offline.
- **Live provider verification is blocked:** the authoring sandbox's network policy refuses connections to every RPC host. No real Ethereum or Solana provider has answered a BIP request yet.
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
| Live smoke command (`make live-smoke`) | Implemented. **Run: 0/2 keyless providers reachable** (DNS blocked direct; proxy CONNECT refused, 403) | 2026-11-03 run |
| Capital Flows, Opportunities, AI research | Planned (placeholders only) | — |
| Cross-provider verification, historical crawler, live followers, Parquet lake | Planned | — |

## Blockers
1. **Network policy (authoring sandbox):** `ethereum-rpc.publicnode.com`, `api.mainnet.solana.com` and other RPC hosts are denied. To verify live, run `make live-smoke` on a machine with internet access, or allow those hosts in the cloud environment's network settings.
2. **Credentials:** Alchemy, Infura, Ankr, QuickNode and Helius need free accounts and keys (`BIP_PROVIDER_*_URL`). None are configured. No accounts were created.
3. **PublicNode Solana hostname** is unconfirmed (sources disagree). It is left unconfigured.

## VPS (owner's Contabo, 8 vCPU / 24 GB RAM / ~145 GB disk, ~94% used at last measurement)
Untouched by BIP. No installs, downloads or deletions have been made. The default `BIP_MAX_DATA_BYTES` is 5 GiB, and the free-space floor is 5 GiB / 5%.

## Branches
- `claude/modest-fermi-0yklvj`: BIP work (this branch, draft PR #2).
- `claude/nifty-ptolemy-1emo3n`: separate Graphify developer tooling. Not merged, not part of BIP code.

## Awaiting owner approval
1. ADRs 001–020 (all Proposed). Already implemented provisionally: ADR-004, ADR-014 to ADR-016, and ADR-017 to ADR-020 (TOML registry, stdlib transport, synchronous bounded ingestion, log-based Ethereum discovery).
2. Storage target beyond the ~5 GB prototype cap.
3. Free accounts: Alchemy, Infura, Chainstack, Helius, Etherscan, GitHub PAT, Google Cloud BigQuery sandbox.
4. Where Postgres (and later Redis) run, and with what resource limits.
5. Initial scopes: contracts, programs, tokens and wallets to watch first.
6. Alert channel (Stage 12).
7. Web-fetcher User-Agent contact string.
8. Remote LLM providers allowed, or local Ollama only.

## Known limitations
- Ethereum wallet history is discovered only through Transfer logs in the last 2,000 blocks (configurable). Plain ETH transfers without logs, internal transfers and ERC-1155 are not covered.
- Solana history is bounded to the most recent signatures per run. Cursors continue older history on later runs.
- Every ingested record is `unverified`, because it comes from a single provider with no cross-check yet.
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
