#!/bin/bash
# SessionStart hook: make the graphify CLI available in Claude Code cloud sessions.
# Installs graphifyy with `uv tool install` when it is missing and puts uv's tool
# bin directory on PATH. It never builds the knowledge graph, and it never fails
# the session: if the install fails, it reports that and the guards stay silent.
set -uo pipefail

# Only run in Claude Code cloud sessions; local machines manage their own tools.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

bin_dir="$HOME/.local/bin"
if command -v uv >/dev/null 2>&1; then
  bin_dir="$(uv tool dir --bin 2>/dev/null || echo "$bin_dir")"
fi
export PATH="$bin_dir:$PATH"

if ! command -v graphify >/dev/null 2>&1; then
  if ! command -v uv >/dev/null 2>&1; then
    echo "graphify: uv not found; graphify CLI not installed (graph hooks stay inactive)."
  elif ! uv tool install graphifyy >&2; then
    echo "graphify: 'uv tool install graphifyy' failed; graph hooks stay inactive this session."
  fi
fi

# Persist PATH for the rest of the session (Bash tool commands).
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  line="export PATH=\"$bin_dir:\$PATH\""
  grep -qxF "$line" "$CLAUDE_ENV_FILE" 2>/dev/null || echo "$line" >> "$CLAUDE_ENV_FILE"
fi

exit 0
