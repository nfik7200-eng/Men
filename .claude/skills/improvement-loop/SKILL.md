---
name: improvement-loop
description: Run one measured improvement iteration on the Watchtower shopping platform (snapshot -> baseline -> red test -> fix -> full suite -> verify -> independent review -> before/after measurement -> docs). Use when asked to improve data quality, matching, deals, the assistant, scraping reliability, or to "run the next loop iteration".
---

# One improvement iteration (see docs/LOOP_ENGINEERING.md)

1. **Snapshot, never touch the real DB.**
   `python -m watchtower.cli backup --out "$SCRATCH/snap.db"` (WAL-safe; a plain `cp` loses committed data).
   Record `sha256sum data/watchtower.db data/watchtower.db-wal` and re-check it at the end.
2. **Baseline.** `python -m pytest` (count), `python -m watchtower.cli verify --db "$SCRATCH/snap.db"`,
   `python scripts/loop_bench.py "$SCRATCH/snap.db"`.
3. **Pick ONE weakness with evidence** from the backlog in docs/LOOP_ENGINEERING.md §4 or from the baseline output.
   No evidence -> add to the backlog, do not code it.
4. **Red test first** (real titles/fixtures, both merchants where relevant). Confirm it fails for the expected reason;
   if the test setup is wrong, fix the test, never the assertion.
5. **Smallest fix**, then targeted tests, then the full suite. Never weaken or delete a test.
6. **Measure on a copy** of the snapshot (`cp snap.db exp.db`; `WATCHTOWER_DB_URL=sqlite:///exp.db ...`):
   `verify` and `loop_bench.py` before vs after. Check nothing else regressed.
7. **Independent review**: read-only reviewer subagent on the commit; fix high-confidence findings in the next
   iteration; record declined ones with the reason.
8. **Document**: iteration row in docs/LOOP_ENGINEERING.md §3 (evidence, change, before -> after, review), update
   docs/PROJECT_PROGRESS.md. Commit with a message that states the measured effect.

Hard limits: no live crawl outside the policy gate and approved pacing; no new source without a reviewed
`data/source_policies.json` entry; never fabricate observations or relax the verified-deal rule; never push, merge or
change PR state unless the user asked; no cron/system services without approval; no paid dependencies.
