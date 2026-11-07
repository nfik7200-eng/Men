# Deployment (single Linux VPS, systemd + nginx)

Status: **scripts written and exercised in a temporary prefix on a dev sandbox; never run on a VPS** (no VPS access existed when this was
written). Read section 11 (verified vs untested) before trusting anything here. Everything below is additive: it uses its own
unit names (`watchtower-*`), directories, system user and port, and refuses to touch anything that already exists unless `--force`.

## 1. Layout

| Path | Purpose | Owner |
|---|---|---|
| `/opt/watchtower/releases/<UTC id>/` | code + its own `.venv` (read-only to the service) | root |
| `/opt/watchtower/current`, `previous` | symlinks; atomic switch for update/rollback | root |
| `/opt/watchtower/.watchtower-deploy` | marker: "these scripts created this prefix" (idempotency/safety) | root |
| `/var/lib/watchtower/watchtower.db` (+`-wal`,`-shm`) | the SQLite database (**outside** releases, so updates cannot lose it) | `watchtower` |
| `/var/lib/watchtower/backups/` | verified snapshots (`watchtower-<UTC>.db`) | `watchtower` |
| `/var/lib/watchtower/schedule.json` | scheduler opt-in file (ships `enabled:false`) | `watchtower` |
| `/etc/watchtower/watchtower.env` | all settings, mode 0640 `root:watchtower`; from `.env.example` | root |

`config.ROOT` is the release directory, so every ROOT-relative default (DB, `data/backups`, `data/schedule.json`) would be lost on the
next update. The env file therefore **must** set an absolute `WATCHTOWER_DB_URL`; the scripts refuse otherwise, and every script passes
explicit paths (`backup --out`, `schedule run-due --config`). `data/source_policies.json` stays inside the release on purpose: it is
reviewed in git and not configurable.

Override any path/threshold with `WT_PREFIX WT_STATE WT_ETC WT_USER WT_UNIT_DIR WT_PORT WT_MIN_FREE_GB WT_MIN_RAM_MB WT_PYTHON`
(see `deploy/lib.sh`). Test-only switches: `WT_NO_SYSTEMCTL=1 WT_ALLOW_NONROOT=1 WT_NO_SU=1`.

## 2. Prerequisites, sizing, cost

* Linux with systemd (Debian/Ubuntu assumed; the scripts warn on others), root/sudo, `curl`, `python3` >= 3.11 with `venv`
  (`apt install python3-venv`), nginx + certbot for HTTPS, a **domain you control** pointing at the VPS.
* The historical target was a Contabo Cloud VPS 8 (~8 vCPU / 24 GB RAM) with a small root disk that once hit critical usage.
  **Treat those numbers as unverified**: the scripts measure the real machine and abort below `WT_MIN_FREE_GB` (default 5 GB free
  on the prefix and state filesystems) or `WT_MIN_RAM_MB` (default 2048 MB total).
* Footprint: one uvicorn worker (rate limiter is in-process, DB is SQLite), capped at `MemoryMax=2G`, `TasksMax=256`. Real memory use
  and database growth under live collection are **not measured**; observations are append-only, so the DB only grows. Watch
  `df -h /var/lib/watchtower`. Backups keep the newest 14 snapshots (plus anything under 30 days), each about the DB size; budget
  roughly 30x the DB size for them (the rule keeps the newest 14 OR anything younger than 30 days: about 30 daily files, plus one pre-update snapshot per update; `.UNVERIFIED` files are never pruned) and copy them off the box, or lower `--keep`/`--min-age-days`.
* Cost: no recurring cost beyond the VPS itself and your domain registration. Let's Encrypt is free; there are no paid APIs
  anywhere in the code. Ollama (optional, `OLLAMA_URL`) would run locally and is not provisioned by these scripts. Prices of the VPS
  and domain are not stated here because they were not verified.

## 3. Install (step by step)

Run as root on the VPS from a checkout of this repository (copy it there yourself: no remote is assumed).

