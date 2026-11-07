#!/usr/bin/env bash
# Health check: HTTP /health must answer "ok", and a FRESH snapshot must pass `cli verify`. Exit 0 healthy, 1 unhealthy.
#   healthcheck.sh [--no-db] [--url URL]
# --no-db skips the snapshot (cheap check for frequent polling). Cron-free: see docs/DEPLOYMENT.md.
set -euo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
DB=1; URL="http://127.0.0.1:$WT_PORT/health"
while [ $# -gt 0 ]; do
  case "$1" in --no-db) DB=0 ;; --url) URL="${2:?}"; shift ;; -h|--help) sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;; *) die "unknown option $1" 2 ;; esac; shift
done
rc=0
body="$(curl -fsS --max-time 5 "$URL" 2>&1)" || { log "FAIL http: $body"; exit 1; }
[ "$body" = ok ] && log "ok   http $URL" || { log "FAIL http: unexpected body '$body'"; rc=1; }
log "ok   disk: $(free_gb_of "$WT_STATE") GB free on $WT_STATE"
[ "$(free_gb_of "$WT_STATE")" -ge "$WT_MIN_FREE_GB" ] || { log "FAIL disk below ${WT_MIN_FREE_GB} GB floor"; rc=1; }
if [ "$DB" = 1 ]; then
  [ -f "$WT_ENV_FILE" ] && load_env "$WT_ENV_FILE"
  PY="$(app_py)"; APP="$(app_dir)"; mkdir -p "$WT_BACKUP_DIR"
  tmp="$WT_BACKUP_DIR/.healthcheck-$$.db"; trap 'rm -f -- "$tmp"' EXIT
  if ( cd "$APP" && "$PY" -m watchtower.cli backup --out "$tmp" >/dev/null ) && ( cd "$APP" && "$PY" -m watchtower.cli verify --db "$tmp" | tail -n1 ); then
    log "ok   fresh snapshot verified"
  else log "FAIL snapshot/verify"; rc=1; fi
fi
exit "$rc"
