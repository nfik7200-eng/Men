# BIP Branch Repair Plan (rebase damage, 2026-11-19/20)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development.

**Goal:** Restore `claude/modest-fermi-0yklvj` to a correct, green state. A `pull --rebase` onto
origin's Stage 3/4 commit `9765710` hit conflicts. The rebase committed conflict markers in six
frontend files (`b0c3f32`), overwrote `CURRENT_STATE.md` / `IMPLEMENTATION_LOG.md` wholesale (`434ded3`),
and a follow-up commit (`b1d7fc3`) deleted backend investigation/curation code. All of this is
already pushed, so the repair is a **forward commit** — no history rewrite.

**Spec (the authority):** the correct tree is the three-way merge of
- `UPSTREAM` = `9765710` (origin: Stage 3/4 human supervision, ranking ML, discovery, alerts, memory)
- `LOCAL` = `5bdddbf` (pre-rebase local work: Detective platform, strategies, curation, graph explorer, acquisition fetcher)
- `BASE` = `61a3e49` (their merge base)

`git merge-tree --write-tree 9765710 5bdddbf` (tree `9b590a1`) reproduces exactly 12 conflicted
files. Every other file at HEAD already equals the clean merge, except `tests/stage5/test_strategy_api.py`
(HEAD switched it from Solana to Ethereum fixtures — keep whichever version passes; prefer HEAD's).

**Resolution rule:** union both sides. Both sides added independent features; neither side's
additions may be dropped. Where both sides edit the same construct (e.g. a router list, the
migrations list, a nav list, the `Case` type), merge them so both features work. When both sides
define the same name differently, keep both behaviours and rename only if unavoidable; record any
such choice in the task report.

Reconstruct a conflicted file with:
`git merge-file -p <(git show 9765710:F) <(git show 61a3e49:F) <(git show 5bdddbf:F)` — or
`git show 9b590a1:F` (identical result). Write the resolved content into the working tree at the
same path on the current branch.

## Global Constraints
- Python 3.12+, `ruff`, `pyright` (strict for `bip/core`, `bip/router`), `pytest` — all must be green.
- Frontend: `npx tsc --noEmit`, `npx vitest run`, `npm run build` — all must be green.
- No new dependencies. No network except none. No live providers.
- Migrations are additive and ordered; a migration number may not be reused for two different schemas.
  If both sides added a migration with the same version number, renumber LOCAL's to the next free
  number and keep both.
- Never discard uncommitted work without a backup (a backup patch exists at
  `.superpowers/sdd/2026-11-21-bip-merge-repair/uncommitted-before-repair.patch`).
- Commit messages: imperative, explain why, end with the attribution line given in the dispatch. No model identifiers in code/docs.
- Do not push; the controller pushes.

---

### Task 1: Backend merge resolution
**Files:** `bip/api/app.py`, `bip/api/routes/investigations.py`, `bip/db/engine.py`, `bip/db/schema.py`
(and `tests/stage5/test_strategy_api.py` only if needed).
- Resolve the 8 conflict hunks by union as above, writing results over the current HEAD files.
- Make sure every table, migration, route and router registration from both UPSTREAM and LOCAL
  exists exactly once (e.g. `schema.curation_feedback` is currently missing — `tests/api/test_curation_api.py` fails).
- Fix any ruff errors introduced by the merge in touched files and any pre-existing ruff errors across the repo (`ruff check .` must report 0).
- Verify: `uv run --offline pytest -q`, `uv run --offline ruff check .`, `uv run --offline ruff format --check .` (report, fix only touched files), `uv run --offline pyright`. Paste the summary lines in the report.
- Commit: one commit, "Restore backend lost in the rebase merge" style.

### Task 2: Frontend merge resolution
**Files:** `frontend/src/App.tsx`, `frontend/src/api/client.ts`, `frontend/src/api/types.ts`,
`frontend/src/pages/Investigations.tsx`, `frontend/src/styles.css`, `frontend/src/test/fakeBackend.ts`.
- The current working-tree edits of these files are a failed resolution (925 tsc errors; it
  dropped UPSTREAM types like `Alert`, `Detector`, `Fact`, `Signal`, `SystemHealth`). Discard them in
  favour of a fresh union resolution (backup patch already saved).
- Resolve the 10 conflict hunks by union: all routes/nav entries/pages from both sides, all types from
  both sides (where both sides define e.g. `Case`/`CaseEvidence` differently, produce one type that
  satisfies every consumer), all client methods from both sides, both fake-backend handlers.
- Verify from `frontend/`: `npx tsc --noEmit -p .` (0 errors), `npx vitest run` (all pass), `npm run build`.
- Commit: one commit.

### Task 3: Restore state documents and record the repair
**Files:** `CURRENT_STATE.md`, `IMPLEMENTATION_LOG.md`, `docs/IMPLEMENTATION_ROADMAP.md`.
- `IMPLEMENTATION_LOG.md`: the rebase replaced ~1190 lines of history. Rebuild it as the union of
  UPSTREAM's log and LOCAL's log (chronological; no entry lost), then append a 2026-11-21 entry for
  this repair with the real command output from Tasks 1–2 (provided in the dispatch).
- `CURRENT_STATE.md`: merge both sides into one honest current-state document covering Stage 3/4
  (UPSTREAM) and the Detective/strategies/curation work (LOCAL), correct test counts (provided), and
  note the repair. Remove any claim not backed by evidence.
- `docs/IMPLEMENTATION_ROADMAP.md`: update the five-stage programme table so Stage 4 reflects the
  human-supervision/ranking work actually present and Stage 5 reflects what exists (strategies,
  curation, investigation workbench) vs what does not (Ollama, production ops). Keep labels honest.
- Commit: one commit.
