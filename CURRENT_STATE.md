# Current State

_Last updated: 2026-11-03 (Stage 1)_

## Phase
**Stage 1 — Repository foundation & core engineering contracts: done.** BIP has a tested, offline software foundation. It is **not operational**: nothing connects to a blockchain, provider, database or AI model.

## What exists in code

| Component | Module | Status |
|---|---|---|
| Python project (uv, locked deps), ruff, pyright strict, pytest | `pyproject.toml`, `uv.lock`, `Makefile` | Implemented and tested |
| Typed settings with validation and readable, value-free errors | `bip/config/settings.py` | Implemented and tested |
| Secret references (env / `*_FILE`) and redaction | `bip/security/secrets.py` | Implemented and tested |
| Logging with mandatory redaction (JSON/text) | `bip/core/logging.py` | Implemented and tested |
| Outbound URL validation (first SSRF layer) | `bip/security/url_validation.py` | Implemented and tested. Connection-time IP pinning and redirect enforcement belong to the Stage 3 HTTP client and do not exist yet |
| Storage-root containment and no-overwrite file creation | `bip/storage/paths.py` | Implemented and tested |
| Disk guard (BIP quota, free-space floor and ratio) | `bip/storage/disk_guard.py` | Implemented and tested. Not yet called by any writer, because no writer exists |
| Chain registry, positions, identifiers, address refs | `bip/contracts/blockchain.py`, `identifiers.py` | Implemented and tested |
| Provenance contract | `bip/contracts/provenance.py` | Implemented and tested |
| Block, transaction, log, instruction, transfer, deployment contracts | `bip/contracts/*.py` | Implemented and tested as data shapes. No adapter produces them yet |
| Finding / claim / evidence / attribution contracts | `bip/contracts/findings.py` | Implemented and tested as data shapes. No detector or agent produces them yet |
| Pre-commit config (ruff, gitleaks, large-file guard) | `.pre-commit-config.yaml` | Implemented but **not run locally** (gitleaks is not installed in the authoring sandbox) |
| CI workflow (lint, format, pyright, pytest, gitleaks) | `.github/workflows/ci.yml` | Implemented. **Unverified until it runs on GitHub** |

Test evidence: see `IMPLEMENTATION_LOG.md` (2026-11-03, Stage 1).

## What does not exist yet
- Provider registry YAML, loader and capability model (Stage 2).
- Router, HTTP client, chain adapters, any network code (Stages 3–8).
- Parquet writer, manifests, compaction (Stage 5).
- PostgreSQL/Redis schemas or connections (Stage 3b; needs approval).
- Normalization, features, detectors, API, web fetcher, AI agents, dashboard (Stages 9–16).
- Provider accounts or API keys.
- Any ingested data.

## VPS (owner's Contabo, 8 vCPU / 24 GB RAM / ~145 GB disk, ~94% used at last measurement)
Untouched by BIP. No installs, downloads or deletions have been made. The default `BIP_MAX_DATA_BYTES` is 5 GiB, and the free-space floor is 5 GiB / 5%.

## Branches
- `claude/modest-fermi-0yklvj`: BIP work (this branch, draft PR #2).
- `claude/nifty-ptolemy-1emo3n`: separate Graphify developer tooling. Not merged, not part of BIP code.

## Awaiting owner approval
1. ADRs 001–014 (all Proposed). ADR-004 tooling and ADR-014 layout are already implemented provisionally.
2. Storage target beyond the ~5 GB prototype cap.
3. Free accounts: Alchemy, Infura, Chainstack, Helius, Etherscan, GitHub PAT, Google Cloud BigQuery sandbox.
4. Where Postgres (and later Redis) run, and with what resource limits.
5. Initial scopes: contracts, programs, tokens and wallets to watch first.
6. Alert channel (Stage 12).
7. Web-fetcher User-Agent contact string.
8. Remote LLM providers allowed, or local Ollama only.

## Known limitations
- Unknown `BIP_*` environment variables are ignored, so a misspelled setting falls back to its default.
- EIP-55 checksums are not verified (no Keccak in stdlib); EVM addresses are lower-cased.
- Redaction patterns are heuristics; only exact registered secret values are guaranteed to be removed.
- Provider limits in `docs/PROVIDER_REGISTRY.md` are still search-derived (`verified_at: null`).
- Context7 and the GitHub plugin failed to connect in the authoring sessions; library behaviour was confirmed by running the installed versions (pydantic 2.14.2, pydantic-settings 2.16.2).
