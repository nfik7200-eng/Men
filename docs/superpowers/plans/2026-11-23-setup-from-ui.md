# Setup From the UI Implementation Plan

**Owner goal (2026-11-23):** "complete the entire backend and frontend; keys and APIs are left to me; I will set up the backend from the frontend UI." So every credential and operator switch that today needs a `BIP_*` environment variable must be settable from an admin page, with no secret ever returned to the browser.

## Design (ADR-056, Proposed)
- **`ConfigStore`** (`bip/config/store.py`): two files under `<data_root>/config/` (directory 0700, files 0600, atomic replace, symlinks refused): `secrets.json` (allowlisted secret names only) and `settings.json` (allowlisted setting fields only). Each value records `updated_at` and `updated_by`.
- **Precedence:** the process environment always wins. A store value for a field or secret the environment sets is ignored and shown as "overridden by environment".
- **Secrets live:** `LayeredEnviron` (environment first, then the store, never both for one name) is what the router, `read_secret` callers and the AI provider factory see. Provider endpoints are resolved per call, so provider keys apply immediately.
- **Settings at start:** `load_effective_settings()` merges the store's allowlisted overrides into `Settings` (minus environment-set fields). The setup API validates a change by building `Settings` with it before saving.
- **Restart from the UI:** `bip.api.__main__` runs the server in a loop; `POST /setup/restart` (admin) asks the running server to exit and the loop starts a fresh app with re-read settings. Unavailable (409) when the app is not run by `python -m bip.api`.
- **API:** `/setup` routes are admin-only for reads and writes (`access.ADMIN_ANY`). Secret values are write-only; reads return set/not set, source and timestamps. Every change is audited by name, never value.
- **UI:** a Setup page: provider credentials (with docs and terms links and a health check), Telegram, AI, background jobs (provider-contacting switches marked), data origin, and a restart banner.

## Tasks
1. ConfigStore + LayeredEnviron + effective settings loader (tests first).
2. Setup catalogue (allowlisted secrets and settings with labels) and the `/setup` API (tests first), access rule, app wiring, restart controller and server loop.
3. Frontend Setup page, client, types, nav, fake backend, Vitest; fix the stale "Authentication: Not implemented" on the Settings page.
4. ADR-056, `docs/OPERATIONS.md`, API reference regeneration, state docs, full checks, commit and push.
