# Implementation Log

Entries are newest first. Each entry records what was done, how it was verified, and what was not done.

## 2026-11-03 — Stage 1b: BIP 0.1 integrated application foundation

**Scope.** A locally runnable prototype: FastAPI backend, SQLite persistence, REST API and a React/TypeScript dashboard connected to it. No blockchain, provider or AI integration. No authentication. No VPS changes.

**Reused from Stage 1 (not rebuilt)**
- `Settings`, extended with `api` and `database` groups.
- `StorageRoot` and `Layer`, extended with `OPERATIONAL`: they locate the SQLite file.
- `DiskGuard` gates every API write.
- `AddressRef` / chain registry validate every chain and address.
- The `Finding` contract is the findings request body, and its evidence rules are enforced.
- `Provenance` is stored as JSON on chain rows.
- The redacting logger is used for all API errors.

**Added**
- `bip/db/`: SQLAlchemy Core schema v1 (12 tables, natural-key unique constraints, foreign keys with cascades, indexes); versioned migrator that refuses newer schemas; UTC datetime column type; repository with typed errors (404/409/422/503 mapping).
- Chain-data writers `store_transaction` / `store_transfer`. These are idempotent and update wallet first/last observation. They are not exposed through the API.
- `bip/providers/catalog.py`: 14 providers from `PROVIDER_REGISTRY.md`. Configuration is detected by variable presence only. Every provider is `unavailable`.
- `bip/api/`: app factory and lifespan; 19 route handlers under `/api/v1`. It also adds an error envelope, a body-size limit (Content-Length and streamed), security headers, a CORS exact-origin allow-list, and `python -m bip.api`.
- `frontend/`: Vite + React 19 + TypeScript (strict, `exactOptionalPropertyTypes`).
  - Nine navigation sections and a typed API client, with loading, empty, error and retry states.
  - Address validation that mirrors the backend.
  - A restrictive CSP.
  - Capital Flows and Opportunities are explicit "Not built yet" placeholders.
- Tooling and docs: Makefile targets (`api`, `web`, `web-*`, `e2e`, `check-all`), CI frontend job, `.env.example` additions, ADR-015 (SQLite), ADR-016 (stack moved forward), roadmap Stage 1b, `docs/DEVELOPMENT.md` (database, endpoints, error codes, security).

**Dependencies added**
- Runtime:
  - `fastapi` 0.162 (brings `starlette` 1.10): the API framework requested.
  - `uvicorn` 0.54: ASGI server.
  - `sqlalchemy` 2.1: parameterized, dialect-portable SQL (ADR-015).
- Dev: `httpx2` 2.16, which Starlette 1.10's TestClient requires (plain `httpx` emits a deprecation warning).
- Frontend runtime: `react`, `react-dom`, `react-router-dom`.
- Frontend dev: `vite`, `@vitejs/plugin-react`, `typescript`, `vitest`, `jsdom`, `@testing-library/*`, `eslint` with `typescript-eslint` and `eslint-plugin-react-hooks`.
- No Postgres, Redis, Docker or chain SDKs.

**Defects found and fixed during this stage**
- Pyright strict flagged protected `Row._asdict()` use, untyped SQLite hooks and `Iterator`-typed context managers. Switched to `.mappings()`, typed the hooks and used `Generator`.
- The React Hooks v7 linter flagged a ref write during render and a synchronous `setState` in an effect in `useApi`. The hook was rewritten so state changes only in async callbacks, and loading is derived from a request key.
- The end-to-end run found that `frame-ancestors` is ignored in a meta CSP. It was removed rather than leaving a no-op.
- **Independent review, both confirmed by failing tests first:**
  - A chunked oversize body returned 400, not 413, because FastAPI rewraps body-parser exceptions. The limit exception is now an `HTTPException(413)`.
  - The findings API accepted self-asserted `cross_verified` evidence for transactions never stored, which the UI would show as verified. Verified evidence must now be a stored chain record, otherwise 422.
- Also from the review: the SQLite URL is now built with `URL.create`, so special characters in the path cannot corrupt it.

