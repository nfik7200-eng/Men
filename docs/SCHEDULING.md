# Scheduling (disabled by default)

Nothing in this repository runs on a schedule. No cron job, systemd unit or daemon is installed, and none may be added
without the operator's explicit approval.

`python -m watchtower.cli schedule status|run-due [--config PATH]` is the only entry point. `run-due` does something only if
1. `data/schedule.json` has `"enabled": true` **and**
2. the host environment sets `WATCHTOWER_SCHEDULER_ENABLED=1`,

and then every source still passes `collector.run_source`: reviewed policy approved and current, robots.txt compliant, a real
`WATCHTOWER_CONTACT` (not a placeholder), not already running, and its per-run page budget. A refusal is printed as a skip
reason; it is never worked around. Only real-retailer (`live`) sources can be listed; the minimum interval is 12 h and the
budget ceiling is `config.MAX_PAGE_BUDGET` (500). Each invocation runs every due source once, never-run first, then the
longest-waiting, each with its own budget (no merchant takes another's capacity).

## To start accumulating history (needs the owner)
1. A real contact address in `WATCHTOWER_CONTACT` (never invented by the tooling).
2. A legal read of the Adafruit/SparkFun terms (policy status "approved" means *no prohibition found*, not permission).
3. Take a `python -m watchtower.cli backup` of the real database first: the first normal open after Mission 005 adds nullable
   columns to `crawl_urls` (`sync_columns`).
4. Set `"enabled": true` and the environment variable, then call `schedule run-due` from a scheduler you approve
   (e.g. once a day). Verified deals need 3+ collections over 7+ days, so plan for at least eight consecutive days.

## Clearing a blocked source
`run-due` skips a source whose latest run ended `blocked` (the site answered 401/403/451), and never retries it unattended.
After an operator has reviewed why, and only if collecting is still permitted, one successful manual
`python -m watchtower.cli collect --source K` becomes the latest run and the source is schedulable again.
