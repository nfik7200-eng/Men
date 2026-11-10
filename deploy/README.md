# Deploying BIP to a VPS

This directory is a **kit, not a deployment**. Nothing here has been applied to any machine, and
BIP was never run on the target VPS. Sizes and timings for that host are unknown until measured
there. Every file is an example to copy and adapt.

BIP is one Python process (API plus an optional background scheduler) with SQLite in WAL mode. A
reverse proxy terminates HTTPS, serves the built frontend, and forwards `/api/*` to the process on
loopback. There is no queue, cache or second database.

## What you need

* Linux with systemd, Python 3.12+, [uv](https://docs.astral.sh/uv/), and a reverse proxy
  (Caddy or nginx). Node 20+ only on the machine that builds the frontend.
* A DNS name for the site and ports 80/443, **or** a private network (Tailscale/WireGuard) and
  `tls internal`. Prefer the private network for a single-user instrument.
* Storage with room for `BIP_MAX_DATA_BYTES` plus `BIP_MIN_FREE_BYTES` (5 GiB each by default).
  The target VPS is ~94% full: put `BIP_DATA_ROOT` on a separate volume.

## Install

```bash
# 1. service account and directories
sudo useradd --system --home /opt/bip --shell /usr/sbin/nologin bip
sudo install -d -o bip -g bip -m 750 /opt/bip /var/lib/bip
sudo install -d -o root -g bip -m 750 /etc/bip
sudo install -d -o root -g root -m 755 /srv/bip   # static files: readable by the proxy only

# 2. code and dependencies (from a checkout of the reviewed commit)
sudo -u bip git clone https://github.com/nfik7200-eng/Men /opt/bip/app
cd /opt/bip/app && sudo -u bip git checkout <reviewed-commit>   # never deploy a branch tip unseen
sudo -u bip uv sync --locked --no-dev

# 3. frontend, built anywhere and copied (the host does not need Node)
( cd frontend && npm ci && npm run build )
sudo rsync -a --delete frontend/dist/ /srv/bip/web/

# 4. configuration
sudo cp deploy/bip.env.production.example /etc/bip/bip.env
sudo chown root:bip /etc/bip/bip.env && sudo chmod 640 /etc/bip/bip.env
sudoedit /etc/bip/bip.env            # set BIP_API__CORS_ORIGINS to your site

# 5. check it. FAIL lines must be fixed; WARN lines are decisions. (The unit runs the same check
#    with --use-environment, judging exactly the environment systemd passes the service.)
sudo -u bip env -i /opt/bip/app/.venv/bin/python -m bip.tools.preflight --env-file /etc/bip/bip.env

# 6. the first administrator (password from a prompt or a root-only file, never an argument).
#    systemd-run reads /etc/bip/bip.env itself, so no value ever appears on a command line.
sudo systemd-run --pty --wait --collect --uid=bip --property=EnvironmentFile=/etc/bip/bip.env \
    --property=WorkingDirectory=/opt/bip/app \
    /opt/bip/app/.venv/bin/python -m bip.tools.users create admin --role admin

# 7. service
sudo cp deploy/systemd/bip-api.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now bip-api

# 8. proxy: copy deploy/caddy/Caddyfile (or deploy/nginx/bip.conf), set the host name, reload
```

The unit runs the pre-flight check before every start, so an unsafe edit (authentication off, a
non-Secure cookie, a reachable bind with proxy headers trusted, a world-readable env file) stops
the service instead of exposing it.

## Verify

```bash
curl -fsS https://bip.example.org/api/v1/health            # public, minimal: {"status": "ok"}
curl -i  https://bip.example.org/api/v1/system/health      # 401 without a session: protection works
```

Sign in at the site, then read the detailed report at `GET /api/v1/system/health` (database and WAL
size, disk, scheduler jobs, backups, provider summary). Warning codes are listed in
`docs/OPERATIONS.md`.

## Backups and restore

The `backup` job (enabled in the template) writes verified SQLite snapshots under
`$BIP_DATA_ROOT/operational/backups/` and keeps the newest `BIP_WORKERS__BACKUP_KEEP`. They share
the disk with the database, so copy them elsewhere: `systemd/bip-backup-offsite.*` is an rsync
timer to adapt. **Restore is manual by design**; the steps are in `docs/OPERATIONS.md`.

## Upgrade and roll back

```bash
sudo systemctl stop bip-api
sudo -u bip cp -a /var/lib/bip/operational/bip.sqlite3 /var/lib/bip/operational/bip.sqlite3.pre-upgrade
cd /opt/bip/app && sudo -u bip git fetch && sudo -u bip git checkout <reviewed-commit>
sudo -u bip uv sync --locked --no-dev        # migrations run on start, are additive and idempotent
sudo systemctl start bip-api
```

Roll back by checking out the previous commit. Migrations only add tables, so an older release
ignores them; **BIP refuses to open a database newer than its code**, so if you must go back past
a migration, restore the `pre-upgrade` copy as well.

## Turning collection on (off by default)

Collection contacts public RPC endpoints. Keep it off until you have chosen a **free, read-only**
endpoint, read its published limits, and confirmed they allow the traffic. Each invocation is
capped by validation at 150 requests and 90 seconds per chain.

1. Run one invocation by hand as an administrator and read the manifest it returns:
   `POST /api/v1/discovery/collect/ethereum` and `/solana` (an administrator session and its CSRF
   header are required). It reports requests used, coverage, gaps and what it did not cover.
2. Only then set `BIP_WORKERS__COLLECTOR_ENABLED=true` and/or
   `BIP_WORKERS__SOLANA_COLLECTOR_ENABLED=true`, restart, and watch `GET /api/v1/system/jobs`.

The pre-flight check reports an enabled collection job as a WARN so it is never on by accident.

## Known limits

* **Client address behind the proxy.** The HTTP server rewrites the peer address from
  `X-Forwarded-For` for any caller on loopback, whatever `BIP_AUTH__TRUST_PROXY_HEADERS` says. On a
  host shared with other software, a local process can therefore spoof the address BIP uses for
  throttling and in the audit log. Login brute force is still bounded by the per-account lockout.
  Remedies: do not run untrusted software on the host, or reach the API through a Unix socket
  (not wired up in this kit).
* **Pool and token coverage rests on factories someone verified.** Only Uniswap v2/v3 factories
  with a documented address feed token transfers and reserve watching; add others after verifying
  them with `BIP_COLLECTOR__EXTRA_FACTORIES`.
* **Ownership.** The unit runs as `bip`, which also owns the code under `/opt/bip/app`; for a
  stricter setup make the code root-owned and read-only to `bip`.

## What this kit does not do

* It does not deploy, open firewall ports, request certificates, or contact any provider.
* It does not provide off-host backups, monitoring/alerting, or log shipping; the `/system/health`
  report and journald JSON logs are the hooks for those.
* It does not make SQLite highly available. One writer, one host. See "SQLite: WAL and locking
  assessment" in `docs/OPERATIONS.md` for when that stops being enough.