**Verification (actual commands and output)**
```
$ uv run ruff check .                 -> All checks passed!
$ uv run ruff format --check .        -> all files formatted
$ uv run pyright                      -> 0 errors, 0 warnings, 0 informations
$ uv run pytest -q                    -> 272 passed (213 Stage 1 + 59 new: 49 API/DB, 10 settings)
$ npm run lint / npm run typecheck    -> clean
$ npx vitest run                      -> 26 passed (3 files)
$ npm run build                       -> built (JS 296 kB, 91 kB gzip)
$ node e2e/smoke.mjs                  -> 15/15 checks passed
```
The end-to-end run used the real backend on a temporary data root, the built UI through `vite preview` and headless Chromium. It covered:
- health through the proxy, and zero counts on an empty database;
- chains shown as not connected;
- watchlist create and add, with address normalization;
- duplicate rejection, and investigation create plus status change;
- the SQLite file under the data root, and persistence across a **backend restart**;
- dashboard counts;
- 422 and 404 wallet responses, no paths in settings, and CORS refusing a foreign origin;
- no horizontal overflow at 390 px, and no unexpected console errors.

**Not done (by design or blocked)**
- Authentication and authorization (documented as future work, API bound to loopback).
- Chain adapters and ingestion, so the database holds no chain data, and the UI and API say so.
- The end-to-end test is not in CI (it needs Chromium). The CI frontend job had not yet run on GitHub at the time of writing.

## 2026-11-03 — Stage 1: Repository foundation & core engineering contracts

**Scope.** Offline foundation only. No RPC, provider, database, LLM or frontend code. No network access at runtime. No VPS changes.

**Done**
- Project: `pyproject.toml` (hatchling build, `requires-python >=3.12`), `uv.lock`, `Makefile`, `.env.example`, `.gitignore` (ignores `.env`, `var/`, `data/`, `*.parquet`, `*.car`), `.pre-commit-config.yaml`, `.github/workflows/ci.yml`.
- `bip/config/settings.py`: `Settings` / `load_settings()`. `BIP_*` env vars and nested `__`. Environment, log level/format, data root, temp dir, byte-size quotas (`5GiB` syntax), free-space floors, enabled chains (comma list validated against the chain registry), router feature flags, optional `SecretStr` Postgres/Redis URLs. Production rules: absolute data root, JSON logs. Errors list variable names and reasons, never values.
- `bip/security/secrets.py`: `read_secret()` (`$NAME` or `$NAME_FILE`; both set is rejected), exact-value registry, pattern redaction (URL userinfo, bearer/basic tokens, sensitive `key=value`/JSON pairs, opaque URL path segments). `0x`-hex identifiers are left readable. Every pattern is bounded, and input over 256 KiB is truncated before redaction.
- `bip/core/logging.py`: JSON/text logging. A mandatory filter redacts message, args, `extra=` fields (values under sensitive key names are masked) and exception text.
- `bip/security/url_validation.py`: `validate_url()`, `check_ip()`, `validate_redirect()`, with limitations documented (DNS rebinding, redirects, pinning required in Stage 3).
- `bip/storage/paths.py`: `StorageRoot` (traversal, absolute, NUL and symlink-escape rejection; `create_exclusive()` uses `O_EXCL` and never overwrites). `bip/storage/disk_guard.py`: `DiskGuard` (BIP quota, free bytes, free ratio; fails safe when usage cannot be measured; never deletes).
- `bip/contracts/`: chain registry (`ethereum`, `solana`, `base`, `arbitrum`, `bnb`), `BlockPosition` vs `SlotPosition`, EVM hex and Solana base58 identifiers (decoded to exact byte length), `AddressRef` (no identity field), `Provenance`, `EvmBlock`/`SolanaBlock`, `EvmTransaction`/`SolanaTransaction`, `EvmLog`/`SolanaInstruction`, `TokenTransfer` (raw amounts, unknown decimals stay `None`, `unclassified` by default), `ContractDeployment`, and `Finding`/`Claim`/`EvidenceRef`/`Attribution` with evidence rules.
- Docs: `docs/DEVELOPMENT.md` (new), README setup, CURRENT_STATE, roadmap progress, ADR-014.

**Dependencies added**
- Runtime: `pydantic` (2.14.2), `pydantic-settings` (2.16.2). These were already chosen in ADR-004. `pydantic-settings` provides typed env loading and `NoDecode` for comma lists.
- Dev: `pytest` (9.2.0), `ruff` (0.17.3), `pyright` (1.1.414).
- The lockfile resolves 17 packages in total. No HTTP client, web3 or Solana SDK has been added yet.

