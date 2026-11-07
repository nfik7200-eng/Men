#!/usr/bin/env bash
# Deploy a new release from a source tree. DRY-RUN by default.
#   update.sh --src DIR [--apply] [--keep N]
# Order: validate env -> pre-update DB snapshot (backup.sh of the CURRENT release, verified) -> build new release dir +
# venv + smoke import (against a temp DB) -> atomic switch of `current` (old one kept as `previous`) -> restart ->
# health check; on failure the switch is reverted automatically. Old release dirs beyond --keep (default 3) are removed;
# `current` and `previous` never are. The DB is NOT rolled back automatically: the pre-update snapshot path is printed.
set -euo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
SRC=""; KEEP=3
while [ $# -gt 0 ]; do
  case "$1" in
    --src) SRC="$(cd "${2:?}" && pwd)"; shift ;; --keep) KEEP="${2:?}"; shift ;;
    -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) parse_common_flag "$1" || die "unknown option $1" 2 ;;
  esac; shift
done
[ -n "$SRC" ] || die "--src DIR (a checkout of the new version) is required" 2
[[ "$KEEP" =~ ^[0-9]+$ ]] && [ "$KEEP" -ge 2 ] || die "--keep must be >= 2"
[ "$DRY" = 1 ] && log "DRY-RUN mode (default): nothing will be changed."
is_ours || die "$WT_PREFIX was not created by install.sh (no marker): refusing"
[ -L "$WT_PREFIX/current" ] || die "no current release symlink"
validate_env "$WT_ENV_FILE" || die "env file not valid" 5
check_disk "$WT_PREFIX" "$WT_STATE"
need_root
OLD="$(readlink "$WT_PREFIX/current")"; ID="$(new_release_id)"

log "== pre-update backup =="
if [ "$DRY" = 1 ]; then log "DRY-RUN: would run as $WT_USER: $(app_dir)/deploy/backup.sh"; else
  as_wt "$(app_dir)/deploy/backup.sh" || die "pre-update backup failed: aborting before any change" 6
fi
log "== build =="
make_release "$SRC" "$ID"
log "== switch =="
run ln -sfn "$OLD" "$WT_PREFIX/previous.tmp"; run mv -T "$WT_PREFIX/previous.tmp" "$WT_PREFIX/previous"
swap_symlink "releases/$ID" "$WT_PREFIX/current"
run sysctl_ restart watchtower-web.service
if [ "$DRY" = 0 ]; then
  if [ "${WT_NO_SYSTEMCTL:-0}" = 1 ] || wait_healthy 20; then log "update ok: current -> releases/$ID (previous -> $OLD)"; else
    warn "health check FAILED after switch: reverting to $OLD"
    ln -sfn "$OLD" "$WT_PREFIX/current.tmp"; mv -T "$WT_PREFIX/current.tmp" "$WT_PREFIX/current"
    sysctl_ restart watchtower-web.service || true
    die "update reverted; inspect: journalctl -u watchtower-web -n 100. DB snapshot from before the update is in $WT_BACKUP_DIR (restore.sh if the new code altered data)" 8
  fi
fi
log "== prune old releases (keep $KEEP, never current/previous) =="
cur="$(basename "$(readlink "$WT_PREFIX/current" 2>/dev/null || echo "releases/$ID")")"; prev="$(basename "$OLD")"
i=0
while IFS= read -r d; do
  i=$((i + 1)); b="$(basename "$d")"
  [ "$i" -le "$KEEP" ] && continue
  [ "$b" = "$cur" ] || [ "$b" = "$prev" ] && continue
  run rm -rf -- "$d"
done < <(find "$WT_PREFIX/releases" -mindepth 1 -maxdepth 1 -type d -name '2*T*Z' 2>/dev/null | sort -r)
