"""AlertDispatcher: sends due outbox rows, retries with backoff, never resends a delivered row."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, select, update

from bip.config import load_settings
from bip.config.settings import AlertSettings
from bip.db import schema
from bip.db.engine import create_db_engine
from bip.db.repository import Repository
from bip.discovery.delivery import AlertDispatcher
from bip.discovery.notify import AlertMessage, DeliveryOutcome, ExternalChannel
from bip.discovery.service import DiscoveryService
from bip.discovery.telegram import TelegramChannel
from bip.providers.registry import ResolvedEndpoint
from bip.rpc.transport import TransportResponse
from tests.discovery.seed import ETH, NOW, Seed
from tests.discovery.test_pipeline import Clock, baseline, big

TOKEN = SecretStr("654321:" + "B" * 35)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    e = create_db_engine(load_settings(data_root=tmp_path / "data", env="test"))
    yield e
    e.dispose()


class FakeChannel:
    name = "telegram"

    def __init__(self, *outcomes: DeliveryOutcome) -> None:
        self.outcomes = list(outcomes)
        self.sent: list[AlertMessage] = []

    def send(self, message: AlertMessage) -> DeliveryOutcome:
        self.sent.append(message)
        if len(self.outcomes) > 1:
            return self.outcomes.pop(0)
        return self.outcomes[0] if self.outcomes else DeliveryOutcome("delivered")


def queue(engine: Engine, alerts: int = 1, channels: tuple[str, ...] = ("telegram",)) -> None:
    """One discovery pass with ``alerts`` matching rules: one alert and outbox row per rule."""
    settings = load_settings(data_root=Path("/unused"), env="test")
    svc = DiscoveryService(
        Repository(engine), settings, clock=Clock(NOW), external_channels=channels
    )
    seed = Seed(engine)
    baseline(seed)
    big(seed, 900)
    for i in range(alerts):
        svc.create_rule(f"rule {i}", min_severity="low")
    assert svc.run(ETH)["alerts_created"] == alerts


def dispatcher(
    engine: Engine,
    channel: ExternalChannel,
    clock: Clock,
    sleeps: list[float] | None = None,
    **cfg: Any,
) -> AlertDispatcher:
    log = sleeps if sleeps is not None else []
    return AlertDispatcher(
        Repository(engine),
        AlertSettings(**cfg),
        {channel.name: channel},
        clock=clock,
        sleep=log.append,
    )


def rows(engine: Engine) -> list[Any]:
    d = schema.alert_deliveries
    with engine.connect() as conn:
        return list(conn.execute(select(d).order_by(d.c.id)).all())


def only_row(engine: Engine) -> Any:
    (row,) = rows(engine)
    return row


def counts(
    delivered: int = 0, retrying: int = 0, failed: int = 0, skipped: int = 0
) -> dict[str, int]:
    return {"delivered": delivered, "retrying": retrying, "failed": failed, "skipped": skipped}


def test_delivers_due_rows_and_marks_delivered(engine: Engine) -> None:
    queue(engine)
    fake = FakeChannel(DeliveryOutcome("delivered"))
    assert dispatcher(engine, fake, Clock(NOW)).dispatch() == counts(delivered=1)
    row = only_row(engine)
    assert (row.status, row.attempts, row.delivered_at, row.last_error) == (
        "delivered",
        1,
        NOW,
        None,
    )
    (msg,) = fake.sent
    with engine.connect() as conn:
        alert = conn.execute(select(schema.alerts)).one()
    assert msg == AlertMessage(
        alert_key=f"{alert.rule_id}:{alert.signal_id}",
        severity=alert.severity,
        title=alert.title,
        signal_id=alert.signal_id,
    )


def test_second_dispatch_sends_nothing(engine: Engine) -> None:
    queue(engine)
    fake = FakeChannel()
    d = dispatcher(engine, fake, Clock(NOW))
    d.dispatch()
    assert d.dispatch() == counts()
    assert len(fake.sent) == 1


def test_retry_backoff_doubles(engine: Engine) -> None:
    queue(engine)
    clock = Clock(NOW)
    d = dispatcher(engine, FakeChannel(DeliveryOutcome("retry", "HTTP 502")), clock)
    assert d.dispatch() == counts(retrying=1)
    row = only_row(engine)
    assert (row.status, row.attempts, row.last_error) == ("pending", 1, "HTTP 502")
    assert row.next_attempt_at == NOW + timedelta(seconds=30)
    clock.now = row.next_attempt_at
    d.dispatch()
    assert only_row(engine).next_attempt_at == clock.now + timedelta(seconds=60)


def test_retry_after_overrides_backoff(engine: Engine) -> None:
    queue(engine)
    d = dispatcher(engine, FakeChannel(DeliveryOutcome("retry", "429", 17.0)), Clock(NOW))
    d.dispatch()
    assert only_row(engine).next_attempt_at == NOW + timedelta(seconds=17)


def test_backoff_capped_at_one_hour(engine: Engine) -> None:
    queue(engine)
    clock = Clock(NOW)
    d = dispatcher(
        engine, FakeChannel(DeliveryOutcome("retry", "x")), clock, max_attempts=20, retry_base_s=600
    )
    for _ in range(4):  # 600, 1200, 2400, then 4800 capped at 3600
        clock.now = only_row(engine).next_attempt_at
        d.dispatch()
    assert only_row(engine).next_attempt_at == clock.now + timedelta(seconds=3600)


def test_huge_retry_after_is_capped_at_one_hour(engine: Engine) -> None:
    queue(engine)
    d = dispatcher(engine, FakeChannel(DeliveryOutcome("retry", "429", 1e12)), Clock(NOW))
    d.dispatch()
    assert only_row(engine).next_attempt_at == NOW + timedelta(seconds=3600)


def test_failed_after_max_attempts(engine: Engine) -> None:
    queue(engine)
    clock = Clock(NOW)
    d = dispatcher(engine, FakeChannel(DeliveryOutcome("retry", "HTTP 502")), clock)
    results: list[dict[str, int]] = []
    for _ in range(5):
        clock.now = only_row(engine).next_attempt_at
        results.append(d.dispatch())
    assert results[-1] == counts(failed=1)
    assert results[:-1] == [counts(retrying=1)] * 4
    row = only_row(engine)
    assert (row.status, row.attempts, row.last_error) == ("failed", 5, "HTTP 502")
    clock.now += timedelta(days=1)
    assert d.dispatch() == counts()


def test_permanent_fails_immediately(engine: Engine) -> None:
    queue(engine)
    d = dispatcher(engine, FakeChannel(DeliveryOutcome("permanent", "HTTP 403")), Clock(NOW))
    assert d.dispatch() == counts(failed=1)
    row = only_row(engine)
    assert (row.status, row.attempts, row.last_error) == ("failed", 1, "HTTP 403")


def test_not_due_rows_untouched(engine: Engine) -> None:
    queue(engine)
    with engine.begin() as conn:
        conn.execute(
            update(schema.alert_deliveries).values(next_attempt_at=NOW + timedelta(minutes=5))
        )
    fake = FakeChannel()
    assert dispatcher(engine, fake, Clock(NOW)).dispatch() == counts()
    assert fake.sent == []
    assert only_row(engine).attempts == 0


def test_per_run_limit_and_send_interval(engine: Engine) -> None:
    queue(engine, alerts=25)
    fake = FakeChannel()
    sleeps: list[float] = []
    assert dispatcher(engine, fake, Clock(NOW), sleeps).dispatch() == counts(delivered=20)
    assert len(fake.sent) == 20
    assert sleeps == [1.0] * 19
    assert sorted(r.status for r in rows(engine)).count("pending") == 5


def test_rows_for_unconfigured_channel_skipped(engine: Engine) -> None:
    queue(engine, channels=("telegram", "email"))
    fake = FakeChannel()
    assert dispatcher(engine, fake, Clock(NOW)).dispatch() == counts(delivered=1, skipped=1)
    by_channel = {r.channel: r.status for r in rows(engine)}
    assert by_channel == {"telegram": "delivered", "email": "pending"}


def test_unconfigured_rows_never_starve_configured_ones(engine: Engine) -> None:
    queue(engine, alerts=3, channels=("email", "telegram"))
    fake = FakeChannel()
    out = dispatcher(engine, fake, Clock(NOW), per_run_limit=2).dispatch()
    assert out["delivered"] == 2


class EchoTransport:
    """Answers 400 with a body that echoes the request URL (and so the token)."""

    def post_json(
        self, endpoint: ResolvedEndpoint, body: bytes, *, timeout_s: float, max_bytes: int
    ) -> TransportResponse:
        url = endpoint.url.get_secret_value()
        payload = {
            "ok": False,
            "description": f"Bad Request: {url} token {TOKEN.get_secret_value()}",
        }
        return TransportResponse(400, json.dumps(payload).encode(), 3)


def test_last_error_never_contains_the_token(engine: Engine) -> None:
    queue(engine)
    channel = TelegramChannel(TOKEN, "-1001", EchoTransport())
    assert dispatcher(engine, channel, Clock(NOW)).dispatch() == counts(failed=1)
    error = only_row(engine).last_error
    assert error is not None
    assert TOKEN.get_secret_value() not in error
    assert "BBBBBBBBBB" not in error


def test_concurrent_dispatch_reports_skipped(engine: Engine) -> None:
    queue(engine, alerts=2)
    fake = FakeChannel()
    d = dispatcher(engine, fake, Clock(NOW))
    with d.lock:
        assert d.dispatch() == counts(skipped=2)
    assert fake.sent == []
