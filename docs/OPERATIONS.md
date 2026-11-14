# Operations

How BIP runs, what bounds its resource use, how to back it up and how to see that it is healthy.
Everything below was checked against offline tests. **Nothing was measured on the production
VPS** (it was not accessed), so sizes and timings for that machine are unknown until measured there.

## Processes

One Python process (`python -m bip.api`) with a thread pool for request handlers. There is no
Redis, no queue broker and no second database. A single background thread runs the scheduler when
`BIP_WORKERS__ENABLED=true`. The scheduler is **off by default**.

## SQLite: WAL and locking assessment

* The database runs in WAL mode with `busy_timeout` 5 s (`BIP_DATABASE__BUSY_TIMEOUT_MS`) and
  foreign keys on. Readers do not block the writer and the writer does not block readers.
* SQLite allows **one writer at a time**. BIP's writers are short transactions from request
  handlers, one ingestion run at a time (a process-wide lock), scheduler jobs one after another
  (a scheduler lock), and the AI research manager's worker. The design keeps every transaction
  small (a detector's batch, one transaction row set); none holds a write lock across a network
  call.
* The WAL file grows between checkpoints. The `maintenance` job runs
  `PRAGMA wal_checkpoint(TRUNCATE)`; `/system/health` reports `wal_bytes` and warns above 256 MB.
* What this assessment does **not** show: behaviour under production load. The tests prove
  correctness of locking order and that jobs never overlap, not throughput.
* Reasons to leave SQLite, to be judged from `/system/health` and logs on the real host:
  sustained `database is locked` errors; write latency that makes request handlers queue; a
  database large enough that backups no longer finish comfortably; or a need for more than one
  process writing. None is established. Adding PostgreSQL before one of these is shown would add
  operating cost for no measured gain.

## What bounds resource use

| Resource | Bound | Where |
|---|---|---|
| RPC calls per ingestion run | `BIP_INGEST__MAX_RPC_CALLS_PER_RUN` (150), deadline 90 s | `bip/router` budget |
| Concurrent ingestion | one run at a time (409 otherwise) | `ingest_lock` |
| Token accounts per run | `solana_token_accounts_limit` (5), 10 signatures each | wallet ingestion |
| Placeholder / partial retries | 10 per run, at most 5 attempts per record | ingestion |
| Detector rows read | `BIP_DISCOVERY__MAX_ROWS_SCANNED` (20 000), reported when hit | discovery |
| New signals per detector per run | `max_signals_per_detector` (50), dropped count reported | discovery |
| Signal life | open signals expire after `ttl_days` (7); closed ones pruned after 90 days | maintenance |
| API request body | 64 KiB default | middleware |
| API request rate | 600 per minute per client address; login 10 per minute | protection middleware |
| Rate-limiter memory | at most 10 000 tracked client addresses | `RateLimiter` |
| API metrics memory | at most 200 distinct routes, ids collapsed | `ApiMetrics` |
| Audit log | `audit_retention_days` (365) | maintenance |
| Job history | `job_history_days` (90) | maintenance |
| Backups | newest `backup_keep` (7); refused without room for the copy plus 64 MB | `BackupService` |
| Disk | the existing disk guard refuses writes below the free-space floor | `DiskGuard` |

## Backups and restore

A backup is a consistent SQLite snapshot taken with the online backup API while BIP keeps running,
checked with `PRAGMA integrity_check`, written owner-only (0600) to `<data root>/operational/backups/`.

* Make one now: `POST /api/v1/system/backups` (admin) or enable the `backup` job
  (`BIP_WORKERS__BACKUP_ENABLED=true`, `BIP_WORKERS__BACKUP_INTERVAL_HOURS`, `BIP_WORKERS__BACKUP_KEEP`).
* Check one: `POST /api/v1/system/backups/<name>/verify`.
* **Restore is manual by design.** Stop BIP, keep the current database file aside, copy the chosen
  backup over `<data root>/operational/bip.sqlite3`, delete any `bip.sqlite3-wal` and `-shm` next to
  it, start BIP (migrations then run if the backup is older). BIP refuses a database newer than its
  code.
* Backups live on the same disk as the database. They protect against corruption and mistakes, not
  against losing the disk. Copy them elsewhere (an owner decision; nothing is uploaded by BIP).

## Scheduler and jobs

Enable with `BIP_WORKERS__ENABLED=true`. Jobs run one at a time on one thread; each run is recorded
in `job_runs`, so state survives a restart, and a run left `running` by a crash is closed as an
error. A failing job backs off exponentially (cap `max_backoff_hours`). A job that finds its lock held
reports `skipped:` and does not count as a failure.

| Job | Default | Contacts providers | What it does |
|---|---|---|---|
| `discovery` | every 60 min | no | run all detectors over stored data |
| `relationships` | every 60 min | no | rebuild evidence-backed relationships |
| `maintenance` | every 24 h | no | retention for audit, signals, runs, errors, sessions; WAL checkpoint |
| `backup` | off | no | verified backup, keep the newest few |
| `watchlist_refresh` | **off** | **yes** | re-ingest the least recently refreshed watchlist wallets |
| `chain_scan` | **off** | **yes** | read the next finalized Ethereum blocks for contract creations and large native transfers |
| `solana_scan` | **off** | **yes** | read the next finalized Solana slots for new mints, programs, pools and large SOL transfers |
| `ethereum_backfill` | **off** | **yes** | read one bounded window of Ethereum history backward (ADR-043) |
| `solana_backfill` | **off** | **yes** | read one bounded window of Solana history backward (ADR-043) |
| `gap_recovery` | **off** | **yes** | re-read a few positions of unresolved coverage gaps on both chains |

`watchlist_refresh`, `chain_scan` and `solana_scan` are the jobs that spend provider quota. `solana_scan` needs `BIP_WORKERS__SOLANA_COLLECTOR_ENABLED=true` and reads at most `solana_collector.blocks_per_run` (10) finalized slots, one `getBlock` request each, within 150 requests and 90 s. Public Solana endpoints rate-limit and blocks are large: each invocation covers seconds of chain, so the collector reports its lag and records a gap whenever it has to jump ahead. It needs
`BIP_WORKERS__WATCHLIST_REFRESH_ENABLED=true`, takes at most `watchlist_refresh_wallets_per_tick` (2)
wallets, and goes through the normal bounded ingestion path (call budget, disk guard, single-run
lock). `chain_scan` needs `BIP_WORKERS__COLLECTOR_ENABLED=true` and reads at most
`collector.blocks_per_run` (20) finalized blocks, `collector.max_receipts_per_run` (60) receipts and
`collector.max_pools_per_run` (15, each costing block, transaction and receipt calls) per run, sharing the same ingestion lock. Do not enable either without approving the provider usage
it implies.

Run a job now: `POST /api/v1/system/jobs/<name>/run` (admin). Status: `GET /api/v1/system/jobs`.

### Historical backfill, recovery and pause (Stage 2)

- **Limits:** backfill and recovery run at most `backfill.max_rpc_calls_per_run` (100) calls and `backfill.run_deadline_s` (60 s) per run. Each backfill run reads `blocks_per_run` / `slots_per_run` (10) positions backward.
- **Live priority:** they report `deferred` while live collection is running, or is more than `max_live_lag_blocks` (100) / `max_live_lag_slots` (3,000) behind as of its last run.
- **Manual runs (admin):** `POST /api/v1/ingestion/backfill/{chain}` and `POST /api/v1/ingestion/recovery/{chain}`.
- **Pause and resume (admin):** `POST /api/v1/ingestion/streams/{chain}/{live|historical}/pause` (or `/resume`). No cursor moves; the other direction keeps running.
- **Status:** `GET /api/v1/ingestion/checkpoints` shows live and historical cursors, the boundary, status and lag. `GET /api/v1/ingestion/coverage/{chain}` shows processed, empty and gap ranges, and how far back history has been processed.
- **History floor:** a run that reports `blocked` with `history_unavailable` has reached history that no configured provider serves. Add a provider that has it, or accept the recorded limit. BIP never shortens the period silently.
- **Provider quotas:** `GET /api/v1/system/budgets` shows BIP's own per-provider request counts for today. `BIP_RPC__DAILY_REQUEST_BUDGETS` can cap a provider per UTC day.
- **Provider terms:** check each provider's terms before enabling these jobs. PublicNode, the solana.com site terms and Etherscan Free restrict bulk or automated collection (recorded in `bip/providers/registry.toml`).

## Health

`GET /api/v1/health` is public and minimal. `GET /api/v1/system/health` (viewer) returns database
and WAL size, schema version, disk status, process memory and threads, API counters and latency
buckets, recent ingestion runs and error codes, discovery state, provider summary, scheduler jobs and
backups. It never returns file paths or secrets. `status` is `ok`, `degraded` (any warning) or
`critical` (database unavailable or writes refused). Warning codes: `database_unavailable`,
`wal_large`, `database_large`, `storage_refusing_writes`, `disk_low`, `storage_unmeasurable`,
`ingestion_failures`, `no_healthy_provider` (only when providers *were* checked and none passed),
`job_failing`, `backup_stale`.

`GET /api/v1/system/usage?days=N` totals BIP's own RPC requests per day, chain and provider from the
ingestion run log. It is not the provider's meter, so it shows no credits or prices.

## Deployment kit

`deploy/` holds a reproducible, **unapplied** VPS kit: a production env template, a hardened systemd
unit that runs the pre-flight check before every start, Caddy and nginx examples, an off-disk backup
timer and `deploy/README.md` (install, verify, upgrade, roll back). `python -m bip.tools.preflight
--env-file /etc/bip/bip.env` fails closed on an unsafe configuration (authentication off, insecure
cookie, proxy headers trusted on a reachable bind, a world-readable env file) and warns when a
job that contacts providers is on. It prints names and reasons, never values.

## GitHub Codespaces preview (SYNTHETIC)

`.devcontainer/` installs dependencies only; it starts nothing and contacts no provider.

1. Create a Codespace on the branch.
2. Run `bash .devcontainer/start-preview.sh`. It prints a synthetic password; sign in as `viewer`, `analyst` or `admin`.
3. Open the forwarded port **5173** in the Ports tab, on a phone or a browser.
   - Keep its visibility **Private**, the default, so only your GitHub login can reach it. Do not make it public or forward 8000.
   - The API stays on 127.0.0.1:8000 behind the Vite proxy.
   - The preview tool allows the codespace's own forwarded HTTPS origin, so sign-in and writes work through it.

The preview uses a throwaway database of synthetic records. To use real stored data instead, run the real API with `BIP_AUTH__MODE=required`. Live collection still needs explicit approval.

## Exposure

The API refuses to start on a non-loopback address unless `BIP_AUTH__MODE=required` (or
`BIP_AUTH__ALLOW_INSECURE_BIND=true`, which must never be used on a reachable host). Recommended for
phone access: serve on a Unix socket (`BIP_API__UNIX_SOCKET`, mode 0660, so only the proxy can
reach it and its `X-Forwarded-For` can be trusted; uvicorn would otherwise `chmod` its own socket
to 0666, so BIP binds it itself) or at least keep BIP bound to `127.0.0.1`, reach it over a private network (Tailscale or
WireGuard) through a TLS-terminating reverse proxy you control, set `BIP_AUTH__MODE=required`, keep
`BIP_AUTH__COOKIE_SECURE=true`, and set `BIP_AUTH__TRUST_PROXY_HEADERS=true` only behind that proxy.
Create the first administrator with `python -m bip.tools.users create <name> --role admin`.
The bundled frontend has a sign-in screen: with `mode=required` nothing but the sign-in form renders until a session exists, the CSRF token lives in memory only and is recovered from `GET /api/v1/auth/me` after a reload, and controls the signed-in role cannot use are disabled with the reason (the server stays authoritative). With `mode=disabled` the UI is unchanged, but the API protects itself (ADR-048). Every TCP request needs a loopback host (or one in `BIP_API__ALLOWED_HOSTS`). Every change request needs an `X-BIP-Client` header, which the UI sends and scripts must add. A change request is refused if it comes from a browser page whose origin is not on the exact `BIP_API__CORS_ORIGINS` list. A locked account answers the same 401 as a wrong password, so the UI cannot tell them apart.
