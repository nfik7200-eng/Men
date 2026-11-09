# Production database cleanup runbook (requires owner approval — NOT run by any agent)

Known integrity findings on the real database (reproduced on a verified snapshot, sha256 `7f9419c3…`):
11 mirrored pending review pairs, and 6 SparkFun listings labelled `self` while sharing a product with Adafruit.

1. `python -m watchtower.cli backup --out /safe/place/pre-cleanup.db` (online-backup API; never `cp` the `.db`).
2. `python -m watchtower.cli verify --db /safe/place/pre-cleanup.db` (expect exactly the two failures above).
3. `python -m watchtower.cli cleanup-preview --db /safe/place/pre-cleanup.db` — runs `rematch` on a temporary copy and prints
   before/after. Measured on the snapshot: pending review candidates 23 → 12, 6 labels healed, 0 product groups changed,
   observations 901 → 901, "safe to apply: YES", all checks pass after.
4. Stop the web service and any collection. Apply: `python -m watchtower.cli rematch` (the first normal open also adds the nullable
   `crawl_urls` columns via `sync_columns`).
5. `python -m watchtower.cli backup` again, then `verify --db <that snapshot>`: expect `integrity: OK`.
6. Rollback: restore the step-1 snapshot to the live path with the service stopped (`deploy/restore.sh`, once the deployment package exists).
`rematch` never merges or splits products on weak evidence; it only prunes duplicate review questions and heals labels.

## Applied (2026-11-07, with the owner's approval)
Steps 1-5 were executed on the development container's database (no service running): pre-cleanup backup
`data/backups/pre-cleanup-20261107T144145Z.db` (verify: the two expected failures), `cleanup-preview` (safe: YES), `rematch`
(11 candidates dropped, 6 labels healed), post-cleanup backup `data/backups/post-cleanup-20261107T144210Z.db`
(`verify`: integrity OK; pending candidates 23 -> 12; observations 901 -> 901; prices, stock and product grouping identical).
The first normal open also added the nullable `crawl_urls`/`match_candidates` columns. This does NOT apply to any other
database (e.g. one on the VPS): repeat steps 1-5 there. Rollback for this one: restore the pre-cleanup backup with the service stopped.
