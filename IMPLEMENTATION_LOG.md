# Implementation Log

Entries are newest first. Each entry records what was done, how it was verified, and what was not done.

## 2026-11-04 — Stage 4 (Build Prompt 05): budgeted provider capability prober and reliability intelligence

**Network check first (no provider contacted).**
- The agent-proxy status on 2026-11-04 showed the same egress policy that refused CONNECT to the RPC hosts on 2026-11-03.
- As instructed, no live request was repeated just to reproduce that failure.
- **No live probe was run, and no real provider capability has been observed.**

**Done**
- **Read-only allow-list.**
  - Added `eth_getCode`, Solana `getVersion`, `getBlockHeight` and `getLatestBlockhash`.
  - `is_write_capable` pattern guard, checked at import and when a probe is sent.
  - Registry entries gained `documentation_basis` / `documentation_checked`.
- **`ProviderRouter.probe`.**
  - One request: pinned provider, no retries or failover, no identity side-call.
  - Shared throttle, with spacing ≥ `min_interval_s`.
  - Honours Retry-After cooldowns.
  - Rate limits are not recorded as lifecycle failures.
- **`bip/providers/`.**
  - `capabilities.py`: catalogue, Ethereum 12 and Solana 14 capabilities, including derived ones.
  - `prober.py`:
    - planning and validation;
    - budgets (ceilings 8/48/90 s/2/5 s/0/1 MB/≥ 1 s);
    - concurrency with a thread pool of at most 2;
    - classification: capability status separate from connectivity, environment restrictions explicit;
    - response validation;
    - stop rules;
    - live-derived samples only.
  - `intelligence.py`: run persistence, reliability (`reliability@1.0.0`), consistency checks, historical coverage labels.
- **Migration v4.** Additive: four tables.
- **API.** 9 routes under `/api/v1/providers/...` (profiles, profile, history, reliability, consistency, preview, start run, list runs, get run). They are registered before `/providers/{provider_id}`.
- **Dashboard.** A provider intelligence console on the Providers page: capability profiles, a per-provider capability matrix, reliability and historical coverage, a budget preview required before "Run bounded provider check", results, history, environment-restriction notes and the fixture banner.
- **CLI.** `make probe` (opt-in `BIP_LIVE_RPC=1`).
- **Fakes.** Extended to answer the new methods: a head-block transaction and log, synthesized history, `eth_getCode`, Solana version, block height and blockhash.
- **Docs.** `docs/PROVIDER_CAPABILITY_METHODOLOGY.md`, ADR-025/026; DEVELOPMENT, README, roadmap, PROVIDER_REGISTRY, SECURITY_ARCHITECTURE, RISK_REGISTER (R24–R25) and CURRENT_STATE updated.

**Verification (actual output)**
```
uv run ruff check . / format --check   -> All checks passed / all files formatted
uv run pyright (strict)                -> 0 errors, 0 warnings, 0 informations
uv run pytest -q                       -> 454 passed (Python 3.13)
venv312 python -m pytest -q            -> 454 passed (Python 3.12)
frontend: eslint, tsc                  -> clean
npx vitest run                         -> 44 passed
make e2e                               -> 38/38 (8 new provider-console checks on the offline fixture backend)
gitleaks dir .                         -> no leaks found
```
- Checked by screenshot (fixture backend):
  - the capability matrix opens under its row;
  - the never-probed providers show "No observations" rather than a reliability state.

**Live validation:** none. Providers reached live: 0. The cause is the environment's egress restriction, not any observed provider failure.

## 2026-11-03 — Stage 3 (Build Prompt 04): wallet intelligence, cross-provider verification, relationship graphs

**Live connectivity retry (one bounded attempt, as instructed).** `make live-smoke` at 2026-11-03T17:25:24Z reached 0 of 2 keyless providers. Direct mode fails DNS; proxy mode gets 403 on CONNECT. It was not retried again, no proxy bypass was attempted, and no alternative endpoint was used. **No live observation was retrieved in this stage, and no evidence was corroborated live.**

**Done**
- **Verification model.**
  - `VerificationStatus.INVALIDATED` added; display labels are corroborated / conflicted / invalidated / unverified.
  - Every RPC provider declares an `infrastructure` group; `registry.independent()` (ADR-021).
  - `bip/intel/verify.py` fetches the same transaction from up to two independent providers, compares normalized fields, and records `provider_observations` and `evidence_verifications`. Source records are never modified.
