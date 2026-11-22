# Telegram Alert Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver BIP alerts to a Telegram chat through a retrying outbox, off by default, without putting a network call inside the discovery transaction. Also correct the stale roadmap progress table.

**Architecture:** Alert evaluation (`DiscoveryService._evaluate_alerts`) keeps writing the in-app alert row. When an external channel is enabled, it also writes a `pending` row to a new `alert_deliveries` table in the same transaction, with no network I/O. A new `AlertDispatcher`, run by a scheduler job, claims due rows and sends them through `TelegramChannel`. `TelegramChannel` uses the existing hardened `HttpsTransport` (pinned IPs, no redirects, size and time limits). Delivery is at-least-once.

**Tech Stack:** Python 3.12, SQLAlchemy Core and SQLite (migration v23), the stdlib `HttpsTransport`, pydantic-settings, pytest with a fake transport.

**Spec:** Owner decisions, 2026-11-22, recorded in this session. Alert channel: Telegram bot. Stage 12 alert channel from `docs/IMPLEMENTATION_ROADMAP.md`. Constraints: `CLAUDE.md` §2, §4 and §5.

## Global Constraints

- No new dependency. Use `bip/rpc/transport.py` `HttpsTransport` / `Transport`.
- Off by default: `BIP_ALERTS__TELEGRAM_ENABLED=false`. The delivery job is registered only when it is enabled and `BIP_WORKERS__ENABLED` is on.
- The bot token comes only from `read_secret("BIP_TELEGRAM_BOT_TOKEN")` (or `BIP_TELEGRAM_BOT_TOKEN_FILE`). It is never logged, stored, or returned by the API.
- `BIP_TELEGRAM_BOT_TOKEN` must be added to `_TOOL_NAMES` in `bip/config/settings.py` (ADR-053 allowlist).
- Messages are plain text (no `parse_mode`), at most 4096 characters, and start with `BIP alert — statistical signal (not a verified observation)` (`CLAUDE.md` §2.12).
- Provider facts used in code (message length 4096; about 1 message per second per chat) go into `docs/PROVIDER_REGISTRY.md` under a "Telegram Bot API (alert channel)" section, with `source_url` and `verified_at`. Sources: https://core.telegram.org/bots/api#sendmessage and https://core.telegram.org/bots/faq#my-bot-is-hitting-limits-how-do-i-avoid-this. Fetch both pages when executing. If a value differs, use the page's value and record the date. If the pages cannot be fetched, record `verified_at: null` and keep the values below.
- Tests never touch the network (`CLAUDE.md` §4).
- Commit messages: imperative, explain why, no model identifiers and no attribution lines (the commit hook rejects them).

## Review Focus

1. **Telegram unreachable during a discovery pass.** The alert must still be created, and the pass must not wait on the network. Test: `test_evaluation_never_calls_the_transport` (Task 2).
2. **The bot token leaking** into `last_error`, an API response or an exception message. This includes Telegram error bodies that echo it. Test: `test_last_error_never_contains_the_token` (Task 4).
3. **A malformed token or chat id** injecting into the URL path (`.../bot<token>/sendMessage`). It must be refused before any request is sent. Tests: `test_token_with_slash_is_refused`, `test_chat_id_pattern` (Tasks 3 and 4).
4. **Repeated dispatch runs.** A `delivered` row is never resent, and a crash after a send but before the status update is documented as a possible duplicate (at-least-once). Test: `test_second_dispatch_sends_nothing` (Task 4).
5. **HTTP 200 with `"ok": false`, or 429 with `parameters.retry_after`.** Neither may count as delivered, and `retry_after` must set the next attempt time (`CLAUDE.md` §2.7). Tests: `test_ok_false_is_not_delivered`, `test_429_honours_retry_after` (Task 3).

---

### Task 1: Correct the roadmap progress table

**Files:**
- Modify: `docs/IMPLEMENTATION_ROADMAP.md` (the `## Progress` table, rows "UI integration" and "5 onward (others)")

- [ ] **Step 1: Rewrite the stale rows from evidence**

"UI integration": list what exists in `frontend/src/pages/` (23 pages, including Alerts, GraphExplorer, Research, Curation, Coverage and Strategies, each with Vitest tests). Then compare against the Milestone B–E lists in `docs/FRONTEND_ARCHITECTURE.md` (§6.5 and the Milestone C traceability fix). Mark each milestone Done, Partial (naming what is missing) or Not started. Do not mark a milestone Done unless every item in its list maps to a page or test that exists.
Add a row: "Owner decisions 2026-11-22: Telegram alert channel; Postgres/Redis as code plus compose only (nothing run); pyarrow and duckdb approved; BigQuery sandbox to be created by the owner". It links this plan.

