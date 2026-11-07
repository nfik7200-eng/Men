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

## Milestones
| # | Milestone | Status | Evidence / next |
|---|---|---|---|
| 0 | Preservation & intelligence | DONE | 26 commits verified, bundle+snapshot delivered, baseline reproduced |
| 1 | Architecture & foundations (deps, contracts) | IN PROGRESS | requirements split (core/ml/dev), architecture doc, config contract |
| 2 | Real acquisition | PARTIAL / external | engine + planner done (M005); live run needs real `WATCHTOWER_CONTACT` + owner approval; more sources need review |
| 3 | Product intelligence | PARTIAL | matcher + review queue exist; needs evaluation set + explanations audit |
| 4 | ML (scikit-learn) | NOT STARTED | env spec, eval harness, honest data-sufficiency verdict |
| 5 | Price intelligence | PARTIAL | stats exist; needs history (≥ 8 days, external) and anomaly detector |
| 6 | AI research assistant | PARTIAL | deterministic assistant done; broaden intents |
| 7 | Frontend | PARTIAL | coverage/product/research pages exist; review-queue pair view, settings, price history UI gaps |
| 8 | Security & reliability | PARTIAL | M002 fixes; new full review pending |
| 9 | Integration & final tests | NOT STARTED | |
| 10 | Contabo deployment package | NOT STARTED | package is written + statically verified locally; **not deployable-verified without VPS access (external)** |

## Open blockers needing the owner
Real contact address · approval for any live collection · legal read of Adafruit/SparkFun terms · VPS access/specs ·
approval of a real-DB backup + rematch · decisions on the 12 review candidates · push approval.
