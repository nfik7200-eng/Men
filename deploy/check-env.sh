#!/usr/bin/env bash
# ExecStartPre guard for watchtower-web.service: refuse to start with placeholder/weak settings.
set -euo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
validate_env "$WT_ENV_FILE" || die "env file rejected; fix it and restart" 5