- [ ] **Step 2: Commit**

```bash
git add docs/IMPLEMENTATION_ROADMAP.md
git commit -m "Correct the roadmap progress table, which still listed UI milestones B to E as not started although most pages exist"
```

### Task 2: Delivery outbox table and pending rows at evaluation

**Files:**
- Modify: `bip/db/schema.py` (new `alert_deliveries` table, `V23_TABLES`, `SCHEMA_VERSION = 23`)
- Modify: `bip/db/engine.py` (`_alert_deliveries` migration, `Migration(23, "alert delivery outbox", _alert_deliveries)`)
- Modify: `bip/discovery/service.py` (`DiscoveryService.__init__` gains `external_channels: Sequence[str] = ()`; `_evaluate_alerts` inserts pending rows)
- Test: `tests/discovery/test_alert_outbox.py`

**Interfaces:**
- Produces the table `alert_deliveries`, with these columns:
  - `id` Integer PK;
  - `alert_id` FK to `alerts.id`, `ondelete="CASCADE"`, not null;
  - `channel` String(24), not null;
  - `status` String(12), not null, CHECK in (`'pending'`, `'delivered'`, `'failed'`);
  - `attempts` Integer, not null, default 0;
  - `next_attempt_at` UTCDateTime, not null;
  - `last_error` String(200), nullable;
  - `delivered_at` UTCDateTime, nullable;
  - `UniqueConstraint("alert_id", "channel")` and `Index(None, "status", "next_attempt_at")`.
- Produces `DiscoveryService(repo, settings, *, clock=..., channels=..., external_channels: Sequence[str] = ())`.

- [ ] **Step 1: Write the failing tests**

Use the seed and fixture helpers in `tests/discovery/seed.py` and `tests/discovery/test_pipeline.py`. Create one enabled rule and one matching signal, then run.

```python
def test_pending_row_per_external_channel(...) -> None:
    svc = DiscoveryService(repo, settings, clock=clock, external_channels=("telegram",))
    # run a pass that creates exactly one alert
    rows = select_all(schema.alert_deliveries)
    assert [(r.channel, r.status, r.attempts) for r in rows] == [("telegram", "pending", 0)]
    assert rows[0].next_attempt_at == clock()

def test_no_external_channel_no_rows(...) -> None:  # default external_channels=()
    assert select_all(schema.alert_deliveries) == []

def test_evaluation_never_calls_the_transport(...) -> None:
    # Construct the service with external_channels=("telegram",) and patch
    # bip.rpc.transport.HttpsTransport.post_json to raise AssertionError; the pass must succeed.

def test_rerun_does_not_duplicate_pending_rows(...) -> None:
    # A second pass over the same signal creates no new alert and no new delivery row.
```

Also: `tests/api/test_forward_migration.py`-style check that a v22 database upgrades to v23 with `alert_deliveries` present and existing alerts intact.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/discovery/test_alert_outbox.py -v`
Expected: FAIL (`alert_deliveries` is not defined in `schema`).

- [ ] **Step 3: Implement the table, migration v23 and pending inserts**

Insert the pending rows inside the existing `_evaluate_alerts` transaction, right after the alert insert. Use the inserted alert id (`result.inserted_primary_key[0]`). Then find tests that pin the schema version and update them: `grep -rn "SCHEMA_VERSION\|== 22\b" tests`.

- [ ] **Step 4: Run to verify they pass, then the discovery and migration suites**

Run: `uv run pytest tests/discovery tests/api/test_forward_migration.py -q 2>&1 | tail -3`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add bip/db/schema.py bip/db/engine.py bip/discovery/service.py tests/
git commit -m "Queue external alert deliveries in an outbox row so a slow or unreachable channel can never stall or roll back a discovery pass"
```

### Task 3: TelegramChannel client

**Files:**
- Create: `bip/discovery/telegram.py`
- Modify: `bip/discovery/notify.py` (add `DeliveryOutcome`, `ExternalChannel` protocol and `format_alert_text`)
- Test: `tests/discovery/test_telegram.py`

**Interfaces:**
- Produces, in `notify.py`:
  - `DeliveryOutcome` (frozen dataclass): `status: Literal["delivered", "retry", "permanent"]`, `error: str | None = None`, `retry_after_s: float | None = None`.
  - `class ExternalChannel(Protocol): name: str; def send(self, message: AlertMessage) -> DeliveryOutcome`.
  - `format_alert_text(message: AlertMessage) -> str`. Its lines are the header from Global Constraints, `Severity: <severity>`, `<title>` and `Signal #<signal_id>`, truncated to 4096 characters.
