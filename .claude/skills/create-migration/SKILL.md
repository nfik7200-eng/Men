---
name: create-migration
description: Add a BIP schema migration with a collision-checked number, forward-migration test and Proposed ADR.
disable-model-invocation: true
argument-hint: "<what the migration adds, e.g. 'token holder snapshots table'>"
---

# Create a BIP schema migration

Migration numbers are the one thing in this repo that cannot be fixed by a later commit: a number
recorded in someone's database keeps its meaning forever. The 2026-11 merge repair exists because two
branches both claimed v6 and v7 (see ADR-051). Every step below protects the number or the
upgrade path.

Request: $ARGUMENTS

## 1. Claim a free number

Run `python3 .claude/skills/create-migration/check_numbers.py`. It fetches, then reads `MIGRATIONS`
on the working tree and every local and remote branch.

Done when: it exits 0 (no collisions) and you have its "Next free migration number". Use that
number (call it N), not "working tree highest + 1": another branch may already hold the next one.
On exit 1, stop and report the collision to the user before writing anything.

## 2. Write the migration (TDD)

Write the test first and watch it go red, then implement:

- **Test**: in `tests/api/test_forward_migration.py`, freeze a database at N-1 (the `frozen_at`
  helper), seed a row in a table that already exists, run `migrate()`, and assert the new
  tables/columns exist, the seeded row survives, and a second `migrate()` is a no-op. Add the new
  tables to `NEW_TABLES` there if the file's existing assertions need it.
- **Schema**: define the tables in `bip/db/schema.py` and group them as `V{N}_TABLES`. Bump
  `SCHEMA_VERSION` to N.
- **Step**: add `_<name>(conn)` in `bip/db/engine.py` with a docstring starting `vN:` that says
  what it adds and that it is additive and idempotent. Append
  `Migration(N, "<description>", _<name>)` as the last entry of `MIGRATIONS`. The import-time
  assert checks it matches `SCHEMA_VERSION`.

Every migration is **additive and idempotent**, because SQLite auto-commits DDL and a crash can leave
a step half-applied with the version unrecorded:

- New tables: `table.create(conn, checkfirst=True)`.
- New columns: read `PRAGMA table_info(<table>)` and `ALTER TABLE ... ADD COLUMN` only when the
  column is missing (pattern: `_label_requester`, v15). A `NOT NULL` column needs a `DEFAULT`.
- Data backfills: insert only rows that are missing, and leave the source rows in place (pattern:
  `_checkpoints_and_coverage`, v16).
- Missing data stays `NULL`. Backfills never invent zeros (CLAUDE.md §2.8).

Earlier migrations are frozen. If one is wrong, fix it forward with a new migration, as v22 does.

Done when: the new test passes and the existing suites are green:
`uv run pytest tests/api/test_forward_migration.py tests/api/test_database.py -q`, then the full
`make check` (ruff, pyright strict, pytest).

## 3. Record it

- **ADR**: append `## ADR-<next number> <title> (Proposed; <stage>, <YYYY-MM-DD>)` to
  `DECISIONS.md` with Context / Decision / Consequences, and include the migration number and
  upgrade consequences. Status stays Proposed until the owner approves.
- **State docs**: update the schema version wherever `CURRENT_STATE.md` states it, and add an entry to
  the top of `IMPLEMENTATION_LOG.md` (newest first) with the real `make check` summary lines.
- **Commit**: in imperative mood, saying why the tables are needed, with no model attribution. Push only to
  `origin/claude/modest-fermi-0yklvj` per CLAUDE.md §8. The commit guard hook enforces both.

Done when: you have re-run `check_numbers.py` after committing, it still exits 0, and it reports N as
the working tree's highest migration.
