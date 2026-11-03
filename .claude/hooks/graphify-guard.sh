#!/bin/bash
# PreToolUse wrapper around `graphify hook-guard <search|read>`.
# Fails open: if the CLI is missing, errors out, or times out, it prints nothing
# and exits 0 so the tool call goes ahead. With no graph built, hook-guard
# itself prints nothing.
kind="${1:-}"

exe="$(command -v graphify 2>/dev/null)"
if [ -z "$exe" ] && [ -x "$HOME/.local/bin/graphify" ]; then
  exe="$HOME/.local/bin/graphify"
fi
[ -n "$exe" ] || exit 0

if command -v timeout >/dev/null 2>&1; then
  out="$(timeout 8 "$exe" hook-guard "$kind" 2>/dev/null)" || exit 0
else
  out="$("$exe" hook-guard "$kind" 2>/dev/null)" || exit 0
fi
[ -n "$out" ] && printf '%s' "$out"
exit 0
