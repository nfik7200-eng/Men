#!/usr/bin/env bash
# WAL-safe snapshot of the live DB via `python -m watchtower.cli backup` (never cp), then `cli verify` on the snapshot.
#   backup.sh [--dry-run] [--prune] [--keep N] [--min-age-days D] [--dir DIR]
# Pruning (only with --prune, and only after THIS run's snapshot verified): deletes regular files named
# watchtower-<UTC>.db directly inside the backup dir, ONLY if they are older than D days (default 30) AND not among the
# newest N (default 14). Nothing else is ever deleted. Without --prune it only lists what it would delete.
set -euo pipefail
# shellcheck source=lib.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
DRY=0; PRUNE=0; KEEP=14; AGE=30; DIR="$WT_BACKUP_DIR"
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;; --prune) PRUNE=1 ;;
    --keep) KEEP="${2:?}"; shift ;; --min-age-days) AGE="${2:?}"; shift ;; --dir) DIR="${2:?}"; shift ;;
    -h|--help) sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option $1" 2 ;;
  esac; shift
done
[[ "$KEEP" =~ ^[0-9]+$ ]] && [ "$KEEP" -ge 1 ] || die "--keep must be an integer >= 1"
[[ "$AGE" =~ ^[0-9]+$ ]] || die "--min-age-days must be an integer"
[ -f "$WT_ENV_FILE" ] && load_env "$WT_ENV_FILE"
LIVE="$(db_path)" || die "WATCHTOWER_DB_URL must be set to an absolute sqlite URL (env file: $WT_ENV_FILE)"
[ -f "$LIVE" ] || die "live DB $LIVE does not exist yet (start the service once, or nothing to back up)"
PY="$(app_py)"; APP="$(app_dir)"
[ -x "$PY" ] || die "runtime python $PY not found"

# space: need the DB (+WAL) again, with headroom, and never dip below the free-space floor
need_mb=$(( ( $(du -k "$LIVE"* 2>/dev/null | awk '{s+=$1} END {print s+0}') / 1024 ) * 2 + 1 ))
[ "$DRY" = 1 ] || mkdir -p "$DIR" 2>/dev/null || die "cannot create $DIR"
free_mb=$(( $(free_gb_of "$DIR") * 1024 ))
[ "$(free_gb_of "$DIR")" -ge "$WT_MIN_FREE_GB" ] || die "only $(free_gb_of "$DIR") GB free in $DIR (< ${WT_MIN_FREE_GB} GB floor): not creating a backup" 3
[ "$free_mb" -ge "$need_mb" ] || die "need ~${need_mb} MB for the snapshot, ${free_mb} MB free" 3

OUT="$DIR/watchtower-$(date -u +%Y%m%dT%H%M%SZ).db"
if [ "$DRY" = 1 ]; then
  log "DRY-RUN: would run: (cd $APP && $PY -m watchtower.cli backup --out $OUT) then: $PY -m watchtower.cli verify --db $OUT"
else
  ( cd "$APP" && "$PY" -m watchtower.cli backup --out "$OUT" ) || die "backup failed"
  # Make the snapshot a single self-contained file: the CLI leaves it in WAL mode, which would leave -wal/-shm
  # sidecars next to every backup. Only the NEW snapshot is touched; verify below runs after this.
  "$PY" -I -c 'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute("PRAGMA wal_checkpoint(TRUNCATE)"); print("journal_mode ->", c.execute("PRAGMA journal_mode=DELETE").fetchone()[0]); c.close()' "$OUT" || true
  rm -f -- "$OUT-wal" "$OUT-shm"
  if ! ( cd "$APP" && "$PY" -m watchtower.cli verify --db "$OUT" ) ; then
    mv -- "$OUT" "$OUT.UNVERIFIED" 2>/dev/null || true
    die "snapshot FAILED verification; kept as $OUT.UNVERIFIED (never pruned, never used by restore). Not pruning." 6
  fi
  log "snapshot verified: $OUT ($(du -h "$OUT" | cut -f1))"
fi

# ---- retention: only watchtower-<UTC>.db regular files directly in $DIR
mapfile -t files < <(find "$DIR" -maxdepth 1 -type f -name 'watchtower-????????T??????Z.db' -printf '%T@ %p\n' 2>/dev/null | sort -rn | cut -d' ' -f2-)
total="${#files[@]}"; n=0
for f in "${files[@]}"; do
  n=$((n + 1))
  [ "$n" -le "$KEEP" ] && continue
  if [ -n "$(find "$f" -maxdepth 0 -type f -mtime +"$AGE" -print)" ]; then
    if [ "$PRUNE" = 1 ] && [ "$DRY" = 0 ]; then rm -f -- "$f" "$f-wal" "$f-shm"; log "pruned $f"; else log "would prune (use --prune): $f"; fi
  fi
done
log "retention: $total snapshot(s) present, keeping newest $KEEP and anything younger than $AGE days"
