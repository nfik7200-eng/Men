# Development Guide

How to work on the BIP code base. Applies from roadmap Stage 1 onward.

## Requirements

- Python 3.12 or newer (CI runs 3.12; 3.13 also verified locally).
- [uv](https://docs.astral.sh/uv/) for environments and dependencies.
- No database, Docker, API keys or network access are needed to run the checks or tests.

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

CI (`.github/workflows/ci.yml`) runs lint, format check, pyright and pytest, plus a gitleaks scan, on pushes to `main` and `claude/**` branches and on pull requests.

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
tests/unit/                 Offline unit tests (synthetic fixtures only)
```

Packages for later stages (`router/`, `chains/`, `ingest/`, `quality/`, `process/`, `intel/`) are created when their stage starts, not as empty placeholders.

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
| `BIP_POSTGRES_DSN` | unset | Secret. Must start with `postgresql://`. No database code exists yet |
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
- **Contracts are structural.** Validation proves a record is well formed and internally consistent. It does not prove the record matches the chain; that is the job of verification stages.

## Testing conventions

- Unit tests are offline and hermetic. `tests/conftest.py` strips `BIP_*` environment variables and registered secrets for every test.
- Network-shaped code takes injectable functions (e.g. the resolver in `validate_url`, the disk-usage function in `DiskGuard`), so tests never touch real systems.
- All identifiers in fixtures are synthetic.
- `pytest` runs with warnings as errors and strict markers.
