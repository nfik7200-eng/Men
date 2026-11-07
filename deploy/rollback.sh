#!/usr/bin/env bash
# Switch back to the previous release (code only). DRY-RUN by default.
#   rollback.sh [--to RELEASE_ID] [--apply]
# Default target is the `previous` symlink. The two symlinks are swapped, so running it again rolls forward.
# The database is NOT touched. If the new code altered data, restore a pre-update snapshot explicitly:
#   systemctl stop watchtower-web; restore.sh --from <snapshot> --replace-live --force --apply
set -euo pipefail
# shellcheck source=lib.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
TO=""
while [ $# -gt 0 ]; do
  case "$1" in --to) TO="${2:?}"; shift ;; -h|--help) sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;; *) parse_common_flag "$1" || die "unknown option $1" 2 ;; esac; shift
done
[ "$DRY" = 1 ] && log "DRY-RUN mode (default): nothing will be changed."
is_ours || die "no marker: not a managed prefix"
[ -L "$WT_PREFIX/current" ] || die "no current symlink"
CUR="$(readlink "$WT_PREFIX/current")"
if [ -n "$TO" ]; then
  [[ "$TO" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || die "--to must be a release id like 20260101T000000Z"
  TARGET="releases/$TO"
else
  [ -L "$WT_PREFIX/previous" ] || die "no previous release recorded"
  TARGET="$(readlink "$WT_PREFIX/previous")"
fi
[ -d "$WT_PREFIX/$TARGET" ] || die "release $WT_PREFIX/$TARGET does not exist"
[ "$TARGET" != "$CUR" ] || die "already on $CUR"
[ -f "$WT_PREFIX/$TARGET/main.py" ] && [ -x "$WT_PREFIX/$TARGET/.venv/bin/python" ] \
  || die "release $TARGET is incomplete (needs main.py and .venv/bin/python): a failed or half-built release cannot be rolled back to"
need_root
OLD_PREV="$(readlink "$WT_PREFIX/previous" 2>/dev/null || true)"   # remembered so an auto-revert can restore it
log "current: $CUR  ->  $TARGET"
run ln -sfn "$CUR" "$WT_PREFIX/previous.tmp"; run mv -T "$WT_PREFIX/previous.tmp" "$WT_PREFIX/previous"
swap_symlink "$TARGET" "$WT_PREFIX/current"
run sysctl_ restart watchtower-web.service
if [ "$DRY" = 0 ] && [ "${WT_NO_SYSTEMCTL:-0}" != 1 ]; then
  if ! wait_healthy; then   # revert the switch instead of leaving the site down
    ln -sfn "$CUR" "$WT_PREFIX/current.tmp" && mv -T "$WT_PREFIX/current.tmp" "$WT_PREFIX/current"
    if [ -n "$OLD_PREV" ]; then ln -sfn "$OLD_PREV" "$WT_PREFIX/previous.tmp" && mv -T "$WT_PREFIX/previous.tmp" "$WT_PREFIX/previous"; fi
    sysctl_ restart watchtower-web.service || true
    die "service unhealthy after rollback: switched back to $CUR (see journalctl -u watchtower-web -n 100)" 8
  fi
fi
log "rollback done (code only; DB untouched)"
