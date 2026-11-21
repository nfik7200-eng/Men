#!/usr/bin/env python3
"""Report BIP migration numbers across the working tree and every branch, flag collisions.

Usage: python3 .claude/skills/create-migration/check_numbers.py [--no-fetch]

Reads ``MIGRATIONS`` in bip/db/engine.py and ``SCHEMA_VERSION`` in bip/db/schema.py from the
working tree and from each local and remote-tracking branch (via ``git show``). A collision is
one version number with different descriptions on different refs: exactly what happened when
the local and upstream lines both claimed v6/v7. Prints the next free number. Exit 1 on collision.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

MIGRATION_RE = re.compile(r'Migration\(\s*(\d+),\s*(?:\n\s*)?"([^"]+)"')
VERSION_RE = re.compile(r"^SCHEMA_VERSION\s*=\s*(\d+)", re.MULTILINE)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=False).stdout


def parse(engine_src: str) -> dict[int, str]:
    return {int(n): d for n, d in MIGRATION_RE.findall(engine_src)}


def main() -> int:
    root = Path(git("rev-parse", "--show-toplevel").strip() or ".")
    if "--no-fetch" not in sys.argv:
        subprocess.run(["git", "-C", str(root), "fetch", "--quiet", "--all"], check=False)

    sources: dict[str, dict[int, str]] = {}
    work = root / "bip/db/engine.py"
    sources["(working tree)"] = parse(work.read_text())
    schema_src = (root / "bip/db/schema.py").read_text()
    m = VERSION_RE.search(schema_src)
    schema_version = int(m.group(1)) if m else None

    refs = git("for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes")
    for ref in refs.split():
        if ref.endswith("/HEAD"):
            continue
        src = git("show", f"{ref}:bip/db/engine.py")
        if src:
            sources[ref] = parse(src)

    by_version: dict[int, dict[str, list[str]]] = {}
    for ref, migrations in sources.items():
        for n, desc in migrations.items():
            by_version.setdefault(n, {}).setdefault(desc, []).append(ref)

    collisions = {n: d for n, d in by_version.items() if len(d) > 1}
    for ref, migrations in sources.items():
        top = max(migrations) if migrations else 0
        print(f"{ref:45} highest migration {top}")
    print(f"{'SCHEMA_VERSION (working tree)':45} {schema_version}")
    wt = sources["(working tree)"]
    if wt and schema_version != max(wt):
        print("WARNING: SCHEMA_VERSION does not equal the last migration in the working tree")

    if collisions:
        print("\nCOLLISIONS (same number, different meaning):")
        for n in sorted(collisions):
            for desc, where in collisions[n].items():
                print(f"  v{n}: {desc!r} on {', '.join(where)}")

    next_free = max(by_version) + 1 if by_version else 1
    print(f"\nNext free migration number across all refs: {next_free}")
    return 1 if collisions else 0


if __name__ == "__main__":
    sys.exit(main())
