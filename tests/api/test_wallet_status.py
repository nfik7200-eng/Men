"""Wallet status endpoint and route_not_found (ADR-031).

The status endpoint answers 200 for any valid address so the UI can tell "nothing stored yet"
from an error. Missing values are null, never 0. Unmatched routes get their own error code so a
missing endpoint is never mistaken for "no data for this wallet".
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.stage3.test_intel_api import Env
from tests.stage5.helpers import API, env_with


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Env]:
    e = env_with(tmp_path)
    yield e
    e.engine.dispose()


@pytest.fixture
def client(env: Env) -> Iterator[TestClient]:
    with TestClient(env.app) as c:
        yield c


def test_unstored_valid_wallet_is_200_with_nulls_not_zeros(client: TestClient) -> None:
    addr = "0x" + "12" * 20
    r = client.get(f"{API}/wallets/ethereum/{addr}/status")
    assert r.status_code == 200
    body = r.json()
    assert body["stored"] is False
    assert body["address"] == addr
    for key in (
        "transaction_count",
        "transfer_count",
        "first_observed_at",
        "last_observed_at",
        "last_ingested_at",
    ):
        assert body[key] is None, key
    assert body["classifications"] == 0  # a real count of related records
    assert body["research_runs"] == 0
    assert body["watchlists"] == []
    assert body["investigations"] == []
    assert body["data_origin"] in {"live", "offline_fixture"}


def test_status_after_ingestion_reports_counts_and_related_records(
    env: Env, client: TestClient
) -> None:
    w = env.eth.wallet
    assert client.post(f"{API}/ingestion/wallet/ethereum/{w}?limit=20").status_code in {200, 201}
    wl = client.post(f"{API}/watchlists", json={"name": "Whales", "description": None}).json()
    client.post(
        f"{API}/watchlists/{wl['id']}/entries",
        json={"chain": "ethereum", "address": w, "note": None},
    )
    case = client.post(
        f"{API}/investigations",
        json={
            "title": "Case A",
            "description": None,
            "subject_chain": "ethereum",
            "subject_address": w,
        },
    ).json()
    client.post(f"{API}/ml/classify/ethereum/{w}")

    body = client.get(f"{API}/wallets/ethereum/{w.upper().replace('0X', '0x')}/status").json()
    assert body["stored"] is True
    assert body["address"] == w  # canonical (lowercase) form
    assert body["transaction_count"] > 0
    assert body["transfer_count"] >= 0
    assert body["last_ingested_at"] is not None
    assert body["classifications"] == 1
    assert body["watchlists"] == [{"id": wl["id"], "name": "Whales"}]
    assert body["investigations"] == [
        {"id": case["id"], "title": "Case A", "status": case["status"]}
    ]


def test_invalid_address_or_chain_is_422(client: TestClient) -> None:
    assert (
        client.get(f"{API}/wallets/ethereum/0x1234/status").json()["error"]["code"]
        == "invalid_input"
    )
    r = client.get(f"{API}/wallets/dogecoin/abc/status")
    assert r.status_code in {404, 422}
    assert r.json()["error"]["code"] != "route_not_found"


def test_unmatched_route_has_its_own_code(client: TestClient) -> None:
    r = client.get(f"{API}/definitely-not-a-route")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "route_not_found"


def test_existing_profile_endpoint_still_404s_for_unstored_wallet(client: TestClient) -> None:
    r = client.get(f"{API}/wallets/ethereum/{'0x' + '34' * 20}")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
