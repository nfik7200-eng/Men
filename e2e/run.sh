#!/usr/bin/env bash
# Runs the Playwright journeys against a DISPOSABLE copy of a snapshot. Never touches data/watchtower.db.
#   e2e/run.sh [SNAPSHOT.db]     (default: data/backups/watchtower-db-snapshot.db, made with `cli backup`)
set -euo pipefail
cd "$(dirname "$0")/.."
SNAP="${1:-data/backups/watchtower-db-snapshot.db}"
[ -f "$SNAP" ] || { echo "snapshot not found: $SNAP (make one with: python -m watchtower.cli backup --out PATH)" >&2; exit 2; }
case "$(readlink -f "$SNAP")" in "$(readlink -f data/watchtower.db)") echo "refusing the live database" >&2; exit 2;; esac
TMP="$(mktemp -d /tmp/wt-e2e.XXXXXX)"; trap 'kill "${PID:-0}" 2>/dev/null || true; rm -rf "$TMP"' EXIT
cp "$SNAP" "$TMP/e2e.db"
export WATCHTOWER_DB_URL="sqlite:///$TMP/e2e.db" WATCHTOWER_ADMIN_TOKEN="${E2E_ADMIN_TOKEN:-e2e-admin-token-0123456789-abc}"
python -m uvicorn main:app --host 127.0.0.1 --port 8041 --no-proxy-headers >"$TMP/uvicorn.log" 2>&1 & PID=$!
for _ in $(seq 1 60); do curl -sf http://127.0.0.1:8041/health >/dev/null && break; sleep 0.5; done
mkdir -p "${E2E_OUT:-/tmp/e2e-shots}"
NODE_PATH="${NODE_PATH:-$(npm root -g)}" node e2e/journeys.js