```bash
cd /path/to/checkout
deploy/install.sh                      # DRY RUN (default): preflight + prints every change; changes nothing
deploy/install.sh --init-env --apply   # creates /etc/watchtower/watchtower.env from .env.example (placeholders)
$EDITOR /etc/watchtower/watchtower.env # replace every YOUR_* ; generate the admin token on the server
deploy/install.sh                      # dry run again: must not report a refused env file (a placeholder, a short admin token or a relative DB path is refused)
deploy/install.sh --apply --start --enable-backup-timer
```

Preflight (always, also in dry run): Linux, systemd, `curl`, RAM, free disk, python >= 3.11 + venv, existing `watchtower-*` units, an
existing `/opt/watchtower` or non-empty `/var/lib/watchtower` without our marker, an existing `watchtower` user, and port 8000 in use.
Any conflict aborts with exit 4 unless `--force`. Re-running after a successful install is idempotent (marker present: it skips the
release build and refreshes units). `install.sh` **never enables `watchtower-collect.timer`**; `--start` and `--enable-backup-timer`
are the only things it enables.

The web unit refuses to start (`ExecStartPre=deploy/check-env.sh`) while the env file has placeholders, a short admin token, or a
non-absolute `WATCHTOWER_DB_URL`.

## 4. First-run checklist

1. `systemctl status watchtower-web` active; `curl -s http://127.0.0.1:8000/health` prints `ok` (the app creates the DB on first start).
2. `sudo -u watchtower /opt/watchtower/current/deploy/backup.sh` -> ends with `snapshot verified` (this runs `cli backup` then `cli verify`).
3. `/opt/watchtower/current/deploy/healthcheck.sh` -> exit 0.
4. `systemctl list-timers 'watchtower-*'` shows only `watchtower-backup.timer`; `systemctl is-enabled watchtower-collect.timer` is `disabled`.
5. `ss -ltnp | grep 8000` shows `127.0.0.1:8000` only.
6. Do steps in section 5 (nginx/TLS), then check from outside: `https://YOUR_DOMAIN/health`.

## 5. HTTPS with nginx + certbot

`deploy/nginx/watchtower.conf` is a template with `YOUR_DOMAIN` placeholders; `deploy/render-nginx.sh` refuses placeholder or
documentation domains. **The config has NOT been syntax-tested** (no nginx binary and no running docker daemon in the authoring
session): always run `nginx -t` before reload.

```bash
apt install nginx certbot
mkdir -p /var/www/certbot
WT_NGINX_TEMPLATE=watchtower-http-bootstrap.conf deploy/render-nginx.sh --domain YOUR.real.domain --out /etc/nginx/conf.d/watchtower-bootstrap.conf
nginx -t && systemctl reload nginx
certbot certonly --webroot -w /var/www/certbot -d YOUR.real.domain      # use your own contact email when prompted
rm /etc/nginx/conf.d/watchtower-bootstrap.conf
deploy/render-nginx.sh --domain YOUR.real.domain --out /etc/nginx/conf.d/watchtower.conf
nginx -t && systemctl reload nginx
```

(The bootstrap step exists because the full config references certificate files that do not exist before issuance.) If another site
already lives on this nginx, delete the `default_server` blocks (port 80 and 443) from the rendered file first. Note that a stock `apt install nginx` on Debian/Ubuntu already ships `sites-enabled/default` with `listen 80 default_server`, so this clash is the NORMAL case: remove that default site (`rm /etc/nginx/sites-enabled/default`) only if you own this nginx, and run `nginx -t` before reloading (a second default server is
an error). Certbot's packaged timer renews certificates; nothing else is installed for it by these scripts.

How the app and proxy must agree (`shopping/web/security.py`, `watchtower/config.py`):

* `WATCHTOWER_TRUST_PROXY=1`: client IP = **rightmost** `X-Forwarded-For` entry. nginx *overwrites* the header with `$remote_addr`, so a
  client-supplied value can never be the one used. Set this **only** when nginx is the sole way in (uvicorn binds 127.0.0.1;
  `--no-proxy-headers` is deliberate so there is one mechanism, not two).