- Produces, in `telegram.py`:
  - `TELEGRAM_HOST = "api.telegram.org"`.
  - `TOKEN_PATTERN = re.compile(r"^\d{5,16}:[A-Za-z0-9_-]{30,64}$")`.
  - `CHAT_ID_PATTERN = re.compile(r"^(-?\d{1,20}|@[A-Za-z0-9_]{5,32})$")`.
  - `class TelegramChannel`, with `name = "telegram"` and `__init__(self, token: SecretStr, chat_id: str, transport: Transport, *, timeout_s: float = 10.0)`. It raises `ConfigurationError` when either pattern fails, and the message never includes the value. It also has `send(self, message: AlertMessage) -> DeliveryOutcome`.

- [ ] **Step 1: Write the failing tests**

Write a local `RecordingTransport` with `post_json(endpoint, body, *, timeout_s, max_bytes)`. It records the endpoint and body and returns a scripted `TransportResponse`.

```python
TOKEN = SecretStr("123456:" + "A" * 35)


def test_sends_plain_text_to_send_message() -> None:
    t = RecordingTransport(TransportResponse(200, b'{"ok":true,"result":{}}', 5))
    out = TelegramChannel(TOKEN, "-1001", t).send(MSG)
    assert out == DeliveryOutcome("delivered")
    ep, body = t.calls[0]
    assert ep.host == "api.telegram.org"
    assert (
        ep.url.get_secret_value()
        == f"https://api.telegram.org/bot{TOKEN.get_secret_value()}/sendMessage"
    )
    payload = json.loads(body)
    assert payload == {"chat_id": "-1001", "text": format_alert_text(MSG)}  # no parse_mode
    assert payload["text"].startswith("BIP alert — statistical signal (not a verified observation)")


def test_text_truncated_to_4096() -> None: ...  # 10_000-char title -> len(text) == 4096
def test_ok_false_is_not_delivered() -> (
    None
): ...  # 200 + {"ok":false,"description":"x"} -> status "permanent"
def test_429_honours_retry_after() -> (
    None
): ...  # 429 + {"ok":false,"parameters":{"retry_after":17}} -> ("retry", retry_after_s=17.0)
def test_5xx_and_transport_error_retry() -> (
    None
): ...  # 502 -> "retry"; TransportError("timeout", ...) -> "retry"
def test_401_403_400_permanent() -> None: ...
def test_non_json_body_retries() -> (
    None
): ...  # 200 + b"<html>" -> "retry", error mentions invalid JSON
def test_token_with_slash_is_refused() -> None:
    with pytest.raises(ConfigurationError) as e:
        TelegramChannel(SecretStr("123456:abc/../x" + "A" * 30), "1", t)
    assert "abc" not in str(e.value)


def test_chat_id_pattern() -> None: ...  # "1/2", "", "@ab" refused; "-1001", "@bip_alerts" accepted
def test_errors_never_contain_the_token() -> (
    None
): ...  # a 400 body that echoes the token -> outcome.error has no token
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/discovery/test_telegram.py -v`
Expected: FAIL (`bip.discovery.telegram` does not exist).

- [ ] **Step 3: Implement `format_alert_text`, `DeliveryOutcome`, `ExternalChannel` and `TelegramChannel`**

Build `ResolvedEndpoint(url=SecretStr(...), host=TELEGRAM_HOST, from_environment=True)` and call `transport.post_json(..., timeout_s=timeout_s, max_bytes=64 * 1024)`. Pass every `error` through `bip.security.secrets.redact` and cut it to 200 characters. Mapping:
- 2xx with `ok: true` → `delivered`;
- 429 → `retry` (with `parameters.retry_after` when it is a number);
- 5xx, `TransportError` or invalid JSON → `retry`;
- any other 4xx, or `ok: false` on 2xx → `permanent`.