- **Migration v3.** Additive and idempotent: five tables plus `transactions.created_contract`. Ingestion now stores `receipt.contractAddress`.
- **`bip/intel/`:**
  - dataset loader (scoped, hashable);
  - profile (scope-labelled metrics);
  - 9 descriptive behaviour rules;
  - 5 anomaly detectors with published specs;
  - capital flows (canonical source: `transfers`; no USD);
  - bounded evidence-backed graph;
  - `rising_participation` research leads;
  - analysis runner with input-hash reuse, per-detector outcomes and deterministic findings.
- **API.** `/api/v1/intelligence/*` (16 routes). Every response carries `data_origin`, scope and verification; inputs are validated and bounded.
- **Dashboard:**
  - wallet intelligence sections: analysis, verification with per-transaction verify, metrics, behaviour, detectors, findings, counterparties, interactions, limitations;
  - Capital Flows replaces the placeholder: per asset, chart, counterparties, largest transfers, repeated paths;
  - new Relationship Graph page (SVG: zoom, pan, node/edge evidence details);
  - Opportunities replaces the placeholder;
  - Overview shows verification and intelligence metrics;
  - a "Demo data: offline fixtures" banner appears whenever `data_origin = offline_fixture` (set by `tests/stage2/serve_offline.py`).
- **Docs.** New `docs/INTELLIGENCE_METHODOLOGY.md`; ADR-021 to ADR-024 (Proposed); README, CURRENT_STATE, DEVELOPMENT, roadmap and RISK_REGISTER (R21–R23) updated.

**Independent review (code-review agent) and fixes.** Each fix has a regression test:
1. The graph double-counted rows re-read at depth ≥ 2. Rows are now deduplicated by row id.
2. Silent truncation when the per-level row limit was hit. Now reported as `row_limit_reached`.
3. "Not found" by two providers was marked `invalidated`. History depth is unknown, so it is now `unverified`.
4. An unparseable provider answer was shown as `conflict`. It is now a failed lookup (`could_not_run` when nothing usable was returned).
5. Recomputed findings kept stale status after corroboration. They are refreshed in place, keeping the id and investigation link.
6. Wallet findings were filtered in Python over the latest 2,000 rows. They are now filtered in SQL with `json_extract`.
7. Verification was unbounded under concurrency. It is now serialized (409 `verification_busy`), capped at 20 transactions / 60 s per wallet, and stops early when no independent provider exists.
8. Provider error text was stored and returned. Now only exception class and BIP error class are kept. A test confirms endpoint keys never appear in responses.

Also fixed:
- flows report untimed transfers excluded by a window;
- lead event counts count one event per transfer, not per participant;
- the new-contract detector uses an exact, case-sensitive match.

Not changed:
- The `data_origin` marker is per database, not per record. It persists with the database, so a fixture database stays labelled.

**Verification (actual output)**
```
uv run ruff check .              -> All checks passed!
uv run ruff format --check .     -> 125 files already formatted
uv run pyright (strict)          -> 0 errors, 0 warnings, 0 informations
uv run pytest -q                 -> 404 passed   (Python 3.13)
venv312 python -m pytest -q      -> 404 passed   (Python 3.12)
npm run lint / typecheck         -> clean
npx vitest run                   -> 38 passed
make e2e                         -> 30/30 checks passed (Phase A real backend; Phase B "[offline fixture]")
gitleaks dir .                   -> no leaks found
```
- The e2e Phase B wallet corroboration check reports "Checked 1 of 2: 1 could not run", which is correct: the fixture backend has a single provider per chain.
- Palette for graph node kinds and flow in/out validated with the dataviz validator (dark surface `#11171b`): 3 categorical slots, all checks pass; shape is the secondary encoding.

**Candidate and verification counts in this environment**
- Research leads on real data: **0** (no real data exists). Offline-fixture evaluation: `insufficient_data` (1 ingested wallet per chain; the rule needs 3).
- Live observations: 0. Corroborated: 0. Conflicted: 0. Unverified: every stored record. All stored records are offline fixtures or test data.

**Not done (by design)**
- No accounts, keys, paid services, Parquet, graph database or new dependencies.
- No AI in any path. No transaction signing, no writes to any chain.

## 2026-11-03 — Stage 2 follow-up: independent review fixes and live retry

Each of these review findings was first reproduced by a failing test in `tests/stage2/test_review_regressions.py`, then fixed.