* `WATCHTOWER_COOKIE_SECURE=1`: uvicorn sees plain HTTP from nginx, so without this the owner/admin cookies would not be `Secure`.
* **Origin/SameSite**: state-changing routes compare the `Origin`/`Referer` host with the `Host` header, and cookies are SameSite.
  nginx therefore forwards `Host $host` and the public name must equal what browsers use (one canonical name, port 443). A different
  name, a port suffix mismatch, or `proxy_set_header Host $proxy_host` makes every browser POST return 403 "Cross-origin request refused."
  Keep forms same-site; do not put the app behind a second hostname or an iframe (`frame-ancestors 'none'`).
* The app already sends CSP, X-Frame-Options, nosniff, Referrer-Policy and Permissions-Policy; nginx adds only HSTS (no duplicates),
  plus `server_tokens off`, 256 KB body limit, header/body/send timeouts, `limit_req` (10 r/s general, 6/min on `/admin/login`),
  `limit_conn`, and an optional commented `allow YOUR_ADMIN_IP; deny all;` for `/admin`.

## 6. Monitoring and logs (no cron)

* Logs: `journalctl -u watchtower-web -f`, `journalctl -u watchtower-backup -n 50`, nginx in `/var/log/nginx/watchtower.*.log`.
  Journald size is bounded by its own config (`SystemMaxUse=` in `/etc/systemd/journald.conf`): set it on a small disk.
* `deploy/healthcheck.sh` checks `/health` (must return exactly `ok`), the disk floor, then takes a fresh snapshot and runs
  `cli verify` on it (`--no-db` skips the snapshot for frequent polling). Exit 0 healthy, 1 not.
* Cron-free options: run it by hand after deploys; point a free external uptime monitor at `https://YOUR_DOMAIN/health`;
  or add your own systemd timer pair that runs `healthcheck.sh --no-db` (not shipped, not tested here). `Restart=on-failure` already
  restarts a crashed web process (max 5 starts per 5 minutes).
* Collection health: `python -m watchtower.cli health` (read-only against the DB). Note that `status` and every command except `backup`, `verify`, `plan`, `coverage`, `cleanup-preview` and `schedule` open the DB normally, which runs `init`/migrations (nullable columns) first: take a `backup` before the first normal open after an upgrade. Run CLI commands with the env loaded WITHOUT sourcing the file (it is parsed by the scripts, never sourced), e.g. `sudo systemd-run --pty -p User=watchtower -p EnvironmentFile=/etc/watchtower/watchtower.env /opt/watchtower/current/.venv/bin/python -m watchtower.cli health` (with `WorkingDirectory=/opt/watchtower/current`).

## 7. Backup, recovery, rollback

* **Backup**: `watchtower-backup.timer` (daily 03:30 + up to 20 min jitter) runs `backup.sh --prune` as `watchtower`:
  `cli backup --out` (SQLite online-backup API, WAL-safe, never `cp`), converts the new file to a single-file journal mode, `cli verify`.
  A snapshot that fails verification is renamed `*.UNVERIFIED` and nothing is pruned. **Pruning**: only with `--prune`, only regular
  files named `watchtower-<UTC>.db` directly inside the backup dir, only if older than 30 days **and** not among the newest 14.
  Without `--prune` it only lists candidates; `--dry-run` creates and deletes nothing. It refuses to run below the free-space floor.
  Backups on the same disk do not protect against disk loss: copy them off-box yourself (`scp`/`rsync` of the `.db` files is safe,
  they are self-contained).
* **Restore to a new path (safe, default)**: `deploy/restore.sh --from SNAP --to /some/new.db --apply`. It verifies the snapshot, copies via
  SQLite's backup API, verifies the copy, and refuses to overwrite anything. Without `--apply` it only verifies.
