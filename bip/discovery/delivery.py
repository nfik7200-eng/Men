"""Alert dispatcher: sends queued outbox rows (``alert_deliveries``) to external channels.

Discovery writes a ``pending`` row per external channel in the same transaction as the alert, with
no network I/O. A scheduler job calls :meth:`AlertDispatcher.dispatch`, which claims rows that are
due, sends each one **outside** any database transaction, and then records the outcome in its own
short transaction.

Delivery is **at-least-once**. A crash after a send but before its status update leaves the row
``pending``, so the next run sends that alert once more. A ``delivered`` or ``failed`` row is never
sent again.

Retries back off exponentially (``retry_base_s * 2 ** (attempts - 1)``), or wait for the channel's
``retry_after`` when it gives one. Both are capped at one hour. A row is ``failed`` after
``max_attempts`` sends, or at once on a permanent error. Sends are spaced by
``min_send_interval_s`` (Telegram: about one message per second per chat).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update

from bip.config.settings import AlertSettings
from bip.db import schema
from bip.db.repository import Repository
from bip.db.types import utcnow
from bip.discovery.notify import AlertMessage, DeliveryOutcome, ExternalChannel
from bip.security.secrets import redact_text

MAX_BACKOFF_S = 3600.0
MAX_ERROR_CHARS = 200


class AlertDispatcher:
    def __init__(
        self,
        repo: Repository,
        cfg: AlertSettings,
        channels: Mapping[str, ExternalChannel],
        *,
        clock: Callable[[], datetime] = utcnow,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._repo = repo
        self._cfg = cfg
        self.channels: dict[str, ExternalChannel] = dict(channels)
        self._clock = clock
        self._sleep = sleep
        self.lock = threading.Lock()
        """Held for a whole dispatch run; a second concurrent run sends nothing."""

    def dispatch(self) -> dict[str, int]:
        """Send due rows once. Returns counts of ``delivered``, ``retrying``, ``failed`` and
        ``skipped`` (due rows not sent: no configured channel, or another run in progress)."""
        result = {"delivered": 0, "retrying": 0, "failed": 0, "skipped": 0}
        if not self.lock.acquire(blocking=False):
            result["skipped"] = self._count_due(configured_only=False)
            return result
        try:
            now = self._clock()
            result["skipped"] = self._count_due(configured_only=False, now=now) - self._count_due(
                configured_only=True, now=now
            )
            for i, row in enumerate(self._claim(now)):
                if i:
                    self._sleep(self._cfg.min_send_interval_s)
                outcome = self._send(row)
                result[self._record(row, outcome)] += 1
        finally:
            self.lock.release()
        return result

    # ------------------------------------------------------------------ internals

    def _due(self, now: datetime) -> Any:
        d = schema.alert_deliveries
        return (d.c.status == "pending") & (d.c.next_attempt_at <= now)

    def _count_due(self, *, configured_only: bool, now: datetime | None = None) -> int:
        d = schema.alert_deliveries
        q = select(func.count()).select_from(d).where(self._due(now or self._clock()))
        if configured_only:
            q = q.where(d.c.channel.in_(list(self.channels)))
        with self._repo.transaction() as conn:
            return int(conn.execute(q).scalar_one())

    def _claim(self, now: datetime) -> list[Any]:
        d, a = schema.alert_deliveries, schema.alerts
        q = (
            select(
                d.c.id,
                d.c.channel,
                d.c.attempts,
                a.c.rule_id,
                a.c.signal_id,
                a.c.severity,
                a.c.title,
            )
            .join(a, a.c.id == d.c.alert_id)
            .where(self._due(now), d.c.channel.in_(list(self.channels)))
            .order_by(d.c.id)
            .limit(self._cfg.per_run_limit)
        )
        with self._repo.transaction() as conn:
            return list(conn.execute(q).all())

    def _send(self, row: Any) -> DeliveryOutcome:
        message = AlertMessage(
            alert_key=f"{row.rule_id}:{row.signal_id}",
            severity=str(row.severity),
            title=str(row.title),
            signal_id=int(row.signal_id),
        )
        try:
            return self.channels[row.channel].send(message)
        except Exception as exc:  # noqa: BLE001 - a channel bug must not stop the other rows
            return DeliveryOutcome("retry", f"channel error: {type(exc).__name__}")

    def _record(self, row: Any, outcome: DeliveryOutcome) -> str:
        now = self._clock()
        attempts = int(row.attempts) + 1
        error = redact_text(outcome.error)[:MAX_ERROR_CHARS] if outcome.error else None
        values: dict[str, Any] = {"attempts": attempts, "last_error": error}
        if outcome.status == "delivered":
            values |= {"status": "delivered", "delivered_at": now, "last_error": None}
            counted = "delivered"
        elif outcome.status == "permanent" or attempts >= self._cfg.max_attempts:
            values["status"] = "failed"
            counted = "failed"
        else:
            wait = outcome.retry_after_s
            if wait is None:
                wait = self._cfg.retry_base_s * 2 ** (attempts - 1)
            values["next_attempt_at"] = now + timedelta(seconds=min(MAX_BACKOFF_S, max(0.0, wait)))
            counted = "retrying"
        d = schema.alert_deliveries
        with self._repo.transaction() as conn:
            conn.execute(
                update(d).where(d.c.id == row.id, d.c.status == "pending").values(**values)
            )
        return counted
