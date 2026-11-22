"""The discovery HTTP contract: validation, status codes, envelopes and workflows."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from bip.api.deps import AppContext
from bip.discovery.notify import AlertMessage, DeliveryOutcome, ExternalChannel
from bip.discovery.service import DiscoveryService
from tests.discovery.seed import E18, ETH, NOW, Seed, eth_addr, eth_tx
from tests.stage2.test_ingestion_api import API, Env

TOKEN = eth_addr(0x7000)


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Env]:
    e = Env(tmp_path)
    yield e
    e.engine.dispose()


@pytest.fixture
def client(env: Env) -> Iterator[TestClient]:
    with TestClient(env.app) as c:
        ctx = env.app.state.bip
        ctx.discovery = DiscoveryService(ctx.repo, ctx.settings, clock=lambda: NOW)
        yield c


def seed_signal(env: Env) -> None:
    seed = Seed(env.engine)
    for i in range(15):
        when = NOW - timedelta(days=10, hours=i)
        seed.tx(eth_tx(i + 1), eth_addr(0x100 + i), when)
        seed.transfer(
            eth_tx(i + 1),
            eth_addr(0x100 + i),
            eth_addr(0x200),
            (1000 + i * 10) * E18,
            when,
            token=TOKEN,
        )
    when = NOW - timedelta(hours=2)
    seed.tx(eth_tx(900), eth_addr(0x300), when)
    seed.transfer(eth_tx(900), eth_addr(0x300), eth_addr(0x400), 5_000_000 * E18, when, token=TOKEN)


def test_detector_catalogue_declares_coverage_cost_and_limits(client: TestClient) -> None:
    body = client.get(f"{API}/discovery/detectors").json()
    assert body["data_origin"] == "stored_observations"
    detectors = body["detectors"]
    assert {d["id"] for d in detectors} == {
        "large_transfer_mad",
        "dormant_reactivation",
        "activity_burst",
        "new_contract_deployment",
        "common_funding_source",
        "new_liquidity_pool",
        "new_token_activity",
        "pool_reserve_change",
        "holder_concentration",
        "solana_new_entities",
    }
    for d in detectors:
        for field in ("coverage", "limitations", "refresh", "cost"):
            assert d[field], f"{d['id']} does not declare {field}"
        assert 0 < d["confidence_cap"] < 1  # no detector claims certainty


def test_empty_database_gives_an_honest_empty_feed(client: TestClient) -> None:
    feed = client.get(f"{API}/discovery/feed").json()
    assert feed["items"] == []
    assert feed["total"] == 0
    assert feed["freshness"]["stale"] is True
    assert feed["freshness"]["last_run_at"] is None


def test_run_then_feed_signals_and_detail(env: Env, client: TestClient) -> None:
    seed_signal(env)
    run = client.post(f"{API}/discovery/run", json={"chain": ETH})
    assert run.status_code == 201
    assert run.json()["status"] == "succeeded"
    assert run.json()["detectors"]["large_transfer_mad:ethereum"]["created"] == 1
    feed = client.get(f"{API}/discovery/feed").json()
    assert feed["total"] == 1
    assert feed["freshness"]["last_run_status"] == "succeeded"
    sig = feed["items"][0]
    detail = client.get(f"{API}/signals/{sig['id']}").json()
    assert detail["explanation"]["score_components"]
    assert detail["evidence"]
    assert detail["coverage"]
    runs = client.get(f"{API}/discovery/runs").json()["items"]
    assert runs[0]["summary"]["detectors"]


def test_signal_filters_sorting_and_pagination(env: Env, client: TestClient) -> None:
    seed_signal(env)
    client.post(f"{API}/discovery/run", json={})
    base = f"{API}/signals"
    assert client.get(base, params={"family": "large_transfers"}).json()["total"] == 1
    assert client.get(base, params={"family": "contracts"}).json()["total"] == 0
    assert client.get(base, params={"min_score": 99}).json()["total"] == 0
    assert client.get(base, params={"subject": TOKEN}).json()["total"] == 1
    assert client.get(base, params={"sort": "recent", "limit": 1}).json()["limit"] == 1
    assert client.get(base, params={"limit": 1, "offset": 5}).json()["items"] == []


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 201},
        {"offset": -1},
        {"status": "weird"},
        {"severity": "critical"},
        {"sort": "random"},
        {"min_score": 101},
    ],
)
def test_signal_query_validation(client: TestClient, params: dict[str, str | int]) -> None:
    assert client.get(f"{API}/signals", params=params).status_code == 422


def test_unknown_signal_is_a_404_with_the_standard_error_shape(client: TestClient) -> None:
    r = client.get(f"{API}/signals/12345")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
    assert client.get(f"{API}/signals/0").status_code == 422


def test_dismiss_and_promote_workflows(env: Env, client: TestClient) -> None:
    seed_signal(env)
    client.post(f"{API}/discovery/run", json={})
    sig_id = client.get(f"{API}/signals").json()["items"][0]["id"]
    # promote needs the id that matches the target
    assert (
        client.post(f"{API}/signals/{sig_id}/promote", json={"target": "watchlist"}).status_code
        == 422
    )
    assert (
        client.post(f"{API}/signals/{sig_id}/promote", json={"target": "case"}).status_code == 422
    )
    wl = client.post(f"{API}/watchlists", json={"name": "Review"}).json()
    ok = client.post(
        f"{API}/signals/{sig_id}/promote", json={"target": "watchlist", "watchlist_id": wl["id"]}
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "promoted"
    assert client.get(f"{API}/watchlists/{wl['id']}").json()["entries"][0]["address"] == TOKEN
    # a promoted signal can still be reviewed but not dismissed
    assert client.post(f"{API}/signals/{sig_id}/dismiss", json={}).status_code == 422
    assert client.post(f"{API}/signals/999/dismiss", json={}).status_code == 404


def test_alert_rules_and_alert_lifecycle(env: Env, client: TestClient) -> None:
    seed_signal(env)
    assert (
        client.post(f"{API}/alert-rules", json={"name": "x", "min_severity": "nope"}).status_code
        == 422
    )
    assert (
        client.post(f"{API}/alert-rules", json={"name": "x", "families": ["bogus"]}).status_code
        == 422
    )
    created = client.post(f"{API}/alert-rules", json={"name": "all", "min_severity": "low"})
    assert created.status_code == 201
    rule_id = created.json()["id"]
    assert (
        client.post(f"{API}/alert-rules", json={"name": "all"}).status_code == 422
    )  # duplicate name
    client.post(f"{API}/discovery/run", json={})
    alerts = client.get(f"{API}/alerts", params={"unread": "true"}).json()
    assert alerts["total"] == 1
    assert alerts["unread"] == 1
    alert_id = alerts["items"][0]["id"]
    assert client.post(f"{API}/alerts/{alert_id}/read").json() == {
        "data_origin": "stored_observations",
        "discovery_version": "1",
        "id": alert_id,
        "read": True,
    }
    assert client.get(f"{API}/alerts", params={"unread": "true"}).json()["total"] == 0
    assert client.post(f"{API}/alerts/9999/read").status_code == 404
    patched = client.patch(f"{API}/alert-rules/{rule_id}", json={"enabled": False})
    assert patched.json()["enabled"] is False
    assert client.patch(f"{API}/alert-rules/{rule_id}", json={"bogus": 1}).status_code == 422
    assert client.delete(f"{API}/alert-rules/{rule_id}").status_code == 204
    assert client.delete(f"{API}/alert-rules/{rule_id}").status_code == 404
    assert client.get(f"{API}/alerts/channels").json()["available"] == ["in_app"]


def test_concurrent_discovery_run_is_refused(env: Env, client: TestClient) -> None:
    ctx = env.app.state.bip
    assert ctx.discovery_lock.acquire(blocking=False)
    try:
        r = client.post(f"{API}/discovery/run", json={})
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "discovery_busy"
    finally:
        ctx.discovery_lock.release()


def test_discovery_run_never_contacts_a_provider(env: Env, client: TestClient) -> None:
    seed_signal(env)
    before = list(env.eth.calls) + list(env.sol.calls)
    client.post(f"{API}/discovery/run", json={})
    assert list(env.eth.calls) + list(env.sol.calls) == before


def test_openapi_lists_the_new_routes(client: TestClient) -> None:
    paths = client.get(f"{API}/openapi.json").json()["paths"]
    for p in (
        "/discovery/run",
        "/discovery/feed",
        "/discovery/detectors",
        "/signals",
        "/signals/{signal_id}/promote",
        "/alerts",
        "/alert-rules",
    ):
        assert f"{API}{p}" in paths


def test_an_as_of_run_is_bounded_and_labelled(client: TestClient) -> None:
    from datetime import UTC, datetime, timedelta

    past = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    run = client.post(f"{API}/discovery/run", json={"chain": ETH, "as_of": past})
    assert run.status_code == 201
    body = run.json()
    assert body["evaluated_as_of"] is not None
    assert body["alerts_created"] == 0
    assert "holder_concentration" in body["not_evaluable_as_of"]
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    bad = client.post(f"{API}/discovery/run", json={"chain": ETH, "as_of": future})
    assert bad.status_code == 422
    ancient = (datetime.now(UTC) - timedelta(days=4000)).isoformat()
    assert client.post(f"{API}/discovery/run", json={"as_of": ancient}).status_code == 422
    naive = client.post(f"{API}/discovery/run", json={"as_of": "2026-01-01T00:00:00"})
    assert naive.status_code == 422


# ------------------------------------------------------------------ Telegram channel

TG_TOKEN = "777777:" + "C" * 35


class _FakeTelegram:
    name = "telegram"

    def __init__(self) -> None:
        self.sent: list[AlertMessage] = []

    def send(self, message: AlertMessage) -> DeliveryOutcome:
        self.sent.append(message)
        return DeliveryOutcome("delivered")


def _ctx(app: FastAPI) -> AppContext:
    ctx: AppContext = app.state.bip
    return ctx


def _telegram_app(
    tmp_path: Path, channel: ExternalChannel | None, **workers: object
) -> tuple[FastAPI, Engine]:
    from bip.api.app import create_app
    from bip.config import load_settings
    from bip.db.engine import create_db_engine
    from tests.api.conftest import roomy_guard

    settings = load_settings(
        data_root=tmp_path / "data",
        env="test",
        alerts={"telegram_enabled": True, "telegram_chat_id": "-1001"},
        workers=workers,
    )
    engine = create_db_engine(settings)
    app = create_app(
        settings,
        engine=engine,
        disk_guard=roomy_guard(settings.resolved_data_root),
        environ={"BIP_TELEGRAM_BOT_TOKEN": TG_TOKEN},
        alert_channels={"telegram": channel} if channel is not None else None,
    )
    return app, engine


def test_default_channels_list_telegram_as_unavailable(client: TestClient) -> None:
    body = client.get(f"{API}/alerts/channels").json()
    assert body["available"] == ["in_app"]
    assert set(body["unavailable"]) == {"email", "push", "sms", "telegram"}
    assert "BIP_ALERTS__TELEGRAM_ENABLED" in body["unavailable"]["telegram"]


def test_telegram_test_endpoint_is_409_when_not_configured(client: TestClient) -> None:
    resp = client.post(f"{API}/alerts/channels/telegram/test")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "channel_not_configured"


def test_telegram_test_endpoint_sends_through_the_channel(tmp_path: Path) -> None:
    fake = _FakeTelegram()
    app, engine = _telegram_app(tmp_path, fake)
    try:
        with TestClient(app) as c:
            channels = c.get(f"{API}/alerts/channels").json()
            assert channels["available"] == ["in_app", "telegram"]
            assert "telegram" not in channels["unavailable"]
            resp = c.post(f"{API}/alerts/channels/telegram/test")
            assert resp.status_code == 200
            assert resp.json() == {"status": "delivered", "error": None}
            assert TG_TOKEN not in resp.text
            assert len(fake.sent) == 1
            assert _ctx(app).dispatcher is not None
    finally:
        engine.dispose()


def test_telegram_enabled_builds_the_real_channel_from_the_secret(tmp_path: Path) -> None:
    from bip.discovery.telegram import TelegramChannel

    app, engine = _telegram_app(tmp_path, None)
    try:
        with TestClient(app):
            dispatcher = _ctx(app).dispatcher
            assert dispatcher is not None
            assert isinstance(dispatcher.channels["telegram"], TelegramChannel)
    finally:
        engine.dispose()


def test_telegram_enabled_without_token_refuses_to_start(tmp_path: Path) -> None:
    from bip.api.app import create_app
    from bip.config import load_settings
    from bip.core.exceptions import ConfigurationError
    from bip.db.engine import create_db_engine
    from tests.api.conftest import roomy_guard

    settings = load_settings(
        data_root=tmp_path / "data",
        env="test",
        alerts={"telegram_enabled": True, "telegram_chat_id": "-1001"},
    )
    engine = create_db_engine(settings)
    app = create_app(
        settings, engine=engine, disk_guard=roomy_guard(settings.resolved_data_root), environ={}
    )
    try:
        with pytest.raises(ConfigurationError) as exc, TestClient(app):
            pass
        assert "BIP_TELEGRAM_BOT_TOKEN" in str(exc.value)
    finally:
        engine.dispose()


def test_alert_delivery_job_only_when_telegram_enabled(env: Env, tmp_path: Path) -> None:
    from bip.ops.jobs import build_jobs

    with TestClient(env.app):
        ctx = env.app.state.bip
        assert "alert_delivery" not in {j.name for j in build_jobs(ctx, ctx.backups)}
    app, engine = _telegram_app(tmp_path / "tg", _FakeTelegram())
    try:
        with TestClient(app):
            ctx = _ctx(app)
            jobs = {j.name: j for j in build_jobs(ctx, ctx.backups)}
            job = jobs["alert_delivery"]
            assert job.contacts_providers is True
            assert job.interval == timedelta(minutes=1)
            assert "delivered" in job.run()
    finally:
        engine.dispose()
