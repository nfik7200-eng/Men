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
need_root
log "current: $CUR  ->  $TARGET"
run ln -sfn "$CUR" "$WT_PREFIX/previous.tmp"; run mv -T "$WT_PREFIX/previous.tmp" "$WT_PREFIX/previous"
swap_symlink "$TARGET" "$WT_PREFIX/current"
run sysctl_ restart watchtower-web.service
if [ "$DRY" = 0 ] && [ "${WT_NO_SYSTEMCTL:-0}" != 1 ]; then wait_healthy || die "service unhealthy after rollback: journalctl -u watchtower-web -n 100" 8; fi
log "rollback done (code only; DB untouched)"