Also `register_secret` the token in `__init__`.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/discovery/test_telegram.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add bip/discovery/telegram.py bip/discovery/notify.py tests/discovery/test_telegram.py
git commit -m "Add a Telegram channel over the hardened transport that validates Telegram's ok flag and never exposes the bot token"
```

### Task 4: Dispatcher, settings, job, test endpoint, wiring and docs

**Files:**
- Create: `bip/discovery/delivery.py`
- Modify: `bip/config/settings.py` (`AlertSettings`; `Settings.alerts`; add `"BIP_TELEGRAM_BOT_TOKEN"` to `_TOOL_NAMES`)
- Modify: `bip/api/app.py` (build the `TelegramChannel` when enabled; pass `external_channels` to `DiscoveryService`; add `dispatcher` to the context)
- Modify: `bip/api/context.py` or wherever `AppContext` is defined (`grep -n "class AppContext" -r bip`), adding a `dispatcher: AlertDispatcher | None` field
- Modify: `bip/ops/jobs.py` (job `alert_delivery`)
- Modify: `bip/discovery/notify.py` (`unavailable_channels(telegram_configured: bool)`, with a telegram reason when it is not configured)
- Modify: `bip/discovery/service.py` (`channels()` lists `telegram` as available when an external channel is configured)
- Modify: `bip/api/routes/discovery.py` (`POST /alerts/channels/telegram/test`)
- Modify: `.env.example`, `docs/OPERATIONS.md` (setup: BotFather, chat id, env vars, enable), `docs/PROVIDER_REGISTRY.md` (Telegram section), `docs/API_REFERENCE.md` (regenerate), `DECISIONS.md` (ADR-054, Proposed), `CURRENT_STATE.md`, `IMPLEMENTATION_LOG.md`
- Test: `tests/discovery/test_alert_delivery.py`, `tests/unit/test_settings.py` (append), `tests/discovery/test_api.py` (append)

**Interfaces:**
- Consumes `ExternalChannel`, `DeliveryOutcome` and `format_alert_text` (Task 3), and the `alert_deliveries` table (Task 2).
- Produces `AlertSettings` (`BIP_ALERTS__*`, `frozen=True, extra="forbid"`):
  - `telegram_enabled: bool = False`;
  - `telegram_chat_id: str | None = None`, matched against `CHAT_ID_PATTERN`; required when enabled;
  - `max_attempts: int = 5` (ge=1, le=20);
  - `retry_base_s: float = 30.0` (ge=1, le=3600);
  - `per_run_limit: int = 20` (ge=1, le=200);
  - `min_send_interval_s: float = 1.0` (ge=1.0, le=60). The 1 msg/s per chat comes from the Telegram FAQ;
  - `delivery_interval_minutes: int = 1` (ge=1, le=60).
- Produces `AlertDispatcher(repo: Repository, cfg: AlertSettings, channels: Mapping[str, ExternalChannel], *, clock: Callable[[], datetime] = utcnow, sleep: Callable[[float], None] = time.sleep)`, with `dispatch() -> dict[str, int]` returning keys `delivered`, `retrying`, `failed`, `skipped`.

- [ ] **Step 1: Write the failing tests**

```python
def test_delivers_due_rows_and_marks_delivered(...) -> None:
    # one pending row, fake channel returns delivered
    assert d.dispatch() == {"delivered": 1, "retrying": 0, "failed": 0, "skipped": 0}
    row = only_row(); assert row.status == "delivered" and row.attempts == 1 and row.delivered_at == now

def test_second_dispatch_sends_nothing(...) -> None: ...      # fake.sent == 1 after two dispatches
def test_retry_backoff_doubles(...) -> None: ...              # retry without retry_after: next_attempt_at == now + 30s, then +60s
def test_retry_after_overrides_backoff(...) -> None: ...      # retry_after_s=17 -> now + 17s
def test_backoff_capped_at_one_hour(...) -> None: ...
def test_failed_after_max_attempts(...) -> None: ...          # 5th retry -> status "failed"
def test_permanent_fails_immediately(...) -> None: ...
def test_not_due_rows_untouched(...) -> None: ...             # next_attempt_at in the future -> not sent
def test_per_run_limit_and_send_interval(...) -> None: ...    # 25 due, limit 20 -> 20 sent; sleep called 19 times with 1.0
def test_rows_for_unconfigured_channel_skipped(...) -> None:  # channel "email" row -> skipped, status stays pending
def test_last_error_never_contains_the_token(...) -> None:    # TelegramChannel + transport echoing the token in a 400 body
def test_concurrent_dispatch_reports_skipped(...) -> None:    # dispatcher lock held -> {"skipped": ...} and no sends
```

Settings tests (append to `tests/unit/test_settings.py`):
- `BIP_ALERTS__TELEGRAM_ENABLED=true` without a chat id raises `ConfigurationError` mentioning `telegram_chat_id`;
- `BIP_ALERTS__TELEGRAM_CHAT_ID=1/2` is refused;
- `unknown_bip_variables({"BIP_TELEGRAM_BOT_TOKEN": "x", "BIP_TELEGRAM_BOT_TOKEN_FILE": "f"}, "BIP_AI_API_KEY") == []`.

API tests (append to `tests/discovery/test_api.py`):
- the default channels response is unchanged apart from a `telegram` entry under `unavailable`;
- `POST /alerts/channels/telegram/test` returns 409 when it is not configured;
- with a configured fake channel it returns `{"status": "delivered"}`, and the response body does not contain the token;
- jobs: `alert_delivery` is absent by default and present (with `contacts_providers=True`) when telegram is enabled.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/discovery/test_alert_delivery.py tests/unit/test_settings.py tests/discovery/test_api.py -q 2>&1 | tail -5`
Expected: FAIL (`bip.discovery.delivery` does not exist).

