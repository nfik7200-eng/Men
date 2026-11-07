#!/usr/bin/env bash
# Watchtower first-time install. DRY-RUN by default: prints what it would change, changes nothing.
#   install.sh --init-env --apply      create /etc/watchtower/watchtower.env from .env.example (placeholders; edit it)
#   install.sh                         dry run (preflight + plan)
#   install.sh --apply [--start] [--enable-backup-timer] [--src DIR] [--force]
# Never enables watchtower-collect.timer. Never overwrites foreign units/dirs/ports unless --force.
set -euo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

SRC="$(cd "$LIB_DIR/.." && pwd)"; START=0; BTIMER=0; INITENV=0
usage() { sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }
while [ $# -gt 0 ]; do
  case "$1" in
    --src) SRC="$(cd "${2:?--src needs a dir}" && pwd)"; shift ;;
    --start) START=1 ;;
    --enable-backup-timer) BTIMER=1 ;;
    --init-env) INITENV=1 ;;
    -h|--help) usage 0 ;;
    *) parse_common_flag "$1" || { echo "unknown option $1" >&2; usage 2; } ;;
  esac
  shift
done

[ "$DRY" = 1 ] && log "DRY-RUN mode (default): nothing will be changed. Re-run with --apply to execute."

# ------------------------------------------------------------------ preflight (read-only)
log "== preflight =="
[ "$(uname -s)" = Linux ] || die "Linux only"
if [ -r /etc/os-release ]; then . /etc/os-release; log "OS: ${PRETTY_NAME:-unknown}"; case "${ID:-}${ID_LIKE:-}" in *debian*|*ubuntu*) : ;; *) warn "not Debian/Ubuntu: untested, package hints below may not apply" ;; esac; fi
if [ "${WT_NO_SYSTEMCTL:-0}" != 1 ]; then
  [ -d /run/systemd/system ] || die "systemd is not running (set WT_NO_SYSTEMCTL=1 only for tests)"
  command -v systemctl >/dev/null || die "systemctl missing"
fi
command -v curl >/dev/null || die "curl missing (apt install curl)"
check_ram
check_disk "$WT_PREFIX" "$WT_STATE"
PY="$(find_python)" || die "python >= 3.11 not found; install python3.11+ and python3-venv (or set WT_PYTHON)"
log "python: $PY ($("$PY" -V 2>&1))"
"$PY" -c 'import venv, ensurepip' 2>/dev/null || die "python venv/ensurepip missing (Debian/Ubuntu: apt install python3-venv)"
[ -f "$SRC/requirements-prod.txt" ] || die "$SRC/requirements-prod.txt missing"

conflicts=()
for u in watchtower-web.service watchtower-backup.service watchtower-backup.timer watchtower-collect.service watchtower-collect.timer; do
  if [ -e "$WT_UNIT_DIR/$u" ] && ! is_ours; then conflicts+=("unit $WT_UNIT_DIR/$u already exists and was not installed by these scripts"); fi
done
if [ -e "$WT_PREFIX" ] && ! is_ours; then conflicts+=("directory $WT_PREFIX exists and has no $WT_MARKER marker"); fi
if [ -e "$WT_STATE" ] && ! is_ours && [ -n "$(ls -A "$WT_STATE" 2>/dev/null || true)" ]; then conflicts+=("state dir $WT_STATE exists and is not empty"); fi
if id "$WT_USER" >/dev/null 2>&1 && ! is_ours; then conflicts+=("user '$WT_USER' already exists"); fi
if port_in_use "$WT_PORT" && ! { is_ours && service_active; }; then conflicts+=("port $WT_PORT is already in use by something else"); fi
if [ "${#conflicts[@]}" -gt 0 ]; then
  for c in "${conflicts[@]}"; do warn "conflict: $c"; done
  [ "$FORCE" = 1 ] || die "refusing to touch existing resources; resolve them or re-run with --force (WT_PORT / WT_PREFIX / WT_USER can relocate)" 4
  warn "--force given: continuing despite conflicts above"
fi

