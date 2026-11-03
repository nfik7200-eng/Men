# Current State

_Last updated: 2026-11-03_

## Phase
**Stage 0 — Blueprint.** Documentation only. No application code, no infrastructure, no accounts, no data.

## What exists
- Permanent documentation set (see README documentation map).
- 13 Proposed ADRs in `DECISIONS.md`.
- Provider inventory with search-verified figures (direct fetch blocked in the authoring sandbox; see `docs/PROVIDER_REGISTRY.md` §0).

## What does not exist yet
- Any Python package, tests, CI, Docker files.
- Any provider accounts or API keys.
- Any databases (Postgres/Redis) or storage targets.
- Any ingested data.

## VPS (owner's Contabo, 8 vCPU / 24 GB RAM / ~145 GB disk, ~94% used at last measurement)
Untouched by BIP. No installs, downloads or deletions have been made.

## Awaiting owner approval
1. All ADRs 001–013 (especially 001 scope-based acquisition, 002 storage, 011 storage location).
2. Which storage target to use beyond the ~5 GB prototype cap (attached volume / second disk / object storage).
3. Creating free accounts: Alchemy, Infura, Chainstack, Helius, Etherscan, GitHub PAT, Google Cloud BigQuery sandbox.
4. Running Postgres (and later Redis) containers, and where (VPS with resource limits, or another host).
5. Initial scopes: which contracts, programs, tokens and wallets to watch first.
6. Alert channel (e.g. Telegram, email, Discord webhook) for Stage 12.
7. Web-fetcher User-Agent contact string.
8. Remote LLM providers: allowed or local-only (Ollama)?

## Known blockers / caveats
- Provider limits must be re-verified directly on official pages (`verified_at: null` until then).
- Context7 and GitHub MCP servers failed to connect in the authoring session; library-doc verification is pending for Stage 1.
