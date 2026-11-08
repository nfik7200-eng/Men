# In-app notifications: design, migration and rollback

## What it does
- A watchlist item with a target price produces a **target_reached** alert when the latest collected price is at or below the target.
- A watchlist item with restock alerts on produces a **back_in_stock** alert for each real restock in the collection history, observed after the watch was created.
- Alerts appear on `/notifications` (HTML) and `GET /api/v1/notifications` (JSON), with unread counts and mark-read actions for one item or all of them.
- Nothing is sent by email, SMS or push. Those channels are out of scope.

## Safety rules
1. **The application never creates this table.** `Database.init()` (run at every app start) only knows the main models. The notifications table lives in its own declarative base, `NoteBase` in `watchtower/notifications.py`.
2. **Migrations are explicit and run on copies.** The CLI refuses the configured live database, by path.
3. **Until the table exists**, the notification routes return HTTP 503 with "not set up on this database". Nothing else changes.
4. **Idempotent generation.** Each alert's identity (owner + key) comes from the event: a target alert from the listing, the price and the target; a restock alert from the watch and its baseline. Re-running generation adds nothing new. A genuinely new price under the target adds one row.
   A restock alert is keyed on the observation that records the restock (an in-stock observation directly after an out-of-stock one, at or after the watch was created). Every real restock therefore alerts once, including repeated restocks on the same watch. No new table is needed: the append-only observation history already holds the transitions.
4a. **Currency-paused watches** raise no alert, following the monitor's rule: a currency change pauses comparisons until a new target is set.
5. **Owner isolation.** Another browser's alert id returns the same answer as a missing id (404 in the API, no effect in the form).
6. **Rollback refuses to discard history** unless `--discard-rows` is given.

## Migration on a copy (the only supported path)

```bash
# 1. Snapshot the live database (WAL-safe; read-only on the source)
python -m watchtower.cli backup --out /path/to/COPY.db

# 2. Dry run: prints the SQL, changes nothing
python -m watchtower.cli notify-migrate --db /path/to/COPY.db

# 3. Apply to the COPY only (the CLI refuses the live path)
python -m watchtower.cli notify-migrate --db /path/to/COPY.db --apply

# 4. Generate alerts for one owner (idempotent; run as often as you like)
python -m watchtower.cli notify-generate --db /path/to/COPY.db --owner OWNER_ID
```

Verification after step 3: `python -m watchtower.cli verify --db /path/to/COPY.db` must report `ok`, and the live file's SHA-256 must be unchanged.

## Applying to the live database (requires the owner's explicit approval)
The CLI will not do this. The approved route is: take a verified backup, apply the same `CREATE TABLE` to the live file with `sqlite3` inside a transaction after checking the dry-run output, then run `verify`. Do not run it without that approval.

## Rollback

```bash
# Dry run: shows how many rows would be lost
python -m watchtower.cli notify-rollback --db /path/to/COPY.db

# Refused while the table holds rows (exit with an error)
python -m watchtower.cli notify-rollback --db /path/to/COPY.db --apply

# Drops the table and its rows
python -m watchtower.cli notify-rollback --db /path/to/COPY.db --apply --discard-rows
```

The rest of the database is untouched by either direction. Rolling back never changes listings, observations or watch items.

## Verification in this repository
- `tests/test_notifications.py`: schema isolation, dry runs, idempotence, target and restock events, read state, owner isolation, rollback safety, 503 before migration, and the live-database refusal.
- Disposable-copy run: dry run, apply, generation twice (second run 0 new), rollback refusal, rollback with the flag. The live database's SHA-256 was identical before and after.