**Defects found and fixed during this stage**
- Logging leaked values passed as `extra={"api_key": ...}`. Fixed by masking by key name.
- JSON-style `"password": "..."` was not redacted. Fixed in the key-value pattern.
- A substring match on `token` would have masked `token_address`. Changed to suffix matching.
- Two redaction regexes were super-linear on hostile input (2–45 s on 65–80 KB strings). The URL-userinfo scheme is now bounded, the URL-path rule is a linear parser, the key-prefix is bounded, and there is a 256 KiB input cap. Regression tests cover these cases.

**Independent review (code-review agent) — findings confirmed by running them, then fixed**
- Passwords containing `@` or `/` partly survived URL-userinfo redaction (`postgresql://user:p@ss@host` left `ss`). The pattern now ends at the last `@`. It may over-redact, which is the safe direction.
- Non-string `extra=` values (e.g. exceptions) reached JSON logs unredacted through `default=str`. `redact_value` now renders and redacts every non-scalar object.
- `https://[fec0::1]/` (deprecated IPv6 site-local) was accepted. `fec0::/10` is now blocked.
- Orphaned (reorged-out) evidence counted as verified. `EvidenceRef.is_verified` now excludes `finality=orphaned`.
- A parent path that is a file raised a raw `FileExistsError`. It now raises `StoragePathError`.
- Not changed (by design, documented): keys whose sensitive word is not at the end (`secret_key_base`, `password1`) are not pattern-masked. Unresolved hostnames such as `127.0.0.1.nip.io` pass when `resolve=False`, and connection-time pinning (Stage 3) must catch them.

**CI on GitHub (commit 663e949)**
- `checks` (ruff, pyright, pytest on Python 3.12) passed.
- `secret-scan` failed on a false positive: the synthetic `X-API-KEY` fixture in `tests/unit/test_secrets.py`. Reproduced locally with gitleaks 8.24.3. Fixed with a `.gitleaksignore` fingerprint for that historical commit, and the fixture is now assembled at runtime. The local scans `gitleaks git` (PR range), `gitleaks dir .` and `gitleaks git --staged` each report "no leaks found".
- CI re-run on `3342f86`: `checks` and `secret-scan` both succeeded (push and pull_request runs).

**Verification (actual commands and output)**
```
$ uv run ruff check .            -> All checks passed!
$ uv run ruff format --check .   -> 46 files already formatted
$ uv run pyright                 -> 0 errors, 0 warnings, 0 informations
$ uv run pytest -q               -> 213 passed
$ (Python 3.12.3 venv) pytest    -> 213 passed; pyright -> 0 errors
$ gitleaks dir . / git --staged  -> no leaks found
```
- `.env.example` was loaded through `uv run --env-file .env.example` and validates.
- Gitleaks 8.24.3 was run locally from a binary in the session scratchpad; nothing was installed in the project. The full `pre-commit` hook set was not run locally.

**Not done (by design)**
- No provider registry, router, HTTP client, adapters, Parquet writer, database, AI or UI.
- No installs on the VPS, no downloads beyond Python dev dependencies into the project `.venv`.

## 2026-11-03 — Stage 0: Blueprint

**Done**
- Replaced the placeholder README with the BIP overview.
- Created CLAUDE.md, CURRENT_STATE.md, DECISIONS.md (13 Proposed ADRs), this log.
- Created docs: MASTER_SPECIFICATION, DATA_ACQUISITION_STRATEGY, PROVIDER_REGISTRY, DATA_ARCHITECTURE, SECURITY_ARCHITECTURE, IMPLEMENTATION_ROADMAP, RISK_REGISTER.

**Research method**
- Web searches on 2026-11-03 for provider free tiers (Alchemy, Infura, dRPC, Ankr, QuickNode, Chainstack, Helius, PublicNode, Solana public RPC), datasets (BigQuery sandbox, crypto_ethereum, goog_blockchain, crypto_solana, Old Faithful, era1), indexers (Etherscan V2, The Graph, DefiLlama, Dune), GitHub API limits, EIP-4444 status, node disk sizes, object storage free tiers.
- Direct WebFetch of provider sites failed (DNS blocked by session network policy). All figures are graded `V-search` / `Conflict` / `3rd-party` / `Unknown` accordingly.

**Verification**
- Cross-reference check of stage numbers between docs (fixed mismatches between DATA_ARCHITECTURE/README and the roadmap).
- No code, so no tests were run.

**Not done (by design)**
- No installs, downloads, accounts, API calls to providers, or VPS changes.
