# shellcheck shell=bash disable=SC2034
# Shared helpers for the Watchtower deploy scripts. Sourced, never executed.
# Every path is overridable through WT_* variables so the scripts can be exercised in a temp prefix.
#
# Safety model: state-changing scripts (install/update/rollback/restore) are DRY-RUN unless --apply is given.
# Nothing here touches anything outside WT_PREFIX, WT_STATE, WT_ETC and the watchtower-* unit files.

WT_PREFIX="${WT_PREFIX:-/opt/watchtower}"          # code: releases/, current, previous
WT_STATE="${WT_STATE:-/var/lib/watchtower}"        # DB + backups + schedule.json (survives releases)
WT_ETC="${WT_ETC:-/etc/watchtower}"
WT_ENV_FILE="${WT_ENV_FILE:-$WT_ETC/watchtower.env}"
WT_USER="${WT_USER:-watchtower}"
WT_UNIT_DIR="${WT_UNIT_DIR:-/etc/systemd/system}"
WT_PORT="${WT_PORT:-8000}"
WT_MIN_FREE_GB="${WT_MIN_FREE_GB:-5}"              # abort below this much free disk
WT_MIN_RAM_MB="${WT_MIN_RAM_MB:-2048}"             # abort below this much total RAM
WT_BACKUP_DIR="${WT_BACKUP_DIR:-$WT_STATE/backups}"
WT_MARKER="$WT_PREFIX/.watchtower-deploy"          # proves a prefix was created by these scripts
LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DRY=1
FORCE=0

log()  { printf '[%s] %s\n' "$(basename "$0")" "$*"; }
warn() { printf '[%s] WARN: %s\n' "$(basename "$0")" "$*" >&2; }
die()  { printf '[%s] ERROR: %s\n' "$(basename "$0")" "$1" >&2; exit "${2:-1}"; }

# run CMD...: print in dry-run, execute otherwise.
run() {
  if [ "$DRY" = 1 ]; then printf '  DRY-RUN: %s\n' "$*"; else "$@"; fi
}

need_root() {
  [ "$DRY" = 1 ] && return 0
  [ "${WT_ALLOW_NONROOT:-0}" = 1 ] && return 0
  [ "$(id -u)" = 0 ] || die "--apply must run as root (use sudo)"
}

# Run a command as the service user when we are root, directly otherwise.
as_wt() {
  if [ "$(id -u)" = 0 ] && [ "${WT_NO_SU:-0}" != 1 ] && [ "$(id -un)" != "$WT_USER" ]; then
    runuser -u "$WT_USER" -- "$@"
  else
    "$@"
  fi
}

sysctl_() { # systemctl wrapper that can be disabled for tests
  if [ "${WT_NO_SYSTEMCTL:-0}" = 1 ]; then log "(systemctl $* skipped: WT_NO_SYSTEMCTL=1)"; return 0; fi
  systemctl "$@"
}

service_active() {
  [ "${WT_NO_SYSTEMCTL:-0}" = 1 ] && return 1
  systemctl is-active --quiet watchtower-web.service
}

# any unit that may hold the live DB open (web process, a collection, a backup): restore must wait for all of them
active_db_unit() {
  [ "${WT_NO_SYSTEMCTL:-0}" = 1 ] && return 1
  local u
  for u in watchtower-web.service watchtower-collect.service watchtower-backup.service; do
    if systemctl is-active --quiet "$u"; then echo "$u"; return 0; fi
  done
  return 1
}

# ---------------------------------------------------------------- preflight primitives
nearest_existing() { local p="$1"; while [ ! -e "$p" ] && [ "$p" != "/" ]; do p="$(dirname "$p")"; done; echo "$p"; }

free_gb_of() { # integer GB free on the filesystem holding the path (floor)
  local p; p="$(nearest_existing "$1")"
  df -Pk "$p" | awk 'NR==2 {print int($4/1048576)}'
}

check_disk() { # check_disk PATH...
  local p f
  for p in "$@"; do
    f="$(free_gb_of "$p")"
    if [ "$f" -lt "$WT_MIN_FREE_GB" ]; then
      die "only ${f} GB free for $p (need >= ${WT_MIN_FREE_GB} GB; WT_MIN_FREE_GB overrides). Refusing." 3
    fi
    log "disk ok: ${f} GB free for $p (threshold ${WT_MIN_FREE_GB} GB)"
  done
}

