#!/usr/bin/env bash
# Render deploy/nginx/watchtower.conf for a real domain (refuses placeholders). Prints to stdout or --out FILE.
#   render-nginx.sh --domain app.yourorg.net [--out /etc/nginx/conf.d/watchtower.conf]
#   (WT_NGINX_TEMPLATE=watchtower-http-bootstrap.conf renders the HTTP-only certificate-bootstrap server instead)
set -euo pipefail
D=""; OUT=""
while [ $# -gt 0 ]; do case "$1" in --domain) D="${2:?}"; shift ;; --out) OUT="${2:?}"; shift ;; *) echo "unknown option $1" >&2; exit 2 ;; esac; shift; done
[[ "$D" =~ ^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$ ]] || { echo "refusing: '$D' is not a valid lowercase domain" >&2; exit 2; }
case "$D" in *your*|example.*|*.example.*|localhost*|*.invalid|*.test) echo "refusing placeholder/documentation domain '$D'" >&2; exit 2 ;; esac
src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/nginx/${WT_NGINX_TEMPLATE:-watchtower.conf}"
if [ -n "$OUT" ]; then [ ! -e "$OUT" ] || { echo "refusing to overwrite $OUT" >&2; exit 2; }; sed "s/YOUR_DOMAIN/$D/g" "$src" > "$OUT"; echo "wrote $OUT (run: nginx -t)"; else sed "s/YOUR_DOMAIN/$D/g" "$src"; fi
