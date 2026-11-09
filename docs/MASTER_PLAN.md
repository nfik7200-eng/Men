# Watchtower master plan and completion tracker (programme started 2026-11-07)

Single authoritative tracker. Updated by the orchestrator after every integrated milestone; future sessions resume here.
Evidence rule: an item is DONE only with a test/measurement recorded in the "Evidence" column; "external" means it needs
the owner (permission, contact address, VPS access, human review) and is NOT counted as engineering-complete.

## Operating model
- **Orchestrator:** the main session (Sonnet) with the project skill `improvement-loop`. No external Loop-Engineering plugin is
  installed (checked: plugins present are claude-memory, claude-security, graph-query-mcp, playwright, review-council,
  security-guidance, serena, superpowers). `/review-council` is user-invoked only. One orchestrator only; no competing loops.
- **Agents** (real subagent types, spawned per task, read-only unless the task owns a path): backend/data/matching/ML/db/price/assistant/
  frontend/security/QA/independent-review roles map onto `general-purpose`, `feature-dev:code-explorer|code-reviewer|code-architect`,
  `claude-security:*`, `code-simplifier` and read-only reviewers. Parallel agents own DISJOINT paths; shared modules
  (`models.py`, `matching.py`, `collector.py`, `app.py`) and schema changes are integrated by the orchestrator only.
- **Cycle:** DISCOVER → PRIORITISE → PLAN → IMPLEMENT → TEST → REVIEW → MEASURE → DOCUMENT → COMMIT.
- **Budget per unattended run:** ≤ 8 iterations, ≤ 6 concurrent agents, no process > 10 min, stop on: 2 consecutive failed iterations on
  the same item, any rule in CLAUDE.md that would be violated, an external approval needed, or disk < 5 GB.
- **Never without approval:** push, merge/alter PR #3, touch the real DB, live/recurring collection, contact merchants, invent a
  contact, spend money, deploy over a VPS, install services/cron.

## Baseline (reproduced in this checkout, 2026-11-07)
370 tests pass · coverage 92 % · Pyright 75 errors · 26 commits ahead of origin (8 M004 + 18 M005) · real DB sha256 unchanged
(`b5c68461…` + WAL `7c1b5baa…`) · durable copy: git bundle `9ef4c00f…` and DB snapshot `7f9419c3…` delivered to the owner.
Dev container: 4 vCPU, 16 GB RAM, 30 GB free, Python 3.13, no nginx. **This is not the Contabo VPS**; VPS resources are unverified.

## Milestones (final status of this programme run; evidence in git log and docs/PROJECT_PROGRESS.md)
| # | Milestone | Status | Evidence / what remains |
|---|---|---|---|
| 0 | Preservation & intelligence | DONE | 26 commits verified at start; bundle + DB snapshot delivered; baseline reproduced (370 / 92 % / 75) |
| 1 | Architecture & foundations | DONE | docs/ARCHITECTURE.md module map; core/ml/prod requirement pins; contracts (name hints priority-only, ML advisory-only, append-only observations) |
| 2 | Real acquisition | ENGINEERING DONE; live run NOT MET (external) | planner, coverage, scheduler, budgets, policy gate; no free source exists for laptops/phones (docs/SOURCE_DISCOVERY_ADDENDUM.md); live run needs a real contact + approval |
| 3 | Product intelligence | DONE; 12 decisions need a human | audited decisions (who/when/evidence), side-by-side review, label healing, mirrors collapsed |
| 4 | ML (scikit-learn) | HARNESS DONE; models NOT JUSTIFIED | isolated `.venv-ml` (sklearn 1.9.1); independent review fixes applied (H1/H2/M1-M6/L1-L7); verdict: insufficient data |
| 5 | Price intelligence | ENGINEERING DONE; verified deals NOT MET (external: >= 8 days of collection) | stats, peers, claimed-original labelling, market questions |
| 6 | AI research assistant | DONE (deterministic, LLM optional) | evidence-based answers; uncollected/unverified disclosure; new / price-drop / history-gap intents |
| 7 | Frontend | DONE | provenance, admin budgets/schedule, review UI, a11y, coverage page; e2e/run.sh desktop + Pixel 7 |
| 8 | Security & reliability | DONE for reviewed findings; open items listed | independent review: 0 critical/high; all medium/low fixed except noted in PROJECT_PROGRESS |
| 9 | Integration & final tests | DONE | full suites green in system python, ML venv and pinned prod venv; Ruff clean; Pyright 75 pre-existing / 0 new; e2e pass |
| 10 | Contabo deployment package | WRITTEN + TESTED IN A TEMP PREFIX; deployment NOT VERIFIED (external) | independent review fixes applied; shellcheck clean; nginx and systemd runtime UNTESTED; VPS resources unverified |

## Open blockers needing the owner
Real contact address · approval for any live collection · legal read of Adafruit/SparkFun terms · VPS access/specs ·
approval of a real-DB backup + rematch · decisions on the 12 review candidates · push approval.