check_ram() {
  local mb; mb="$(awk '/^MemTotal:/ {print int($2/1024)}' /proc/meminfo)"
  if [ "$mb" -lt "$WT_MIN_RAM_MB" ]; then
    die "only ${mb} MB RAM (need >= ${WT_MIN_RAM_MB} MB; WT_MIN_RAM_MB overrides). Refusing." 3
  fi
  log "RAM ok: ${mb} MB total (threshold ${WT_MIN_RAM_MB} MB)"
}

find_python() { # prints a python >= 3.11 or fails
  local c v
  for c in "${WT_PYTHON:-}" python3.13 python3.12 python3.11 python3; do
    [ -n "$c" ] || continue
    command -v "$c" >/dev/null 2>&1 || continue
    v="$("$c" -c 'import sys; print(1 if sys.version_info >= (3, 11) else 0)' 2>/dev/null || echo 0)"
    if [ "$v" = 1 ]; then command -v "$c"; return 0; fi
  done
  return 1
}

port_in_use() { # port_in_use PORT
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    [ -n "$(ss -H -ltn "sport = :$port" 2>/dev/null)" ]
  else
    (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null
  fi
}

is_ours() { [ -f "$WT_MARKER" ]; }

# ---------------------------------------------------------------- env file
# The env file is parsed, never sourced (no code execution). Only known prefixes are exported.
env_value() { # env_value KEY [FILE] -> last uncommented assignment, quotes stripped
  local f="${2:-$WT_ENV_FILE}"
  grep -E "^[[:space:]]*$1=" "$f" 2>/dev/null | tail -n1 | sed -E "s/^[[:space:]]*$1=//; s/^[\"']//; s/[\"'][[:space:]]*\$//"
}

load_env() {
  local f="${1:-$WT_ENV_FILE}" line k v
  [ -r "$f" ] || die "cannot read env file $f"
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|'#'*) continue ;; esac
    k="${line%%=*}"; v="${line#*=}"
    k="${k//[[:space:]]/}"
    [[ "$k" =~ ^(WATCHTOWER_|OLLAMA_|AFFILIATE_|IMPORT_RULES$|EBAY_)[A-Z0-9_]*$ ]] || continue
    v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"
    export "$k=$v"
  done < "$f"
}

# validate_env FILE: refuses placeholders and weak admin tokens. Returns 0 only if usable.
validate_env() {
  local f="${1:-$WT_ENV_FILE}" bad tok db
  [ -f "$f" ] || { warn "env file $f does not exist (create with: install.sh --init-env --apply)"; return 1; }
  bad="$(grep -nE '^[[:space:]]*[A-Za-z_]+=.*(YOUR_|CHANGE_ME)' "$f" || true)"
  if [ -n "$bad" ]; then
    warn "env file still contains placeholders (YOUR_* / CHANGE_ME) on uncommented lines:"
    printf '%s\n' "$bad" | sed -E 's/=.*/=<placeholder>/' >&2
    return 1
  fi
  tok="$(env_value WATCHTOWER_ADMIN_TOKEN "$f")"
  if [ -n "$tok" ] && [ "${#tok}" -lt 20 ]; then warn "WATCHTOWER_ADMIN_TOKEN is shorter than 20 chars (the app would keep admin disabled)"; return 1; fi
  [ -z "$tok" ] && warn "WATCHTOWER_ADMIN_TOKEN is unset: /admin stays disabled (fail-closed). That is safe, not an error."
  db="$(env_value WATCHTOWER_DB_URL "$f")"
  case "$db" in
    sqlite:////*) : ;;
    *) warn "WATCHTOWER_DB_URL must be an absolute sqlite URL (sqlite:////abs/path.db); got '${db:-<unset>}' -- the default would put the DB inside a release directory"; return 1 ;;
  esac
  [ "$(env_value WATCHTOWER_COOKIE_SECURE "$f")" = 1 ] || warn "WATCHTOWER_COOKIE_SECURE is not 1 (cookies would not be Secure behind the TLS proxy)"
  return 0
}

db_path() { # filesystem path of the configured SQLite DB (env must be loaded)
  local u="${WATCHTOWER_DB_URL:-}"
  case "$u" in sqlite:///*) printf '%s\n' "${u#sqlite:///}" ;; *) return 1 ;; esac
}

