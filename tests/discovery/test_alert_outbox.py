"""External alert deliveries are queued in an outbox row, never sent inside a discovery pass."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, delete, select, text

from bip.config import load_settings
from bip.db import schema
from bip.db.engine import create_db_engine, migrate
from bip.db.repository import Repository
from bip.discovery.service import DiscoveryService
from tests.discovery.seed import ETH, NOW, Seed
from tests.discovery.test_pipeline import Clock, baseline, big


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    e = create_db_engine(load_settings(data_root=tmp_path / "data", env="test"))
    yield e
    e.dispose()


def service(engine: Engine, external: tuple[str, ...] = ()) -> DiscoveryService:
    settings = load_settings(data_root=Path("/unused"), env="test")
    return DiscoveryService(
        Repository(engine), settings, clock=Clock(NOW), external_channels=external
    )


def one_alert_pass(engine: Engine, svc: DiscoveryService) -> dict[str, Any]:
    seed = Seed(engine)
    baseline(seed)
    big(seed, 900)
    svc.create_rule("everything", min_severity="low")
    result = svc.run(ETH)
    assert result["alerts_created"] == 1
    return result


def deliveries(engine: Engine) -> list[Any]:
    with engine.connect() as conn:
        return list(conn.execute(select(schema.alert_deliveries)).all())


def test_pending_row_per_external_channel(engine: Engine) -> None:
    one_alert_pass(engine, service(engine, ("telegram",)))
    rows = deliveries(engine)
    assert [(r.channel, r.status, r.attempts) for r in rows] == [("telegram", "pending", 0)]
    assert rows[0].next_attempt_at == NOW
    assert rows[0].last_error is None
    assert rows[0].delivered_at is None
    with engine.connect() as conn:
        alert_id = conn.execute(select(schema.alerts.c.id)).scalar_one()
    assert rows[0].alert_id == alert_id


def test_no_external_channel_no_rows(engine: Engine) -> None:
    one_alert_pass(engine, service(engine))
    assert deliveries(engine) == []


def test_evaluation_never_calls_the_transport(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bip.rpc import transport

    def boom(*_a: object, **_k: object) -> object:
        raise AssertionError("the discovery pass must not touch the network")

    monkeypatch.setattr(transport.HttpsTransport, "post_json", boom)
    one_alert_pass(engine, service(engine, ("telegram",)))
    assert len(deliveries(engine)) == 1


def test_rerun_does_not_duplicate_pending_rows(engine: Engine) -> None:
    svc = service(engine, ("telegram",))
    one_alert_pass(engine, svc)
    assert svc.run(ETH)["alerts_created"] == 0
    assert len(deliveries(engine)) == 1


def test_v22_database_upgrades_to_v23_with_alerts_intact(engine: Engine) -> None:
    one_alert_pass(engine, service(engine))
    with engine.begin() as conn:  # roll the file back to v22: no outbox table, no v23+ rows
        conn.execute(text("DROP TABLE alert_deliveries"))
        conn.execute(
            delete(schema.schema_migrations).where(schema.schema_migrations.c.version >= 23)
        )
    assert migrate(engine) == schema.SCHEMA_VERSION == 25
    with engine.connect() as conn:
        tables = set(
            conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).scalars()
        )
        assert "alert_deliveries" in tables
        assert conn.execute(text("SELECT COUNT(*) FROM alerts")).scalar_one() == 1
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    assert migrate(engine) == 25  # idempotent