* **Replace the live DB**: `systemctl stop watchtower-web`, then `deploy/restore.sh --from SNAP --replace-live --force --apply`. It refuses
  while the service is active or without `--force`; the old `.db/-wal/-shm` are moved aside as `*.pre-restore-<UTC>` (delete them yourself
  when satisfied). Then `systemctl start watchtower-web` and run `healthcheck.sh`.
* **Update**: `deploy/update.sh --src /path/to/new/checkout` (dry run) then `--apply`. Order: pre-update verified snapshot -> new release
  dir + venv -> smoke import against a throw-away DB -> atomic symlink switch -> restart -> `/health` wait; if unhealthy it switches
  back automatically (exit 8). Releases beyond `--keep 3` are deleted; `current` and `previous` never are.
  The app runs `create_all` + `sync_columns` (adds nullable columns, never drops) on start, so the new code may already have altered
  the schema when you roll back.
* **Rollback (code only)**: `deploy/rollback.sh --apply` swaps `current` and `previous` (run again to roll forward); `--to <id>` picks a
  kept release. The DB is not touched. Whether older code tolerates a schema that newer code extended is **unverified**; if in doubt,
  restore the pre-update snapshot from `/var/lib/watchtower/backups` as above.

## 8. Enabling scheduled collection LATER (triple opt-in; off by default)

`watchtower-collect.timer` ships disabled and `install.sh` never enables it. Scheduled collection (the timer) happens only when **all** hold (a logged-in admin can still start ONE manual collection from the admin page: that path needs only the admin token plus the source policy gate, and ignores these switches):

1. `/var/lib/watchtower/schedule.json` has `"enabled": true` (ships `false`; sources/intervals/budgets are edited here, survive updates; min interval 12 h, budget ceiling 500).
2. `WATCHTOWER_SCHEDULER_ENABLED=1` in the env file (ships `0`).
3. You run `systemctl enable --now watchtower-collect.timer`.

On top of that the unit's `ExecCondition` skips the run unless `WATCHTOWER_CONTACT` passes the app's `contact_ok` (a real address or URL
that you own, not a placeholder), and each source must still pass `watchtower/policy.py` (reviewed + current + approved policy, robots.txt,
host allowlist, per-host delay, budget). A 401/403/451 stops that source and it is not retried unattended.

Before step 1, per `docs/SCHEDULING.md`: put your real contact in the env file; complete the **legal read of each merchant's terms**
(policy status "approved" means no prohibition was found, not permission); take and verify a backup; understand that "Verified" deals need
3+ observations over 7+ days, so plan at least eight consecutive days. The collect unit has `Persistent=false`, so downtime never
triggers a catch-up burst. To stop: `systemctl disable --now watchtower-collect.timer` (and/or set `enabled:false`).

## 9. Security checklist