# ---------------------------------------------------------------- runtime python / app dir
app_dir() { echo "${WT_APP_DIR:-$WT_PREFIX/current}"; }
app_py()  { echo "${WT_PYTHON_BIN:-$(app_dir)/.venv/bin/python}"; }

# ---------------------------------------------------------------- release creation
# make_release SRC ID : copy code (never DBs, .git, .env, tests, caches) into releases/ID, build venv, smoke-import.
make_release() {
  local src="$1" id="$2" rel="$WT_PREFIX/releases/$2" py tmp oldumask item
  oldumask="$(umask)"; umask 022   # the service user must be able to read the release whatever the caller's umask is
  [ -f "$src/main.py" ] && [ -d "$src/watchtower" ] && [ -f "$src/requirements-prod.txt" ] \
    || die "$src is not a Watchtower source tree (needs main.py, watchtower/, requirements-prod.txt)"
  py="$(find_python)" || die "python >= 3.11 not found (set WT_PYTHON)"
  [ ! -e "$rel" ] || die "release dir $rel already exists"
  log "building release $id from $src (python: $py)"
  run mkdir -p "$rel"
  if [ "$DRY" = 1 ]; then
    printf '  DRY-RUN: copy ALLOWLIST only (main.py watchtower/ shopping/ deploy/ docs/ data/*.json requirements*.txt .env.example README.md CLAUDE.md pytest.ini) to %s\n' "$rel"
  else
    # Allowlist, not a denylist: databases, backups, .git, .env, venvs and tool caches can never be copied.
    local items=()
    for item in main.py watchtower shopping deploy docs data/*.json requirements*.txt .env.example README.md CLAUDE.md pytest.ini; do
      [ -e "$src/$item" ] && items+=("$item")   # optional files may be absent; required ones are checked below
    done
    ( cd "$src" && tar --exclude='__pycache__' --exclude='*.pyc' -cf - "${items[@]}" ) | tar -C "$rel" -xf - \
      || { umask "$oldumask"; die "release copy failed (tar error above); partial release left in $rel"; }
    [ -f "$rel/main.py" ] && [ -d "$rel/watchtower" ] || { umask "$oldumask"; die "release copy failed"; }
  fi
  run "$py" -m venv "$rel/.venv"
  # the full lock (what the suite was run against) when the release ships one, else the direct pins
  local reqs="$rel/requirements-prod.txt"
  [ "$DRY" = 1 ] || [ ! -f "$rel/requirements-lock.txt" ] || reqs="$rel/requirements-lock.txt"
  [ "$DRY" = 0 ] || [ ! -f "$src/requirements-lock.txt" ] || reqs="$rel/requirements-lock.txt"
  run "$rel/.venv/bin/pip" install --no-cache-dir --disable-pip-version-check -r "$reqs"
  if [ "$DRY" = 1 ]; then
    printf '  DRY-RUN: smoke test: import main against a throw-away temp DB\n'
  else
    tmp="$(mktemp -d)"
    ( cd "$rel" && PYTHONDONTWRITEBYTECODE=1 WATCHTOWER_DB_URL="sqlite:///$tmp/smoke.db" "$rel/.venv/bin/python" -c 'import main; print("smoke import ok")' ) \
      || { rm -rf "$tmp"; die "smoke import failed; release $id NOT activated (left in $rel for inspection)"; }
    rm -rf "$tmp"
  fi
  [ "$DRY" = 1 ] || chmod -R go+rX "$rel"
  umask "$oldumask"
}

new_release_id() { date -u +%Y%m%dT%H%M%SZ; }

swap_symlink() { # swap_symlink TARGET LINK  (atomic)
  run ln -sfn "$1" "$2.tmp"
  run mv -T "$2.tmp" "$2"
}

wait_healthy() { # wait_healthy [tries]
  local n="${1:-${WT_HEALTH_TRIES:-20}}" i
  for ((i = 1; i <= n; i++)); do
    if [ "$(curl -fsS --max-time 4 "http://127.0.0.1:$WT_PORT/health" 2>/dev/null || true)" = ok ]; then log "health: ok"; return 0; fi
    sleep 2
  done
  return 1
}

parse_common_flag() { # returns 0 if consumed
  case "$1" in
    --apply) DRY=0 ;;
    --dry-run) DRY=1 ;;
    --force) FORCE=1 ;;
    *) return 1 ;;
  esac
}