- [ ] **Step 3: Implement `AlertDispatcher`, `AlertSettings`, the wiring, the job and the endpoint**

Dispatcher:
- Hold a `threading.Lock` and acquire it non-blocking. If it is already held, return `skipped` equal to the number of due rows and send nothing.
- Select due `pending` rows joined to `alerts`, ordered by `id`, limited to `per_run_limit`. Build an `AlertMessage` from the alert's `title`, `severity`, `signal_id` and `f"{rule_id}:{signal_id}"`.
- Send outside any transaction, then update the row in its own short transaction. Backoff is `min(3600, retry_after_s or retry_base_s * 2 ** (attempts - 1))`.
- Call `sleep(min_send_interval_s)` between sends, not after the last one.
- Write a docstring stating at-least-once delivery: a crash between send and update can resend once.

App wiring: if `cfg.alerts.telegram_enabled`, read the token with `read_secret("BIP_TELEGRAM_BOT_TOKEN")`. If it is missing, raise `ConfigurationError("telegram enabled but BIP_TELEGRAM_BOT_TOKEN is not set")`. Build the channel with `HttpsTransport(use_env_proxy=cfg.rpc.use_env_proxy)`.
Job: `Job("alert_delivery", timedelta(minutes=cfg.alerts.delivery_interval_minutes), lambda: str(ctx.dispatcher.dispatch()), "Send queued alerts to Telegram. CONTACTS TELEGRAM.", contacts_providers=True)`. Register it only when `ctx.dispatcher` is set.
Endpoint: a writable context. It sends `AlertMessage(alert_key="test", severity="low", title="BIP test message", signal_id=0)` directly through the channel (no outbox row) and returns `{"status": outcome.status, "error": outcome.error}`.

- [ ] **Step 4: Run to verify they pass, then regenerate the API reference**

Run: `uv run pytest tests/discovery tests/unit/test_settings.py tests/unit/test_api_reference.py -q 2>&1 | tail -3` after `uv run python -m bip.tools.api_contract`.
Expected: all passed. `docs/API_REFERENCE.md` gains the test route.

- [ ] **Step 5: Docs and ADR**

- ADR-054 (Proposed): "Telegram alert delivery through an outbox". Record: owner choice 2026-11-22; outbox plus a job instead of an inline send; at-least-once; off by default; token handling; plain text; Telegram facts with `source_url` and `verified_at`.
- `docs/OPERATIONS.md`: the setup steps. The owner creates the bot with BotFather, gets the chat id, sets `BIP_TELEGRAM_BOT_TOKEN`, `BIP_ALERTS__TELEGRAM_CHAT_ID`, `BIP_ALERTS__TELEGRAM_ENABLED=true` and `BIP_WORKERS__ENABLED=true`, then calls the test endpoint once.
- `.env.example`: placeholders for those variables (no real values).
- `CURRENT_STATE.md` and `IMPLEMENTATION_LOG.md`: describe the change and paste the real check output.

- [ ] **Step 6: Full checks**

Run: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest -q 2>&1 | tail -3`
Run: `cd frontend && npm run lint && npm run typecheck && npx vitest run src/pages/alerts.test.tsx 2>&1 | tail -3`
Expected: all clean. The Alerts page shows `telegram` under the unavailable channels without any frontend change. If its test pins the unavailable list, update the fixture.

- [ ] **Step 7: Commit and push**

```bash
git add -A bip tests docs .env.example DECISIONS.md CURRENT_STATE.md IMPLEMENTATION_LOG.md
git commit -m "Deliver alerts to Telegram from the outbox with bounded retries so the owner is notified without the channel ever blocking discovery"
git fetch origin claude/modest-fermi-0yklvj && git status -sb | head -1
git push origin claude/modest-fermi-0yklvj
```
Expected: the branch is not behind before the push; the push succeeds.
