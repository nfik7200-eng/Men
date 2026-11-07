#!/usr/bin/env bash
# Restore a snapshot made by backup.sh. DRY-RUN by default.
#   restore.sh --from SNAPSHOT [--to NEWPATH] [--apply]
#       verifies SNAPSHOT, copies it with SQLite's backup API (not cp) to NEWPATH (must NOT exist; default
#       $WT_STATE/restore/watchtower-restored-<UTC>.db), verifies the copy. The live DB is not touched.
#   restore.sh --from SNAPSHOT --replace-live --force --apply
#       additionally installs it as the live DB: requires watchtower-web.service to be STOPPED; the old DB, -wal and
#       -shm are MOVED aside (suffix .pre-restore-<UTC>), never deleted.
set -euo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
FROM=""; TO=""; REPLACE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --from) FROM="${2:?}"; shift ;; --to) TO="${2:?}"; shift ;; --replace-live) REPLACE=1 ;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) parse_common_flag "$1" || die "unknown option $1" 2 ;;
  esac; shift
done
[ -n "$FROM" ] && [ -f "$FROM" ] || die "--from SNAPSHOT is required and must be a file"
case "$FROM" in *.UNVERIFIED) die "refusing an .UNVERIFIED snapshot" ;; esac
[ -f "$WT_ENV_FILE" ] && load_env "$WT_ENV_FILE"
PY="$(app_py)"; APP="$(app_dir)"; TS="$(date -u +%Y%m%dT%H%M%SZ)"
[ -x "$PY" ] || die "runtime python $PY not found"
[ "$DRY" = 1 ] && log "DRY-RUN mode (default): only verifying the snapshot; --apply to restore."

log "verifying snapshot $FROM"
( cd "$APP" && "$PY" -m watchtower.cli verify --db "$FROM" | tail -n1 ) || die "snapshot failed verification; not restoring" 6

if [ "$REPLACE" = 1 ]; then
  LIVE="$(db_path)" || die "WATCHTOWER_DB_URL not set to an absolute sqlite URL"
  [ "$FORCE" = 1 ] || die "--replace-live requires --force" 7
  if service_active; then die "watchtower-web.service is active: stop it first (systemctl stop watchtower-web.service)" 7; fi
  [ "${WT_NO_SYSTEMCTL:-0}" = 1 ] && warn "systemctl check skipped (test mode): make sure nothing is using $LIVE"
  DEST="$LIVE"
else
  DEST="${TO:-$WT_STATE/restore/watchtower-restored-$TS.db}"
  [ ! -e "$DEST" ] || die "$DEST already exists: restore never overwrites; choose another --to" 7
fi
[ "$(free_gb_of "$(dirname "$DEST")")" -ge "$WT_MIN_FREE_GB" ] || die "less than ${WT_MIN_FREE_GB} GB free near $DEST" 3
TMP="$(dirname "$DEST")/.restore-$TS-$$.tmp"

if [ "$DRY" = 1 ]; then
  log "DRY-RUN: would copy $FROM -> $TMP (sqlite backup API), verify, then $DEST"
  [ "$REPLACE" = 1 ] && log "DRY-RUN: would move $LIVE{,-wal,-shm} aside to *.pre-restore-$TS"
  exit 0
fi
need_root
mkdir -p "$(dirname "$DEST")"
trap 'rm -f -- "$TMP"' EXIT
"$PY" -I - "$FROM" "$TMP" <<'PYEOF'
import sqlite3, sys
src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.execute("PRAGMA journal_mode=DELETE")
dst.close(); src.close()
PYEOF
( cd "$APP" && "$PY" -m watchtower.cli verify --db "$TMP" | tail -n1 ) || die "restored copy failed verification; nothing was installed" 6
if [ "$REPLACE" = 1 ]; then
  for sfx in "" -wal -shm; do [ -e "$LIVE$sfx" ] && mv -- "$LIVE$sfx" "$LIVE$sfx.pre-restore-$TS"; done
  log "old DB files moved aside: $LIVE*.pre-restore-$TS"
fi
mv -n -- "$TMP" "$DEST"
[ -e "$TMP" ] && die "could not place $DEST (appeared meanwhile?)"
if id "$WT_USER" >/dev/null 2>&1; then chown "$WT_USER:$WT_USER" "$DEST" 2>/dev/null || true; fi
chmod 0640 "$DEST"
log "restored: $DEST"
