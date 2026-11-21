#!/usr/bin/env python3
"""PreToolUse guard enforcing CLAUDE.md commit and file rules.

Blocks (exit 2, reason on stderr, which Claude sees):
- Bash `git commit` when the changes being committed contain conflict markers.
- Bash `git commit` whose message carries model attribution (CLAUDE.md section 4).
- Bash `git push` to any branch other than ALLOWED_BRANCH, or with force/delete/all/mirror
  (CLAUDE.md section 8).
- Edit/Write/NotebookEdit of `.env*` files (except `.env.example`) and `uv.lock`.

Reads the hook payload as JSON on stdin. Fails open on internal errors so a guard bug never
wedges a session; every failure path still prints a warning.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

ALLOWED_BRANCH = "claude/modest-fermi-0yklvj"

CONFLICT_RE = re.compile(r"^\+(<{7}|>{7}|\|{7})(\s|$)", re.MULTILINE)
UNTRACKED_CONFLICT_RE = re.compile(r"^(<{7}|>{7}|\|{7})(\s|$)", re.MULTILINE)
ATTRIBUTION_RE = re.compile(
    r"co-authored-by|generated with \[?claude|noreply@anthropic\.com"
    r"|claude-(opus|sonnet|haiku|fable)|\b(opus|sonnet|haiku|fable) \d",
    re.IGNORECASE,
)
PUSH_FORBIDDEN_FLAGS = {
    "-f",
    "--force",
    "--force-with-lease",
    "--force-if-includes",
    "-d",
    "--delete",
    "--all",
    "--mirror",
    "--prune",
}
SEGMENT_SPLIT_RE = re.compile(r"&&|\|\||;|\n|\|")


def block(reason: str) -> None:
    print(f"commit-guard: {reason}", file=sys.stderr)
    sys.exit(2)


def git(*args: str, cwd: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    ).stdout


def git_segments(command: str) -> list[list[str]]:
    """Return token lists for each `git ...` segment of a (possibly compound) command."""
    segments: list[list[str]] = []
    for raw in SEGMENT_SPLIT_RE.split(command):
        try:
            tokens = shlex.split(raw)
        except ValueError:
            tokens = raw.split()
        while tokens and ("=" in tokens[0] and not tokens[0].startswith("-")):
            tokens = tokens[1:]  # leading VAR=value assignments
        if tokens and tokens[0] == "git":
            # drop git's global options such as -C dir / -c k=v
            rest = tokens[1:]
            while rest and rest[0] in {"-C", "-c"} and len(rest) > 1:
                rest = rest[2:]
            segments.append(["git", *rest])
    return segments


def check_commit(command: str, tokens: list[str], cwd: str) -> None:
    # 1. Attribution: the message may be inline (-m, heredoc) or in a file (-F).
    message_text = command
    for i, tok in enumerate(tokens):
        if tok in {"-F", "--file"} and i + 1 < len(tokens):
            path = Path(cwd, tokens[i + 1])
            if path.is_file():
                message_text += "\n" + path.read_text(errors="replace")
    if ATTRIBUTION_RE.search(message_text):
        block(
            "commit message contains model attribution (Co-Authored-By / model name). "
            "CLAUDE.md section 4 forbids model identifiers in commits; remove it."
        )

    # 2. Conflict markers in what will be committed. A compound `git add ... && git commit`
    # or `commit -a` has not staged yet, so scan all tracked changes plus untracked files.
    stages_now = "git add" in command or any(t in {"-a", "--all"} for t in tokens[2:])
    diff = git("diff", "--cached", "-U0", cwd=cwd)
    if stages_now:
        diff += git("diff", "-U0", cwd=cwd)
    offenders = set()
    current = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
        elif current and CONFLICT_RE.match(line + "\n"):
            offenders.add(current)
    if stages_now:
        for rel in git("ls-files", "--others", "--exclude-standard", cwd=cwd).splitlines():
            p = Path(cwd, rel)
            try:
                if p.stat().st_size < 2_000_000 and UNTRACKED_CONFLICT_RE.search(
                    p.read_text(errors="ignore")
                ):
                    offenders.add(rel)
            except OSError:
                continue
    if offenders:
        block(
            "conflict markers found in changes being committed: "
            + ", ".join(sorted(offenders))
            + ". Resolve them first."
        )


def check_push(tokens: list[str], cwd: str) -> None:
    args = tokens[2:]
    bad = [a for a in args if a in PUSH_FORBIDDEN_FLAGS or a.startswith("--force")]
    if bad:
        block(f"push flag(s) {' '.join(bad)} not allowed (CLAUDE.md section 8: never force push).")
    positional = [a for a in args if not a.startswith("-")]
    refspecs = positional[1:]  # first positional is the remote
    current = git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd).strip()
    if not refspecs:
        if current != ALLOWED_BRANCH:
            block(f"current branch is '{current}'; pushes are only allowed to {ALLOWED_BRANCH}.")
        return
    for spec in refspecs:
        if spec.startswith("+"):
            block(f"forced refspec '{spec}' not allowed (CLAUDE.md section 8: never force push).")
        src, _, dst = spec.partition(":")
        target = dst or src
        target = target.removeprefix("refs/heads/")
        if target == "HEAD":
            target = current
        if target != ALLOWED_BRANCH:
            block(
                f"push target '{target}' not allowed; only {ALLOWED_BRANCH} (CLAUDE.md section 8)."
            )


def check_file(path_str: str) -> None:
    name = Path(path_str).name
    if name == "uv.lock":
        block(
            "uv.lock is generated; change pyproject.toml and run `uv lock` instead of editing it."
        )
    if (name == ".env" or name.startswith(".env.")) and name != ".env.example":
        block(f"{name} holds local secrets and must not be edited by Claude.")


def main() -> None:
    payload = json.load(sys.stdin)
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    cwd = payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()

    if tool in {"Edit", "Write", "MultiEdit", "NotebookEdit"}:
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        if path:
            check_file(path)
        return

    if tool == "Bash":
        command = tool_input.get("command", "")
        for tokens in git_segments(command):
            sub = tokens[1] if len(tokens) > 1 else ""
            if sub == "commit":
                check_commit(command, tokens, cwd)
            elif sub == "push":
                check_push(tokens, cwd)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # fail open, but say so
        print(f"commit-guard: internal error, not blocking: {exc!r}", file=sys.stderr)
        sys.exit(0)
