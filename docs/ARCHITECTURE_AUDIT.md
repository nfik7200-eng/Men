# Architecture audit: missing features, unfinished integrations, duplication, technical debt

Measured on the repository at this round's HEAD (module sizes via `wc -l`, duplication via search). This document
records what was found; items marked **fixed** were changed in this round.

## Structure and size
- `shopping/web/app.py`: 671 lines. Almost every route lives inside one `create_app()` closure (about 70 functions).
  **Debt:** hard to test in isolation, and the closure shares state through the enclosing scope. **Recommended:** split into
  route modules (search, listings API, watchlist and alerts, admin, research), each registered by a small function.
  Not done here: it is a refactor with broad test impact, and the brief asked to preserve architecture unless evidence justifies a change.
- `watchtower/matching.py` (548 lines), `shopping/assistant.py` (564), `watchtower/collector.py` (375), `watchtower/cli.py` (371): large but single-purpose.
- Engine (`watchtower/`) never imports the shopping app (checked by the existing test that greps for it).

## Duplication
- **Fixed:** the owner-cookie format check lived in two places; it is now `security.valid_owner`.
- **Fixed:** the data-class mapping (`real` / `demo` / `all` to source kinds) was written twice in `app.py`; it is now `kinds_of`.
- **Kept on purpose:** the currency exponent tables exist in Python (`watchtower/currency.py`) and TypeScript
  (`frontend/src/lib/format.ts`). The frontend cannot import Python, so the two lists are mirrored and pinned by tests on each side.
- **Kept on purpose:** money formatting exists in three places (`money.fmt_money`, the Jinja filter, the frontend `formatMoney`),
  each for its own surface, all using the same exponent rule.

## Unfinished integrations (honest status)
| Item | State | Notes |
|---|---|---|
| eBay | scaffolding; mock-tested | live only in `EBAY_ENV=production`, which is gated by a missing policy |
| Ollama / local LLM | optional layer, off by default | never tested with a real model; the rules-based assistant is the default |
| Anomaly detector | implemented, advisory | not used by deals or the assistant (by design until data suffices) |
| Forecasting | not built | the sufficiency check says it is not justified yet |
| Import-cost duties and taxes | placeholder assumptions | labelled as unverified in every output |
| Affiliate links | built, never influence ranking | a test greps the ranking code for it |
| Alert delivery (email, SMS, push) | out of scope | in-app alerts only |
| Watchlist, alerts, admin status UI | server pages built; islands not built | the JSON endpoints exist |
| Deal radar, browse | built, islands and server fallback | |

## Missing features relative to the brief (not built)
- Stock-history analytics beyond restock alerts (only the transitions in the observation history).
- Multi-user accounts (identity is a browser cookie by design).
- Live collection (disabled; no contact address; policy review pending).
- A production deployment (rehearsed in a temporary prefix only).

## Technical debt, ranked
1. The `create_app` closure (above). Medium impact, high effort.
2. Pyright reports 78 pre-existing errors (Optional, bs4 and SQLAlchemy typing). No crash-level bug found; new code is clean.
3. Frontend `main.js` is a committed build artefact; the freshness guard enforces that it matches the sources. Any frontend
   change needs a rebuild and a commit of the bundle.
4. Two data stores answer the same question about currency (Python and TypeScript tables). Pinned by tests, not generated from one source.
5. The notifications table lives outside the main metadata by design. Its migration is a manual, approved step (see `docs/NOTIFICATIONS.md`).
