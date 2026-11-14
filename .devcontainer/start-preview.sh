#!/usr/bin/env bash
# Start the SYNTHETIC BIP preview: the API on 127.0.0.1:8000 (sign-in required, synthetic users)
# and the Vite frontend on 127.0.0.1:5173, which proxies /api to it. Nothing contacts a provider.
# In a Codespace, open the forwarded port 5173 (keep it Private). Ctrl+C stops both.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
log="${TMPDIR:-/tmp}/bip-preview-backend.log"
uv run python -m tests.preview.serve_preview --auth --port 8000 >"$log" 2>&1 &
backend=$!
trap 'kill "$backend" 2>/dev/null || true' EXIT
for _ in $(seq 1 60); do
  if curl -fs -o /dev/null http://127.0.0.1:8000/api/v1/health; then break; fi
  if ! kill -0 "$backend" 2>/dev/null; then cat "$log"; exit 1; fi
  sleep 1
done
grep -m1 "password:" "$log" || true
echo "Sign in as viewer, analyst or admin with the password above (SYNTHETIC data only)."
cd frontend && npm run dev
