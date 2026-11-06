# Development Guide

How to work on the BIP code base. Applies from roadmap Stage 1 onward.

## Requirements

- Python 3.12 or newer (CI runs 3.12; 3.13 also verified locally).
- [uv](https://docs.astral.sh/uv/) for environments and dependencies.
- Node.js 22+ and npm for the frontend.
- No database server, Docker, API keys or network access are needed to run the app, checks or tests. SQLite ships with Python.
- **ML runtime (optional):** numpy and scikit-learn are the `ml` dependency group. `uv sync` installs it by default. Without it (`uv sync --no-group ml`), BIP starts normally:
  - classification uses only the deterministic rules baseline, and says so in its limitations;
  - training returns `503 ml_unavailable`;
  - `GET /api/v1/ml/models` reports `runtime.available: false`.

  Rules output is never labelled as a model prediction.

## Android phone (Termux + Ubuntu via proot-distro)

The phone is a development and preview device. scikit-learn is not supported there, so run without the ML group. Full ML tests run in CI and on normal Linux.

**Not verified on a real device yet.** These steps were checked in a Linux container with an environment built using `--no-group ml --no-dev`. Real ARM64 proot remains to be confirmed by the owner.

1. **Use Ubuntu's own Python**, not a uv-managed or Termux (Android) interpreter. Earlier installs failed because uv picked an `aarch64-unknown-linux-android` interpreter and tried to build native wheels:
   ```bash
   cd /root/Men && git pull
   export UV_PYTHON_PREFERENCE=only-system
   uv sync --python /usr/bin/python3 --no-group ml --no-dev
   ```
   This needs no `.venv` deletion when the existing `.venv` already uses `/usr/bin/python3`. If `uv` reports a different interpreter, it recreates the environment. Your database lives under `BIP_DATA_ROOT`, not in `.venv`.
2. **Start the offline fixture backend** (deterministic fake RPC nodes; every page labels the data "Demo data: offline fixtures"):
   ```bash
   BIP_DATA_ROOT=$HOME/bip-fixture uv run --no-sync python -m tests.stage2.serve_offline 8000
   ```
   `--no-sync` stops `uv run` from re-adding the ML group. Port 8000 is the frontend's default proxy target.
3. **Start the frontend** in a second Termux session:
   ```bash
   cd /root/Men/frontend && npx vite
   ```
   Open `http://127.0.0.1:5173` in Chrome on the phone. The dev server binds to 127.0.0.1 only, so nothing is exposed to the network.
4. **To use the real (non-fixture) backend instead:**
   ```bash
   uv run --no-sync python -m bip.api
   ```
   It reaches public RPC providers only where the network allows.

If the backend is not running, the UI shows "Backend unreachable" (the Vite proxy's 502 is recognised) instead of a generic error.

## Commands

| Task | Command | Make target |
|---|---|---|
| Install locked dev environment | `uv sync --locked` | `make install` |
| Lint | `uv run ruff check .` | `make lint` (also checks formatting) |
| Format | `uv run ruff format .` | `make format` |
| Type check (strict) | `uv run pyright` | `make typecheck` |
| Unit tests | `uv run pytest` | `make test` |
| Everything | | `make check` |
| Pre-commit hooks (ruff, gitleaks, large-file guard) | `uvx pre-commit install`, then `uvx pre-commit run --all-files` | `make precommit` |
| Install frontend packages (locked) | `cd frontend && npm ci` | `make install-frontend` |
| Start the API (http://127.0.0.1:8000) | `uv run python -m bip.api` | `make api` |
| Start the frontend dev server (http://127.0.0.1:5173) | `cd frontend && npm run dev` | `make web` |
| Frontend lint / types / tests | `npm run lint`, `npm run typecheck`, `npm test` | `make web-lint web-typecheck web-test` |
| Production build of the frontend | `npm run build` | `make web-build` |
| End-to-end test (real backend + built UI + Chromium) | `npm run build && npm run e2e` | `make e2e` |
| Everything | | `make check-all` |
| **Live** RPC smoke test (real network, opt-in) | `BIP_LIVE_RPC=1 uv run python -m bip.tools.live_smoke` | `make live-smoke` |

**Run the app locally:** start `make api` in one terminal and `make web` in another, then open http://127.0.0.1:5173. The Vite dev server proxies `/api` to the backend, so the browser talks to one origin. Interactive API docs are at http://127.0.0.1:8000/api/v1/docs.

CI (`.github/workflows/ci.yml`) runs on pushes to `main` and `claude/**` branches and on pull requests:
- backend: lint, format check, pyright, pytest;
- frontend: ESLint, `tsc`, Vitest, build;
- a gitleaks scan.

The end-to-end test needs a Chromium that `require("playwright")` can find, so it currently runs locally only. It has two phases:
- **Phase A** runs against the real backend and real network. The provider health check there passes or fails according to your network, and the script prints the observed outcome.
- **Phase B** restarts the backend wired to the deterministic fake RPC nodes (`tests/stage2/serve_offline.py`) to exercise the wallet screens with data. Every Phase B check is labelled `[offline fixture]`.

### Test categories (never mix them up)

| Kind | Where | What it proves |
|---|---|---|
| Offline unit/integration | `tests/unit`, `tests/api`, `tests/stage2` (fake RPC nodes in `tests/stage2/fakes.py`) | Parsing, routing, ingestion, storage and API logic on deterministic data |
| Frontend | `frontend/src/**/*.test.tsx` (in-memory fake API) | UI states and interactions |
| End-to-end | `frontend/e2e/smoke.mjs` | Phase A: real backend and UI. Phase B: same UI on fixture data |
| **Live smoke** | `bip/tools/live_smoke.py` | That a specific real provider answered, identity-verified, at that time |

Only the live smoke test, or a passing health check in the app, is evidence that a provider works.

## Package structure

```
bip/
  config/settings.py        Typed settings (BIP_* env vars), load_settings()
  core/exceptions.py        BIPError hierarchy
  core/logging.py           JSON/text logging with a mandatory redaction filter
  security/secrets.py       Secret references (env / *_FILE), redaction
  security/url_validation.py  Outbound URL validation (first SSRF layer)
  storage/paths.py          StorageRoot: path containment, exclusive-create writes
  storage/disk_guard.py     DiskGuard: quota and free-space checks
  contracts/
    base.py                 ContractModel base, strict integer types (UInt256, NonNegInt)
    identifiers.py          EVM hex identifiers, Solana base58 identifiers, TxRef
    blockchain.py           Chain registry, ChainFamily, BlockPosition/SlotPosition, AddressRef
    provenance.py           Provenance, RawDataRef, verification/finality enums
    blocks.py               EvmBlock, SolanaBlock
    transactions.py         EvmTransaction, SolanaTransaction
    events.py               EvmLog, SolanaInstruction
    transfers.py            TokenTransfer, TokenStandard, TransferClassification
    contracts.py            ContractDeployment
    findings.py             Finding, Claim, EvidenceRef, Attribution
  db/
    schema.py               SQLAlchemy Core tables (schema version 5)
    engine.py               DB location, engine, versioned migrations, connectivity check
    repository.py           All reads/writes; maps failures to typed errors
    records.py              Read models (also used as API response bodies)
    types.py                UTCDateTime column type
  providers/                Registry (registry.toml: methods, hosts, infrastructure groups), lifecycle
  router/ rpc/ chains/ ingest/  Router, hardened transport, chain adapters, bounded wallet ingestion
  providers/capabilities.py prober.py intelligence.py  Stage 4 capability catalogue, budgeted prober, probe storage / reliability / consistency
  tools/probe.py            `make probe`: opt-in live run of the same prober (BIP_LIVE_RPC=1)
  ml/                       Stage 5 classification: taxonomy, features, rules, backend (sklearn), labels, training, service
  research/                 Stage 5 AI research: llm (providers), tools (typed, read-only), engine (plan, claims, report)
  intel/                    Stage 3 intelligence (stored data only; see INTELLIGENCE_METHODOLOGY.md)
    dataset.py              Load one wallet's stored records as a scoped, hashable dataset
    profile.py behaviour.py anomaly.py flows.py graph.py opportunities.py
    verify.py               Cross-provider corroboration (the only network access, via the router)
    service.py              Reproducible analysis runs, detector outcomes, findings
  api/
    app.py                  FastAPI factory (create_app); middleware, CORS, routers
    __main__.py             `python -m bip.api` entry point (uvicorn)
    routes/*.py             /api/v1 endpoints
    errors.py               Error envelope and exception handlers
    middleware.py           Body-size limit, security headers
    deps.py                 App context; write gating through the disk guard
    schemas.py              Request bodies and API-only response models
tests/unit/                 Offline unit tests (synthetic fixtures only)
tests/api/                  API and database tests on temporary SQLite databases
tests/stage2/ … stage4/      Offline fake-RPC integration and intelligence tests (no network)
frontend/                   React + TypeScript + Vite dashboard
  src/api/                  Typed API client and response types
  src/pages/                One component per navigation section
  src/components/ui.tsx     Shared UI primitives (panels, pills, states)
  src/lib/                  Address validation, formatting, useApi hook
  src/test/fakeBackend.ts   In-memory API stand-in for frontend tests only
  e2e/smoke.mjs             End-to-end test against the real stack
```

Packages for later stages (`quality/`, `process/`, `agents/`) are created when their stage starts, not as empty placeholders.

## Provider registry and data engine (Stage 2)

- **Registry:** `bip/providers/registry.toml`, loaded and validated by `bip/providers/registry.py`.
  - It holds 16 entries: 10 RPC providers (5 Ethereum, 5 Solana) and 6 non-RPC sources without adapters.
  - Only read-only methods are allowed. Undocumented capabilities and limits stay unknown.
  - Every entry records its documentation, terms and source links.
- **Lifecycle** (`bip/providers/status.py`):
  - A provider with no adapter shows as `registered`, or `configured` if its credentials are set.
  - An adapter without a configured endpoint shows as `adapter_available`.
  - An adapter and endpoint that no request has ever reached shows as `untested`. It becomes `healthy` or `degraded` after successful requests, `unavailable` after a failed one, and `disabled` by override.
  - Nothing is healthy until a real request succeeded.
- **Router** (`bip/router/router.py`):
  - **Selection:** it filters providers by method support, configuration, enablement, identity and rate-limit cooldown, then ranks healthy → untested → degraded → unavailable.
  - **Identity:** network identity is verified before a provider's first use: `eth_chainId` = 0x1, or the Solana mainnet-beta genesis hash.
  - **Retries:** bounded (3 attempts) with exponential backoff and jitter. `Retry-After` is honoured, and a provider is never retried on a method it reported unsupported.
  - **Throttle:** a per-provider politeness throttle (`client_rps`). Requests are sequential.
- **Transport** (`bip/rpc/transport.py`): HTTPS only, the resolved address validated and pinned, TLS verified against the hostname, no redirects, an 8 MiB response cap and timeouts. `BIP_RPC__USE_ENV_PROXY=true` sends requests through `HTTPS_PROXY`; pinning is not possible in that mode.
- **Adapters** (`bip/chains/ethereum`, `bip/chains/solana`): pure parsers over untrusted JSON into BIP contracts. Raw responses are stored compressed in `raw_payloads`, and records reference them by SHA-256.

### What wallet retrieval does and does not cover

| | Ethereum | Solana |
|---|---|---|
| Discovery | `eth_getLogs` for ERC-20/721 `Transfer` with the wallet as sender or recipient, over the last `BIP_INGEST__ETHEREUM_LOOKBACK_BLOCKS` (default 2,000) blocks, chunked and split if a provider limits ranges | `getSignaturesForAddress`, newest first, up to the limit |
| Per transaction | `eth_getTransactionByHash`, receipt, block header (timestamp, hash check) | `getTransaction` (`json`, `maxSupportedTransactionVersion: 0`) |
| Transfers stored | ERC-20 and ERC-721 Transfer logs; top-level ETH value | System Program transfer; SPL Token / Token-2022 `Transfer`, `TransferChecked` |
| Balance observations | Balance and nonce at head (in the run's coverage) | Native and token pre/post balance changes for the wallet and the token accounts it owns |
| **Not covered** | Plain ETH transfers without logs (unless the same tx has a Transfer log), internal transfers, activity older than the window, ERC-1155 | Signatures older than the bounded window; other instructions are counted as unknown, not interpreted |
| Finality | `finalized` tag compared per block; newer blocks stay `observed` | Commitment as configured (`finalized` default, or `confirmed`) |

Every record is `unverified` (single provider, no cross-check yet). Failed or partially parsed items are stored with `parse_status` and an `ingestion_errors` row; nothing is dropped silently. A transaction whose block hash disagrees with its block is refused and logged as `reorg_detected`. Runs are bounded by `BIP_INGEST__MAX_LIMIT` (50), `BIP_INGEST__MAX_RPC_CALLS_PER_RUN` (150) and `BIP_INGEST__RUN_DEADLINE_S` (90). A run ends as `succeeded`, `partial` or `failed`. Cursors make repeated runs continue rather than restart.

**How to ingest:** open a wallet in the dashboard and choose "Retrieve recent activity", or:

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/ingestion/wallet/solana/<ADDRESS>?limit=20"
```

## Database

- **Engine:** SQLite through SQLAlchemy Core (ADR-015). The file is `<BIP_DATA_ROOT>/operational/<BIP_DATABASE__FILENAME>` (default `var/data/operational/bip.sqlite3`).
- **Location rules:** the path is built through `StorageRoot`, so it cannot leave the data root. The file name setting accepts a name only, never a path.
- **Initialization:** `create_db_engine()` creates the directory and applies versioned migrations (`bip/db/engine.py`, recorded in `schema_migrations`). v1 is frozen SQL (`bip/db/sql/v1.sql`). v2 adds columns and Stage 2 tables in place and is tested against a real v1 database. Repeated startups apply nothing new. A database with a newer schema than the code is refused.
- **Starts empty:** no seed data. Chain data can only be written by the ingestion service, from validated RPC responses. No API endpoint accepts chain data from clients.
- **Conventions:** UTC timestamps as ISO strings, raw amounts as decimal strings, separate `block_number`/`slot` columns, natural-key unique constraints (duplicates are no-ops or 409s), foreign keys on with cascading deletes for watchlist entries and findings.
- **Backup:** stop the API and copy the `.sqlite3` file, plus its `-wal` file if present.

## API endpoints (`/api/v1`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Status, version, environment, database and storage-write status, timestamp |
| GET | `/chains` | Configured chains with configured / adapter / connected / data-available flags |
| GET | `/dashboard/summary` | Database-backed counts, recent findings and activity, provider summary |
| GET | `/providers/status` | Catalogued providers; configuration presence; always `unavailable` until adapters exist |
| GET | `/settings` | Non-sensitive configuration (no secrets, credentials or paths) |
| GET | `/wallets` | Wallets with stored data (`limit` ≤ 200, `offset`) |
| GET | `/wallets/{chain}/{address}` | Wallet profile; 422 for invalid input, 404 when nothing is stored |
| GET, POST | `/watchlists` | List, create |
| GET, DELETE | `/watchlists/{id}` | Retrieve with entries, delete |
| POST | `/watchlists/{id}/entries` | Add an address (validated, normalized; 409 on duplicate) |
| DELETE | `/watchlists/{id}/entries/{entry_id}` | Remove an address |
| GET, POST | `/investigations` | List (optional `?status=`), create |
| GET, PATCH | `/investigations/{id}` | Retrieve with findings, update status (`open`, `in_progress`, `closed`) |
| GET | `/providers`, `/providers/status` | Registry entries with lifecycle, methods, limits, configuration (never URLs or keys) |
| GET, PATCH | `/providers/{id}` | Detail with the last 10 health checks; enable or disable (`{"enabled": false}`) |
| POST | `/providers/{id}/health-check` | Bounded read-only check on that provider only (identity, then head). 409 if it is not usable |
| GET | `/chains/{chain}/network` | Most recent stored, identity-verified network observation (no network request) |
| GET, POST | `/chains/{chain}/latest-block` | Fetches the head from an eligible provider, then stores and returns it (503 if none) |
| GET | `/wallets/{chain}/{address}/transactions` · `/transfers` · `/balance-changes` | Stored observations with provenance (`limit` ≤ 200) |
| POST | `/ingestion/wallet/{chain}/{address}?limit=N` | One bounded synchronous run (N ≤ 50). 409 if another run is active |
| GET | `/ingestion/jobs`, `/ingestion/jobs/{id}` | Run records with counts, coverage notes and errors |
| POST | `/investigations/{id}/findings` | Attach a `Finding`. The contract's evidence rules are enforced, and evidence claiming verified provenance must be a transaction stored in the database (self-asserted verification is rejected with 422) |

### ML classification and AI research endpoints (Stage 5)

Neither group makes provider calls. Classification is local; research reads stored data through typed tools. Methodology: [`ML_WALLET_CLASSIFICATION.md`](ML_WALLET_CLASSIFICATION.md), [`AI_RESEARCH_METHODOLOGY.md`](AI_RESEARCH_METHODOLOGY.md).

| Method | Path | Purpose |
|---|---|---|
| GET | `/ml/taxonomy` | Labels, features and statuses |
| POST | `/ml/classify/{chain}/{address}` | Classify (rules plus active model); appended to history |
| GET | `/ml/classifications/{chain}/{address}` · `/ml/classifications/{id}` · `/ml/features/{chain}/{address}` | History · one result · feature snapshot |
| GET | `/ml/models` · `/ml/models/{name}/{version}` | Registry · evaluation |
| GET, POST | `/ml/training-runs` | History · train (`stored_labels`, or `synthetic` if enabled); 409 `training_busy` |
| GET, POST | `/ml/labels` · POST `/ml/labels/{id}/reviews` | Labels with provenance · append-only reviews |
| GET | `/research/config` | Provider status, budgets, tools |
| POST | `/research/preview` | Interpreted request, fixed plan, budget, LLM availability |
| POST | `/research/runs` | Start (202); 409 `research_busy` |
| GET | `/research/runs` · `/research/runs/{id}` · `/research/runs/{id}/report` | History · progress, plan, tool calls, claims, model usage · report |
| POST | `/research/runs/{id}/cancel` · `/research/runs/{id}/rerun` | Cancel · new run linked to the original |

### Provider intelligence endpoints (Stage 4)

Only `POST /providers/probe-runs` contacts providers, within the server-side budget. Methodology: [`PROVIDER_CAPABILITY_METHODOLOGY.md`](PROVIDER_CAPABILITY_METHODOLOGY.md).

| Method | Path | Purpose |
|---|---|---|
| GET | `/providers/capabilities` | Capability catalogue per chain and status definitions |
| GET | `/providers/profiles` | Every provider: configuration, connectivity, coverage, historical coverage, reliability state, documented limits |
| GET | `/providers/{id}/profile` | Capability matrix (latest observation per capability), reliability, consistency |
| GET | `/providers/{id}/capabilities/history?capability=&limit=&offset=` | Observation history |
| GET | `/providers/{id}/reliability` · `/providers/{id}/consistency` | Reliability (sample sizes, window) · cross-provider checks |
| GET | `/providers/probe/preview?provider=&capability=&max_calls_per_provider=` | What would be tested, the maximum requests and duration, ineligible providers with reasons. Sends nothing |
| POST | `/providers/probe-runs` | Start one bounded run (`{"providers"?, "capabilities"?, "max_calls_per_provider"? ≤ 8}`). 409 `probe_busy` / `nothing_to_probe`; 422 for unknown capabilities, ineligible providers or extra fields |
| GET | `/providers/probe-runs` · `/providers/probe-runs/{run_id}` | Run history · one run with all observations and consistency checks |

### Intelligence endpoints (Stage 3, `/api/v1/intelligence`)

Every response includes `data_origin` (`offline_fixture` or `stored_observations`), `analyzer_version`, scope or coverage, and verification. Everything except the verification POSTs reads stored data only (no network). Methodology: [`INTELLIGENCE_METHODOLOGY.md`](INTELLIGENCE_METHODOLOGY.md).

| Method | Path | Purpose |
|---|---|---|
| GET | `/status` | Verification counts (corroborated / conflicted / invalidated / unverified), runs, anomalies, findings, leads, detector specs |
| GET | `/wallets/{chain}/{address}/profile` | Scope-labelled metrics, volumes, counterparties, interactions, deployments, activity, coverage, verification, limitations, latest run, findings |
| GET | `/wallets/{chain}/{address}/activity` · `/counterparties` | Paginated (`limit` ≤ 200, `offset`) |
| GET | `/wallets/{chain}/{address}/behaviour` | Descriptive rules with status, evidence, confidence |
| POST | `/analyze/{chain}/{address}` | Reproducible analysis run (reused when inputs are unchanged). 409 `analysis_busy` while another runs |
| POST | `/watchlists/{id}/analyze` | Analyse up to 50 watchlist entries |
| GET | `/graph/{chain}/{address}?depth=1..3&max_nodes=&max_edges=` | Bounded, evidence-backed graph with truncation flags |
| GET | `/flows/{chain}/{address}?since=&until=` | Per-asset in/out/net, counterparties, daily buckets, largest transfers, repeated paths (no USD) |
| GET | `/anomalies?chain=&address=&status=&watchlist_id=` · `/anomalies/{finding_id or obs-N}` | Detector results from each wallet's latest run |
| POST | `/findings/{finding_id}/attach` | Attach a finding to an investigation (`{"investigation_id": N}`) |
| GET | `/opportunities` · `/opportunities/{candidate_key}` | Research leads (never advice) |
| POST | `/opportunities/refresh/{chain}` | Re-evaluate the lead rule; upserts, never duplicates |
| GET | `/verification/{chain}/{tx_id}` | Effective status, every check, every provider observation |
| POST | `/verification/{chain}/{tx_id}` · `/verification/{chain}/wallet/{address}?limit≤20` | Corroborate with independent providers (network, via the router) |

Errors always use `{"error": {"code", "message", "details?"}}`:

| Status | Code | Meaning |
|---|---|---|
| 404 | `not_found` | The record does not exist |
| 409 | `conflict` | Duplicate |
| 413 | `payload_too_large` | Request body over the size limit |
| 422 | `validation_error` / `invalid_input` | Bad request body / bad chain or address |
| 503 | `database_unavailable` | Database unavailable |
| 507 | `storage_limit` | Disk guard refused the write |
| 502 | `upstream_failed` | Every provider attempt failed (details list provider and error class) |
| 503 | `no_eligible_provider` | No provider can serve the request (details give the reason for each) |
| 409 | `ingestion_busy` / `provider_not_usable` | A run is already active / provider disabled or unconfigured |
| 500 | `internal_error` | Unexpected error |

## Configuration variables

All settings are environment variables with the `BIP_` prefix. Nested groups use `__`. Settings are **not** read from `.env` implicitly: copy `.env.example` to `.env` and run `uv run --env-file .env <command>`.

| Variable | Default | Notes |
|---|---|---|
| `BIP_ENV` | `development` | `development`, `test`, `production` |
| `BIP_LOG_LEVEL` | `INFO` | `DEBUG` … `CRITICAL` |
| `BIP_LOG_FORMAT` | `json` | `json` or `text`. Production requires `json` |
| `BIP_DATA_ROOT` | `var/data` | Relative to the working directory. Production requires an absolute path |
| `BIP_TEMP_DIR` | `<data_root>/tmp` | Absolute in production |
| `BIP_MAX_DATA_BYTES` | `5GiB` | Accepts sizes like `500MiB`, `2GiB`, or plain bytes |
| `BIP_MIN_FREE_BYTES` | `5GiB` | Free-space floor on the data filesystem |
| `BIP_MIN_FREE_RATIO` | `0.05` | Free-fraction floor, 0–0.5 |
| `BIP_ENABLED_CHAINS` | `ethereum,solana` | Comma-separated; each must be in the chain registry |
| `BIP_ROUTER__LIVE_REQUESTS_ENABLED` | `false` | Not used by any code yet |
| `BIP_ROUTER__CIRCUIT_BREAKERS_ENABLED` | `true` | Not used by any code yet |
| `BIP_ROUTER__CROSS_VERIFICATION_SAMPLE_RATE` | `0.01` | 0–1. Not used by any code yet |
| `BIP_API__HOST` | `127.0.0.1` | Bind address. There is no authentication; keep it on loopback |
| `BIP_API__PORT` | `8000` | |
| `BIP_API__CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Exact origins only. `*`, paths and non-local `http://` are rejected |
| `BIP_API__MAX_REQUEST_BYTES` | `65536` | Larger bodies get 413 |
| `BIP_DATABASE__FILENAME` | `bip.sqlite3` | File name under `<data_root>/operational/` |
| `BIP_DATABASE__BUSY_TIMEOUT_MS` | `5000` | SQLite lock wait |
| `BIP_PROVIDER_<NAME>_URL` | unset | Full endpoint URL for a credentialed provider (see `.env.example`). Treated as a secret; host must match the registry's `allowed_hosts` |
| `BIP_RPC__TIMEOUT_S` / `__MAX_ATTEMPTS` / `__MAX_RESPONSE_BYTES` | 10 / 3 / 8 MiB | Per-request limits |
| `BIP_RPC__USE_ENV_PROXY` | `false` | Use `HTTPS_PROXY` (no local IP pinning) |
| `BIP_INGEST__DEFAULT_LIMIT` / `__MAX_LIMIT` | 20 / 50 | Transactions per run |
| `BIP_INGEST__ETHEREUM_LOOKBACK_BLOCKS` / `__ETHEREUM_LOG_CHUNK_BLOCKS` | 2000 / 500 | Ethereum log scan window |
| `BIP_INGEST__SOLANA_COMMITMENT` | `finalized` | `confirmed` or `finalized` (`processed` is not allowed) |
| `BIP_INGEST__RUN_DEADLINE_S` / `__MAX_RPC_CALLS_PER_RUN` | 90 / 150 | Run bounds |
| `BIP_INTEL__GRAPH_MAX_DEPTH` / `__GRAPH_MAX_NODES` / `__GRAPH_MAX_EDGES` / `__GRAPH_TIMEOUT_S` | 2 / 200 / 500 / 5 | Graph bounds (hard caps 3 / 1000 / 3000 / 30) |
| `BIP_INTEL__MAX_RECORDS_PER_WALLET` | 5000 | Records loaded per analysis (truncation is reported) |
| `BIP_INTEL__FLOW_MAX_WINDOW_DAYS` | 365 | Longest flow window |
| `BIP_INTEL__NEWLY_OBSERVED_DAYS` / `__SPIKE_FACTOR` / `__LARGE_TRANSFER_MAD_Z` | 7 / 3.0 / 6.0 | Detector thresholds |
| `BIP_INTEL__BURST_COUNT` / `__BURST_WINDOW_MINUTES` / `__CONCENTRATION_SHARE` | 5 / 10 / 0.5 | Rule thresholds |
| `BIP_PROBE__MAX_CALLS_PER_PROVIDER` / `__MAX_CALLS_PER_RUN` / `__MAX_RUN_SECONDS` | 8 / 48 / 90 | Probe budgets. These are ceilings: values can only be lowered |
| `BIP_PROBE__MAX_CONCURRENT_PROVIDERS` / `__REQUEST_TIMEOUT_S` / `__MAX_RESPONSE_BYTES` | 2 / 5 / 1 MiB | Ceilings |
| `BIP_PROBE__RETRIES` | 0 | Must be 0 |
| `BIP_PROBE__MIN_INTERVAL_S` | 1.0 | Per-provider spacing floor (may only be raised) |
| `BIP_PROBE__ETHEREUM_HISTORY_LOOKBACK_BLOCKS` / `__SOLANA_HISTORY_LOOKBACK_SLOTS` | 100000 / 1000000 | Distance of the single historical sample |
| `BIP_ML__MIN_LABELLED_WALLETS` / `__MIN_POSITIVES_PER_LABEL` / `__MIN_TX_FOR_CLASSIFICATION` | 30 / 5 / 3 | Training data gate · per-label minimum · abstention |
| `BIP_ML__HIGH_PRECISION_TARGET` / `__MODERATE_PRECISION_TARGET` / `__MIN_THRESHOLD_SUPPORT` | 0.95 / 0.80 / 10 | Threshold validation |
| `BIP_ML__ALLOW_SYNTHETIC_TRAINING` | false | Allows the labelled synthetic demo dataset (the offline fixture backend enables it) |
| `BIP_ML__MAX_ARTIFACT_BYTES` / `__MAX_LABEL_IMPORT` | 64 MiB / 5000 | Storage and import limits |
| `BIP_AI__PROVIDER` | `none` | `none`, `ollama` (loopback only) or `openai_compatible` (https) |
| `BIP_AI__OLLAMA_URL` / `__OLLAMA_MODEL` | `http://127.0.0.1:11434` / unset | Must be an already-installed model; never pulled |
| `BIP_AI__API_BASE_URL` / `__API_MODEL` / `__API_KEY_ENV` | unset / unset / `BIP_AI_API_KEY` | Optional OpenAI-compatible API; the key comes from the named environment variable |
| `BIP_AI__MAX_SECONDS` / `__MAX_TOOL_CALLS` / `__MAX_MODEL_CALLS` | 120 / 20 / 8 | Ceilings (can only be lowered) |
| `BIP_AI__MAX_TOOL_OUTPUT_BYTES` / `__MAX_REPORT_BYTES` / `__MAX_CONCURRENT` | 100 KiB / 30 KiB / 2 | Ceilings |
| `BIP_POSTGRES_DSN` | unset | Secret. Must start with `postgresql://`. Not used (SQLite is the operational store in BIP 0.1) |
| `BIP_REDIS_URL` | unset | Secret. Must start with `redis://` or `rediss://` |

Invalid values raise `ConfigurationError` listing each variable and the reason, never the value. Unknown `BIP_*` environment variables are currently **ignored**, not rejected: provider secret references (Stage 2) will also use `BIP_*` names. A misspelled variable therefore falls back to its default.

Provider API keys are not settings. They will be referenced by name from the provider registry and read with `bip.security.secrets.read_secret(NAME)`, which checks `$NAME` and then the file named by `$NAME_FILE`.

## Security assumptions and rules for later stages

- **Redaction is best effort plus exact match.** Pattern rules catch common credential shapes. The only reliable guarantee is exact-value redaction of secrets loaded through `load_settings()` or `read_secret()`. Code that obtains a secret any other way must call `register_secret()`.
- **Blockchain identifiers stay readable.** `0x`-hex values are never pattern-redacted. Long opaque URL path segments are, so a Solana signature inside a URL path may be redacted in logs.
- **URL validation is a first layer only.** `validate_url()` checks a URL once. It does not prevent DNS rebinding or redirect-based SSRF. The Stage 3 HTTP client must:
  1. resolve at connection time, validate every address with `check_ip()`, and connect to that pinned address;
  2. disable automatic redirects, or validate each hop with `validate_redirect()` (at most 3 hops, no downgrade from https);
  3. set connect, read and total timeouts and a response size cap;
  4. accept URLs only from validated configuration or the registry, never directly from user input.
- **Storage.** All writes go through `StorageRoot.path()` or `create_exclusive()`, and through `DiskGuard.require()` before each batch. Nothing in BIP deletes data automatically.
- **API (BIP 0.1).**
  - There is **no authentication or authorization**. The API binds to 127.0.0.1 by default and must not be exposed to a network until auth exists.
  - CORS allows only exact configured origins. Request bodies are capped. Error responses never include stack traces, SQL, file paths or input values. Every response gets `nosniff`, `DENY`, `no-referrer` and `no-store` headers.
  - All SQL uses SQLAlchemy Core expressions, so every value is a bound parameter.
- **Frontend.** The UI ships a restrictive Content-Security-Policy (`self` only), loads no third-party assets, never renders HTML from API data, and opens external links with `rel="noopener noreferrer"`.
- **Contracts are structural.** Validation proves a record is well formed and internally consistent. It does not prove the record matches the chain; that is the job of verification stages.

## Testing conventions

- Unit tests are offline and hermetic. `tests/conftest.py` strips `BIP_*` environment variables and registered secrets for every test.
- Network-shaped code takes injectable functions (e.g. the resolver in `validate_url`, the disk-usage function in `DiskGuard`), so tests never touch real systems.
- All identifiers in fixtures are synthetic.
- `pytest` runs with warnings as errors and strict markers.
