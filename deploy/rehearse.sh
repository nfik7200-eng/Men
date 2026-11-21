#!/usr/bin/env bash
# Rehearse the deployment steps locally, with the real entry points and the shipped env template.
#
#   deploy/rehearse.sh            # from the repository root, after `uv sync`
#
# What it exercises: the production env template, the pre-flight check, creating the first
# administrator, `python -m bip.api` listening on a Unix socket (mode 0660), sign-in with the
# CSRF token, a protected write, a verified backup. What it cannot: systemd, a reverse proxy, TLS,
# a real host, real providers (nothing here contacts one). Everything lives in a temporary
# directory and is removed at the end. The password below is SYNTHETIC and used only here.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
T="$(mktemp -d /tmp/bip-rehearsal.XXXXXX)"
SERVER_PID=""
cleanup() { [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null || true; rm -rf "$T"; }
trap cleanup EXIT
STEPS=0
ok() { STEPS=$((STEPS + 1)); printf 'PASS  %s\n' "$1"; }
die() { printf 'FAIL  %s\n' "$1" >&2; exit 1; }

mkdir -p "$T/data" "$T/run"
chmod 750 "$T/run"
SOCK="$T/run/api.sock"
PASSWORD="synthetic-rehearsal-$(head -c 6 /dev/urandom | od -An -tx1 | tr -d ' \n')"

# 1. configuration: the template, with this machine's paths and a small disk floor
sed -e "s|^BIP_DATA_ROOT=.*|BIP_DATA_ROOT=$T/data|" \
    -e "s|^BIP_API__UNIX_SOCKET=.*|BIP_API__UNIX_SOCKET=$SOCK|" \
    "$ROOT/deploy/bip.env.production.example" > "$T/bip.env"
printf 'BIP_MIN_FREE_BYTES=1MiB\nBIP_MAX_DATA_BYTES=1GiB\n' >> "$T/bip.env"
chmod 640 "$T/bip.env"
ok "env file written from the production template (mode 640)"

# 2. pre-flight: must have no FAIL
"$PY" -m bip.tools.preflight --env-file "$T/bip.env" > "$T/preflight.txt" || { cat "$T/preflight.txt"; die "pre-flight failed"; }
grep -q "provider_contact: no scheduled job contacts a provider" "$T/preflight.txt" || die "collection is not off"
ok "pre-flight passes and scheduled provider use is off"

# 3. the first administrator, from a root-only password file (never an argument)
printf '%s' "$PASSWORD" > "$T/pw" && chmod 600 "$T/pw"
( set -a; . "$T/bip.env"; set +a
  BIP_NEW_PASSWORD_FILE="$T/pw" "$PY" -m bip.tools.users create admin --role admin >/dev/null )
ok "administrator created"

# 4. the API on the socket, exactly as the unit starts it
( set -a; . "$T/bip.env"; set +a; exec "$PY" -m bip.api ) > "$T/server.log" 2>&1 &
SERVER_PID=$!
for _ in $(seq 1 100); do [ -S "$SOCK" ] && break; sleep 0.1; done
[ -S "$SOCK" ] || { cat "$T/server.log"; die "the socket did not appear"; }
[ "$(stat -c %a "$SOCK")" = "660" ] || die "socket mode is $(stat -c %a "$SOCK"), expected 660"
ok "listening on a Unix socket with mode 660"
C() { curl -sS --unix-socket "$SOCK" "$@"; }

[ "$(C -o /dev/null -w '%{http_code}' http://bip/api/v1/health)" = "200" ] || die "health"
ok "public health answers"
[ "$(C -o /dev/null -w '%{http_code}' http://bip/api/v1/system/health)" = "401" ] || die "protected route open"
ok "a protected route refuses an anonymous caller (401)"

# 5. sign in, then use the CSRF token the API returns
C -D "$T/h" -o "$T/login.json" -H 'Content-Type: application/json' \
  -d "{\"username\":\"admin\",\"password\":\"$PASSWORD\"}" http://bip/api/v1/auth/login
COOKIE="$(grep -i '^set-cookie: bip_session=' "$T/h" | sed 's/^[^:]*: //; s/;.*//')"
CSRF="$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["csrf_token"])' "$T/login.json")"
[ -n "$COOKIE" ] && [ -n "$CSRF" ] || die "sign-in did not return a session and a CSRF token"
grep -qi 'set-cookie:.*httponly' "$T/h" && grep -qi 'set-cookie:.*samesite=strict' "$T/h" && grep -qi 'set-cookie:.*secure' "$T/h" \
  || die "session cookie is missing HttpOnly, SameSite=Strict or Secure"
ok "sign-in works; the cookie is HttpOnly, SameSite=Strict and Secure"
[ "$(C -o /dev/null -w '%{http_code}' -b "$COOKIE" http://bip/api/v1/system/health)" = "200" ] || die "health with a session"
ok "a signed-in administrator reads the system report (200)"

# 6. writes need the CSRF token
[ "$(C -o /dev/null -w '%{http_code}' -b "$COOKIE" -X POST -H 'Content-Type: application/json' -d '{}' http://bip/api/v1/discovery/run)" = "403" ] || die "write without CSRF accepted"
ok "a write without the CSRF token is refused (403)"
[ "$(C -o /dev/null -w '%{http_code}' -b "$COOKIE" -H "X-CSRF-Token: $CSRF" -X POST -H 'Content-Type: application/json' -d '{}' http://bip/api/v1/discovery/run)" = "201" ] || die "write with CSRF refused"
ok "a database-only discovery pass runs with the token (201)"

# 7. a verified backup
C -b "$COOKIE" -H "X-CSRF-Token: $CSRF" -X POST -o "$T/backup.json" http://bip/api/v1/system/backups
NAME="$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["name"])' "$T/backup.json")"
[ "$(C -o /dev/null -w '%{http_code}' -b "$COOKIE" -H "X-CSRF-Token: $CSRF" -X POST "http://bip/api/v1/system/backups/$NAME/verify")" = "200" ] || die "backup verification"
ok "a backup is created and verifies (integrity check)"
printf '\nRehearsal complete: %s steps passed. Nothing contacted a provider.\n' "$STEPS"
