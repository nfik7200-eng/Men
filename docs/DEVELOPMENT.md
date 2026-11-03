# Development Guide

How to work on the BIP code base. Applies from roadmap Stage 1 onward.

## Requirements

- Python 3.12 or newer (CI runs 3.12; 3.13 also verified locally).
- [uv](https://docs.astral.sh/uv/) for environments and dependencies.
- Node.js 22+ and npm for the frontend.
- No database server, Docker, API keys or network access are needed to run the app, checks or tests. SQLite ships with Python.

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

**Run the app locally:** start `make api` in one terminal and `make web` in another, then open http://127.0.0.1:5173. The Vite dev server proxies `/api` to the backend, so the browser talks to one origin. Interactive API docs are at http://127.0.0.1:8000/api/v1/docs.

CI (`.github/workflows/ci.yml`) runs on pushes to `main` and `claude/**` branches and on pull requests:
- backend: lint, format check, pyright, pytest;
- frontend: ESLint, `tsc`, Vitest, build;
- a gitleaks scan.

The end-to-end test needs a Chromium that `require("playwright")` can find, so it currently runs locally only.

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
    schema.py               SQLAlchemy Core tables (schema version 1)
    engine.py               DB location, engine, versioned migrations, connectivity check
    repository.py           All reads/writes; maps failures to typed errors
    records.py              Read models (also used as API response bodies)
    types.py                UTCDateTime column type
  providers/catalog.py      Static provider metadata; reports every provider as unavailable
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
frontend/                   React + TypeScript + Vite dashboard
  src/api/                  Typed API client and response types
  src/pages/                One component per navigation section
  src/components/ui.tsx     Shared UI primitives (panels, pills, states)
  src/lib/                  Address validation, formatting, useApi hook
  src/test/fakeBackend.ts   In-memory API stand-in for frontend tests only
  e2e/smoke.mjs             End-to-end test against the real stack
```

Packages for later stages (`router/`, `chains/`, `ingest/`, `quality/`, `process/`, `intel/`) are created when their stage starts, not as empty placeholders.

## Database

- **Engine:** SQLite through SQLAlchemy Core (ADR-015). The file is `<BIP_DATA_ROOT>/operational/<BIP_DATABASE__FILENAME>` (default `var/data/operational/bip.sqlite3`).
- **Location rules:** the path is built through `StorageRoot`, so it cannot leave the data root. The file name setting accepts a name only, never a path.
- **Initialization:** `create_db_engine()` creates the directory and applies versioned migrations (`bip/db/engine.py`, recorded in `schema_migrations`). Repeated startups apply nothing new. A database with a newer schema than the code is refused.
- **Starts empty:** no seed data. Chain data (`transactions`, `transfers`, `wallets`) can only be written by `Repository.store_transaction` / `store_transfer` from validated contracts. No API endpoint writes chain data; ingestion (Stage 6+) will call these functions.
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
| POST | `/investigations/{id}/findings` | Attach a `Finding`. The contract's evidence rules are enforced, and evidence claiming verified provenance must be a transaction stored in the database (self-asserted verification is rejected with 422) |

Errors always use `{"error": {"code", "message", "details?"}}`:

| Status | Code | Meaning |
|---|---|---|
| 404 | `not_found` | The record does not exist |
| 409 | `conflict` | Duplicate |
| 413 | `payload_too_large` | Request body over the size limit |
| 422 | `validation_error` / `invalid_input` | Bad request body / bad chain or address |
| 503 | `database_unavailable` | Database unavailable |
| 507 | `storage_limit` | Disk guard refused the write |
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
| `BIP_PROVIDER_*` | unset | Provider credentials (e.g. `BIP_PROVIDER_ALCHEMY_KEY`). Only their presence is reported; no adapter reads them yet |
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