1. **Solana cursor could skip signatures.** This happened when more new signatures than `limit` arrived, or a run was interrupted mid-batch. Now the cursor's `newest` moves only after a whole batch is ingested. A batch that fills the limit resets the range (no false contiguity), and older history advances per transaction.
2. **Ethereum cursor advanced before ingestion.** Windows were marked scanned even when transactions were truncated or failed. Now the cursor is computed after ingestion, in a `finally`, and never moves past the newest unfinished transaction's block.
3. **Failed transactions produced transfers.** Failed Solana transactions no longer decode transfer instructions (counted and flagged instead). Failed EVM transactions yield no native-value transfer.
4. **Out-of-range RPC values crashed instead of being rejected.** Timestamps are bounded (≤ 9999-12-31), positions ≤ 2^63−1 (SQLite INTEGER), and token amounts must be ASCII digits. `run()` now always finishes a run, recording `internal_error`. The API maps malformed provider data to 502 `upstream_invalid`.
5. **Migration v2 was not crash-safe.** pysqlite auto-commits DDL. Each step is now idempotent (`PRAGMA table_info` check, `checkfirst`, `IF NOT EXISTS`), and is tested by migrating a database with a half-applied v2.
- Hardening: stored ingestion error text is redacted, and the transport has a total read deadline (3× the timeout).

**Verification (actual output):**
- ruff clean; pyright strict 0 errors; pytest **357 passed**.
- Vitest 30 passed; build OK; e2e 21/21.
- **Live retry 2026-11-03T16:59Z: 0/2 providers reachable.** Direct mode fails DNS; proxy mode is refused with 403. This is unchanged: the sandbox network policy still denies RPC hosts.

## 2026-11-03 — Stage 2: Provider registry and read-only data engine

**Scope.**
- Provider registry and lifecycle, router, hardened transport, and Ethereum and Solana adapters.
- Bounded wallet ingestion, migration v2, API and dashboard integration.
- An opt-in live smoke tool.
- Built on the existing `bip/` package, API, SQLite and UI. Nothing was rewritten.

**Plugins and tools.**
- Context7 and the GitHub plugin failed to connect (proxy refusal), so no library docs came from Context7.
- Web search was used for provider endpoints, auth and limits.
- Pyright ran via the CLI.
- An independent code-review agent (`feature-dev:code-reviewer`) reviewed the stage.
- Superpowers skills were available, but no skill-driven workflow was invoked.

**Live network results (real, 2026-11-03).**
- `make live-smoke` reached **0/2** keyless providers:
  - direct mode: DNS resolution failed;
  - proxy mode: the proxy refused CONNECT (403).
- The sandbox network policy blocks RPC hosts.
- The proxy transport itself was verified against an allowed host (pypi.org): the TLS tunnel worked, a 301 was refused and the size cap triggered.
- **No real provider has answered a BIP request yet.**

**Defects found and fixed during the stage.**
- Registry validator raised `UnsafeURLError` instead of `ValueError` (bad URLs escaped as raw exceptions). Fixed.
- A 429 with a single provider could retry during its cooldown. It now waits inline at most 5 s, otherwise stops.
- DNS failure was mislabelled `unsafe_destination`, and proxy refusal was labelled generic `connection`. These are now `connection` ("DNS resolution failed") and `proxy`.
- A reorg detected through a receipt block-hash mismatch was labelled `parse_error`. It is now `reorg_detected`.
- Signature-only Solana rows were not linked to the wallet. Added the `wallet_activity` table.
- SPL transfers between token accounts owned by the wallet were not shown for it. These are now included through owner metadata.
- e2e: stale `vite preview` children survived stop (now killed by process group); an Overview/Chains selector race was fixed.

**Verification (actual output).**
```
uv run ruff check . / ruff format --check .  -> clean
uv run pyright (strict)                        -> 0 errors (Python 3.12 and 3.13)
uv run pytest -q                               -> 345 passed (offline; RPC via fake nodes)
npm run lint / npm run typecheck               -> clean
npx vitest run                                 -> 30 passed
npm run build                                  -> built
node e2e/smoke.mjs                             -> 21/21 (Phase A real backend; real-network health
                                                  check observed "Check failed"; Phase B offline fixture)
gitleaks (staged)                              -> no leaks found
make live-smoke                                -> 0/2 providers reachable (network policy)
```

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
- The end-to-end test is not in CI (it needs Chromium).

**CI on GitHub (`09bb27c`):** `checks`, `frontend` and `secret-scan` all succeeded (push and pull_request runs).

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