# ------------------------------------------------------------------ --init-env
if [ "$INITENV" = 1 ]; then
  need_root
  if [ -e "$WT_ENV_FILE" ]; then log "env file $WT_ENV_FILE already exists; leaving it untouched"; exit 0; fi
  run install -d -m 0750 "$WT_ETC"
  run install -m 0640 "$SRC/.env.example" "$WT_ENV_FILE"
  [ "$DRY" = 1 ] || { getent group "$WT_USER" >/dev/null && chgrp "$WT_USER" "$WT_ETC" "$WT_ENV_FILE" || warn "group $WT_USER not yet created: run install --apply once, then chgrp $WT_USER $WT_ENV_FILE"; }
  log "now edit $WT_ENV_FILE: replace every YOUR_* placeholder. install --apply refuses until you do."
  exit 0
fi

# ------------------------------------------------------------------ env validation
log "== env file =="
if ! validate_env "$WT_ENV_FILE"; then
  if [ "$DRY" = 1 ]; then warn "env file not usable yet: a real --apply would REFUSE here."; else die "fix $WT_ENV_FILE first (see messages above)" 5; fi
fi

# ------------------------------------------------------------------ plan / apply
log "== plan =="
need_root
ID="$(new_release_id)"
if ! id "$WT_USER" >/dev/null 2>&1; then
  run useradd --system --user-group --home-dir "$WT_STATE" --no-create-home --shell /usr/sbin/nologin "$WT_USER"
fi
run install -d -m 0755 "$WT_PREFIX" "$WT_PREFIX/releases" "$WT_UNIT_DIR"
if [ "$DRY" = 1 ]; then printf '  DRY-RUN: write marker %s\n' "$WT_MARKER"; else date -u +%FT%TZ > "$WT_MARKER"; fi
run install -d -m 0750 -o "$WT_USER" -g "$WT_USER" "$WT_STATE" "$WT_BACKUP_DIR"
run install -d -m 0750 "$WT_ETC"
if [ -d "$WT_PREFIX/releases" ] && [ -L "$WT_PREFIX/current" ] && is_ours; then
  log "already installed (current -> $(readlink "$WT_PREFIX/current")): skipping release build. Use update.sh to deploy new code."
else
  make_release "$SRC" "$ID"
  swap_symlink "releases/$ID" "$WT_PREFIX/current"
fi
# schedule.json lives in the state dir so the operator's opt-in survives updates; ships DISABLED.
if [ ! -e "$WT_STATE/schedule.json" ]; then
  run install -m 0640 -o "$WT_USER" -g "$WT_USER" "$SRC/data/schedule.json" "$WT_STATE/schedule.json"
fi
# group ownership of the env file so the service user can read it (systemd itself reads it as root)
[ "$DRY" = 1 ] || { chgrp "$WT_USER" "$WT_ETC" "$WT_ENV_FILE"; chmod 0640 "$WT_ENV_FILE"; chmod 0750 "$WT_ETC"; }

for u in "$SRC"/deploy/systemd/watchtower-*; do
  n="$(basename "$u")"
  if [ "$DRY" = 1 ]; then
    printf '  DRY-RUN: install unit %s -> %s/%s (paths/user/port substituted)\n' "$n" "$WT_UNIT_DIR" "$n"
  else
    sed -e "s#/opt/watchtower#$WT_PREFIX#g" -e "s#/var/lib/watchtower#$WT_STATE#g" -e "s#/etc/watchtower#$WT_ETC#g" \
        -e "s#^User=watchtower#User=$WT_USER#" -e "s#^Group=watchtower#Group=$WT_USER#" -e "s#--port 8000#--port $WT_PORT#" "$u" \
      | install -m 0644 /dev/stdin "$WT_UNIT_DIR/$n"
  fi
done
run sysctl_ daemon-reload

if [ "$START" = 1 ]; then run sysctl_ enable --now watchtower-web.service; fi
if [ "$BTIMER" = 1 ]; then run sysctl_ enable --now watchtower-backup.timer; fi
log "NOT enabled, by design: watchtower-collect.timer (see docs/DEPLOYMENT.md 'Enabling scheduled collection later')"
if [ "$DRY" = 1 ]; then log "dry run complete: nothing was changed."; else
  log "install complete. Next: first backup + verify (docs/DEPLOYMENT.md, first-run checklist)."
fi