- [ ] No `YOUR_*` left in `/etc/watchtower/watchtower.env`; mode 0640 `root:watchtower`; never committed.
- [ ] `WATCHTOWER_ADMIN_TOKEN` >= 20 random characters (shorter or unset = admin stays disabled, which is safe).
- [ ] `WATCHTOWER_TRUST_PROXY=1` and `WATCHTOWER_COOKIE_SECURE=1` only behind nginx; port 8000 not reachable externally (`ss -ltn`, firewall allows 22/80/443 only; configure with your provider's firewall or `ufw`, not done by these scripts).
- [ ] `systemd-analyze security watchtower-web.service` reviewed (hardening: NoNewPrivileges, ProtectSystem=strict, ProtectHome, PrivateTmp/Devices, empty capability set, syscall filter, only the state dir writable).
- [ ] SSH key-only login, OS security updates enabled (outside the scope of these scripts).
- [ ] Off-box copy of backups; restore rehearsed once (section 7).
- [ ] eBay variables unset: that adapter is scaffolding and must not be described or run as live.
- [ ] Collection timer still disabled until section 8 is satisfied.

## 10. Uninstall (manual; no script on purpose)

`systemctl disable --now watchtower-web watchtower-backup.timer watchtower-collect.timer`, delete the five `watchtower-*` unit files and
`systemctl daemon-reload`, then remove `/opt/watchtower`, `/etc/watchtower`, the nginx file and (only if you want to lose the data)
`/var/lib/watchtower` and the `watchtower` user.

## 11. What was verified here vs. not

| Item | Status |
|---|---|
| `bash -n` on every script; `shellcheck -x -S style` (v0.11.0 via pip in a scratch venv) | clean, rc 0 |
| `systemd-analyze verify` on all 5 units as written | only "Command ... is not executable" for `/opt/watchtower/...` (the files exist only after install); `User=watchtower` raised no warning |
| `systemd-analyze verify` on copies with paths rewritten to existing stand-in files | rc 0, no output; `systemd-analyze security --offline` on the web unit: exposure 1.7 "OK" |
| `install.sh` dry run (no root, no env file): prints plan, creates nothing | done |
| Abort paths: disk below threshold, RAM below threshold, user already exists, placeholder env file, python < 3.11 (simulated) | done (exit 3 / 3 / 4 / 5 / "not found") |
| `install.sh --apply` into a temp prefix with real `python -m venv` + `pip install -r requirements-prod.txt` + smoke import, units written with substituted paths; re-run idempotent | done (Python 3.13, ~20 s) |
| `healthcheck.sh` against a real uvicorn on a spare localhost port with a temp DB | done (rc 0) |
| `backup.sh`: dry run, real snapshot + verify, retention (20 aged fake snapshots; symlink and unrelated file untouched; `--dry-run --prune` deletes nothing) | done |
| `restore.sh`: dry run, restore to new path, refusal to overwrite, refusal without `--force`, refusal while service "active" (stubbed `systemctl`), refusal of `.UNVERIFIED` and of a corrupt file, replace-live moving old db/wal/shm aside | done (temp DB only) |
| `update.sh`: dry run, healthy update (pre-update snapshot, new release, switch, prune), unhealthy update auto-reverted (incl. `previous`) | done with `systemctl` skipped/stubbed |
| `rollback.sh`: dry run, apply, roll forward, `--to` validation, no-marker refusal | done |
| `ExecCondition` contact gate and `schedule run-due` with the shipped disabled `schedule.json` | done (real `config.contact_ok`; "scheduling is disabled; nothing was run") |
| nginx config | **NOT tested** (no nginx; docker daemon not running). Run `nginx -t` |
| Real systemd behaviour: start/stop/restart, hardening directives actually permitting uvicorn/backup to run (e.g. `SystemCallFilter`, `ReadWritePaths`), timers firing, `ExecCondition`, `runuser` path in `update.sh` | **NOT tested** (no systemd instance to run units, no root-managed user) |
| `useradd`, ownership/permissions (`watchtower` user, 0640/0750 modes), `chgrp` of env file | **NOT tested** (a real user must not be created here; tests ran as the current user) |
| certbot issuance/renewal, HTTP->HTTPS, HSTS, rate limits, Origin/Host behaviour through nginx | **NOT tested** |
| Python 3.11/3.12; Debian/Ubuntu package names; Contabo disk/RAM; real memory/DB growth | **NOT tested / unmeasured** (only Python 3.13 on Ubuntu 24.04) |
| Rollback tolerance of older code to a newer schema | **unverified** |
| Dependency versions | `requirements-lock.txt` is a full freeze (starlette 1.10.0, pydantic 2.14.3, ...) of a clean install of `requirements-prod.txt`; the FULL test suite was run against exactly these versions (448 passed, 4 optional-ML tests skipped, Python 3.13, Linux x86_64). `install.sh` installs the lock when the release ships it. Still NOT exercised: Python 3.11/3.12 and the VPS. Known test-only warning: Starlette's TestClient will require `httpx2` in a future release (tests only, not the runtime) |

Known limits: after a successful `install --apply` for the first time, nothing has created the DB until the web service starts once;
`backup.sh` says so and exits. `update.sh` keeps failed release directories until the next successful update prunes them.
