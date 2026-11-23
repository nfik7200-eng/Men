"""Setup from the UI (ADR-056): write-only secrets, allowlisted settings, restart on request."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from bip.api.access import required_role
from bip.api.app import create_app
from bip.api.lifecycle import RestartController
from bip.config import Settings
from tests.api.conftest import roomy_guard

API = "/api/v1"
ALCHEMY = "BIP_PROVIDER_ALCHEMY_ETH_URL"
ALCHEMY_URL = "https://eth-mainnet.g.alchemy.com/v2/abcDEF123456ghiJKL"
TOKEN = "BIP_TELEGRAM_BOT_TOKEN"
GOOD_TOKEN = "123456789:" + "A" * 35
ALL_INTERFACES = "0.0.0.0"  # noqa: S104 - a value the setup API must refuse


def secret(body: dict[str, Any], name: str) -> dict[str, Any]:
    return next(s for s in body["secrets"] if s["name"] == name)


def setting(body: dict[str, Any], key: str) -> dict[str, Any]:
    return next(s for s in body["settings"] if s["key"] == key)


# ---------- reading ----------


def test_setup_lists_every_credential_and_editable_setting(client: TestClient) -> None:
    body = client.get(f"{API}/setup").json()
    alchemy = secret(body, ALCHEMY)
    assert alchemy["set"] is False
    assert alchemy["provider_id"] == "alchemy-eth"
    assert alchemy["applies"] == "immediately"
    assert alchemy["docs_url"].startswith("https://")
    assert alchemy["checkable"] is True  # an RPC adapter exists, so a health check can run
    assert secret(body, "BIP_PROVIDER_THEGRAPH_KEY")["checkable"] is False  # no integration yet
    etherscan = secret(body, "BIP_PROVIDER_ETHERSCAN_KEY")
    assert (etherscan["checkable"], etherscan["check_path"]) == (True, "/sources/etherscan/test")
    assert alchemy["check_path"] == "/providers/alchemy-eth/health-check"
    assert secret(body, "BIP_PROVIDER_DUNE_KEY")["requires_subscription"] is True
    assert secret(body, TOKEN)["checkable"] is False
    assert secret(body, TOKEN)["applies"] == "after_restart"
    assert secret(body, "BIP_AI_API_KEY")["group"] == "AI research"
    workers = setting(body, "workers.enabled")
    assert (workers["value"], workers["source"], workers["kind"]) == (False, "default", "bool")
    assert setting(body, "workers.collector_enabled")["contacts_providers"] is True
    assert body["restart"] == {"available": False, "pending": []}
    assert "api.host" not in {s["key"] for s in body["settings"]}


def test_setup_is_admin_only_for_reads_and_writes() -> None:
    assert required_role("GET", "/setup") == "admin"
    assert required_role("POST", "/setup/secrets/X") == "admin"
    assert required_role("POST", "/setup/restart") == "admin"


# ---------- secrets ----------


def test_a_provider_key_applies_immediately_and_is_never_returned(client: TestClient) -> None:
    r = client.post(f"{API}/setup/secrets/{ALCHEMY}", json={"value": ALCHEMY_URL})
    assert r.status_code == 200, r.text
    assert ALCHEMY_URL not in r.text
    body = client.get(f"{API}/setup")
    assert ALCHEMY_URL not in body.text
    entry = secret(body.json(), ALCHEMY)
    assert (entry["set"], entry["source"], entry["updated_by"]) == (True, "setup", "local")
    assert body.json()["restart"]["pending"] == []  # live: no restart needed
    provider = client.get(f"{API}/providers/alchemy-eth").json()
    assert provider["configuration_status"] == "configured"
    audit = client.get(f"{API}/audit", params={"action": "setup.secret.set"}).json()["items"]
    assert audit[0]["target"] == ALCHEMY
    assert ALCHEMY_URL not in json.dumps(audit)


def test_a_provider_url_on_a_foreign_host_is_refused(client: TestClient) -> None:
    r = client.post(f"{API}/setup/secrets/{ALCHEMY}", json={"value": "https://evil.example/v2/k"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_setup_value"
    assert "host" in r.json()["error"]["message"]
    assert secret(client.get(f"{API}/setup").json(), ALCHEMY)["set"] is False


def test_a_malformed_telegram_token_is_refused(client: TestClient) -> None:
    r = client.post(f"{API}/setup/secrets/{TOKEN}", json={"value": "not-a-token"})
    assert r.status_code == 422
    assert "not-a-token" not in r.text


def test_unknown_secret_names_are_not_found(client: TestClient) -> None:
    r = client.post(f"{API}/setup/secrets/BIP_SOMETHING_ELSE", json={"value": "x" * 20})
    assert r.status_code == 404


def test_clearing_a_secret(client: TestClient) -> None:
    client.post(f"{API}/setup/secrets/{ALCHEMY}", json={"value": ALCHEMY_URL})
    assert client.delete(f"{API}/setup/secrets/{ALCHEMY}").status_code == 200
    assert secret(client.get(f"{API}/setup").json(), ALCHEMY)["set"] is False
    assert client.delete(f"{API}/setup/secrets/{ALCHEMY}").status_code == 404


def test_a_restart_bound_secret_is_pending_until_restart(client: TestClient) -> None:
    assert (
        client.post(f"{API}/setup/secrets/{TOKEN}", json={"value": GOOD_TOKEN}).status_code == 200
    )
    assert client.get(f"{API}/setup").json()["restart"]["pending"] == [TOKEN]


# ---------- settings ----------


def test_a_setting_is_validated_saved_and_pending(client: TestClient) -> None:
    r = client.post(f"{API}/setup/settings/workers.enabled", json={"value": True})
    assert r.status_code == 200, r.text
    body = client.get(f"{API}/setup").json()
    entry = setting(body, "workers.enabled")
    assert (entry["value"], entry["stored"], entry["source"]) == (False, True, "setup")
    assert body["restart"]["pending"] == ["workers.enabled"]
    assert client.delete(f"{API}/setup/settings/workers.enabled").status_code == 200
    assert setting(client.get(f"{API}/setup").json(), "workers.enabled")["stored"] is None


def test_invalid_and_non_editable_settings_are_refused(client: TestClient) -> None:
    bad = client.post(f"{API}/setup/settings/alerts.telegram_chat_id", json={"value": "bad id"})
    assert bad.status_code == 422
    assert (
        client.post(f"{API}/setup/settings/api.host", json={"value": ALL_INTERFACES}).status_code
        == 404
    )
    wrong_type = client.post(f"{API}/setup/settings/workers.enabled", json={"value": "yes"})
    assert wrong_type.status_code == 422


def test_list_settings_take_lists(client: TestClient) -> None:
    mints = ["So11111111111111111111111111111111111111112"]
    r = client.post(
        f"{API}/setup/settings/solana_collector.spl_mint_allowlist", json={"value": mints}
    )
    assert r.status_code == 200, r.text
    entry = setting(client.get(f"{API}/setup").json(), "solana_collector.spl_mint_allowlist")
    assert entry["stored"] == mints


def test_telegram_cannot_be_enabled_without_token_and_chat(client: TestClient) -> None:
    r = client.post(f"{API}/setup/settings/alerts.telegram_enabled", json={"value": True})
    assert r.status_code == 422
    assert "chat" in r.json()["error"]["message"] or "token" in r.json()["error"]["message"]
    client.post(f"{API}/setup/settings/alerts.telegram_chat_id", json={"value": "-1001234"})
    no_token = client.post(f"{API}/setup/settings/alerts.telegram_enabled", json={"value": True})
    assert no_token.status_code == 422
    assert "token" in no_token.json()["error"]["message"]
    client.post(f"{API}/setup/secrets/{TOKEN}", json={"value": GOOD_TOKEN})
    ok = client.post(f"{API}/setup/settings/alerts.telegram_enabled", json={"value": True})
    assert ok.status_code == 200, ok.text
    # The token cannot be removed while Telegram is switched on from the setup page.
    assert client.delete(f"{API}/setup/secrets/{TOKEN}").status_code == 409


# ---------- restart ----------


def test_restart_is_unavailable_without_the_server_loop(client: TestClient) -> None:
    r = client.post(f"{API}/setup/restart")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "restart_unavailable"


@pytest.fixture
def restartable(
    settings: Settings, engine: Engine
) -> Iterator[tuple[TestClient, RestartController]]:
    controller = RestartController(delay_s=0)
    app = create_app(
        settings,
        engine=engine,
        disk_guard=roomy_guard(settings.resolved_data_root),
        restart=controller,
    )
    with TestClient(app) as c:
        yield c, controller


class FakeServer:
    should_exit = False


def test_restart_asks_the_server_to_exit_and_restart(
    restartable: tuple[TestClient, RestartController],
) -> None:
    client, controller = restartable
    server = FakeServer()
    controller.attach(server)
    assert client.get(f"{API}/setup").json()["restart"]["available"] is True
    r = client.post(f"{API}/setup/restart")
    assert r.status_code == 202
    controller.wait_requested(timeout=5)
    assert server.should_exit is True
    assert controller.consume() is True
    assert controller.consume() is False


def test_store_files_are_private(client: TestClient, settings: Settings) -> None:
    client.post(f"{API}/setup/secrets/{ALCHEMY}", json={"value": ALCHEMY_URL})
    path = Path(settings.resolved_data_root) / "config" / "secrets.json"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
