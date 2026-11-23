"""Keyed REST integrations (CoinGecko, Etherscan V2, Dune) behind one guarded client.

The owner creates the accounts and enters the keys on the Setup page; BIP never signs up for or
buys anything. All traffic here goes to a fake transport. Provider data is stored as an observed
provider response, never as independently verified fact.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from bip.api.access import required_role
from bip.api.app import create_app
from bip.config import Settings, load_settings
from bip.db.engine import create_db_engine
from bip.db.repository import Repository
from bip.providers.registry import default_registry
from bip.providers.state import ProviderStateStore
from bip.sources.client import SourceClient, SourceError
from bip.sources.service import SourcesService
from tests.api.conftest import roomy_guard
from tests.sources.fakes import Call, FakeRestTransport, ok

API = "/api/v1"
CG_KEY = "CG-demo-key-0123456789"
ES_KEY = "ESKEY0123456789ABCDEFGHIJKLMNOPQRST"
DUNE_KEY = "dune-key-abcdefghijklmnopqrstuvwx"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
WALLET = "0x00000000000000000000000000000000000000aa"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return load_settings(
        data_root=tmp_path / "data",
        env="test",
        sources={"dune_query_ids": [3373921], "price_tokens": [f"ethereum:{USDC}"]},
    )


@pytest.fixture
def engine(settings: Settings) -> Iterator[Engine]:
    e = create_db_engine(settings)
    yield e
    e.dispose()


def make(
    engine: Engine, settings: Settings, transport: FakeRestTransport, environ: dict[str, str]
) -> SourcesService:
    repo = Repository(engine)
    client = SourceClient(
        default_registry(), ProviderStateStore(repo), transport, environ, settings.sources
    )
    return SourcesService(repo, settings.sources, client)


def coingecko_routes(t: FakeRestTransport) -> None:
    t.routes["api.coingecko.com/api/v3/ping"] = ok({"gecko_says": "(V3) To the Moon!"})
    t.routes["api.coingecko.com/api/v3/simple/price"] = ok(
        {
            "ethereum": {"usd": 3123.45, "last_updated_at": 1795000000},
            "solana": {"usd": 150.5, "last_updated_at": 1795000001},
        }
    )
    t.routes["api.coingecko.com/api/v3/simple/token_price/ethereum"] = ok(
        {USDC: {"usd": 0.9998, "last_updated_at": 1795000002}}
    )


# ---------- the client's guards ----------


def test_a_provider_without_an_integration_is_refused(engine: Engine, settings: Settings) -> None:
    svc = make(engine, settings, FakeRestTransport(), {})
    with pytest.raises(SourceError, match="no integration"):
        svc.client.get("alchemy-eth", "https://eth-mainnet.g.alchemy.com/v2/x")


def test_a_host_outside_the_allow_list_is_refused(engine: Engine, settings: Settings) -> None:
    t = FakeRestTransport()
    svc = make(engine, settings, t, {"BIP_PROVIDER_ETHERSCAN_KEY": ES_KEY})
    with pytest.raises(SourceError, match="not allowed"):
        svc.client.get("etherscan", "https://evil.example/v2/api")
    assert t.calls == []


def test_a_missing_key_is_reported_not_sent(engine: Engine, settings: Settings) -> None:
    t = FakeRestTransport()
    svc = make(engine, settings, t, {})
    out = svc.test("etherscan")
    assert out["ok"] is False
    assert out["code"] == "not_configured"
    assert t.calls == []


def test_the_daily_cap_stops_requests(engine: Engine, tmp_path: Path) -> None:
    settings = load_settings(data_root=tmp_path / "d", env="test", sources={"daily_request_cap": 1})
    t = FakeRestTransport()
    coingecko_routes(t)
    svc = make(engine, settings, t, {})
    assert svc.test("coingecko")["ok"] is True
    second = svc.test("coingecko")
    assert (second["ok"], second["code"]) == (False, "cap_reached")
    assert len(t.calls) == 1


def test_errors_never_contain_the_key(engine: Engine, settings: Settings) -> None:
    t = FakeRestTransport()
    t.routes["api.etherscan.io/v2/api"] = lambda _c: (500, {"message": "boom"})
    svc = make(engine, settings, t, {"BIP_PROVIDER_ETHERSCAN_KEY": ES_KEY})
    out = svc.test("etherscan")
    assert out["ok"] is False
    assert ES_KEY not in json.dumps(out)


# ---------- CoinGecko ----------


def test_coingecko_keyless_and_demo_key_placement(engine: Engine, settings: Settings) -> None:
    t = FakeRestTransport()
    coingecko_routes(t)
    make(engine, settings, t, {}).test("coingecko")
    assert "x-cg-demo-api-key" not in {k.lower() for k in t.calls[-1].headers}
    make(engine, settings, t, {"BIP_PROVIDER_COINGECKO_KEY": CG_KEY}).test("coingecko")
    assert t.calls[-1].headers.get("x-cg-demo-api-key") == CG_KEY
    assert CG_KEY not in t.calls[-1].url


def test_coingecko_pro_plan_uses_the_pro_host(engine: Engine, tmp_path: Path) -> None:
    settings = load_settings(
        data_root=tmp_path / "d", env="test", sources={"coingecko_plan": "pro"}
    )
    t = FakeRestTransport()
    t.routes["pro-api.coingecko.com/api/v3/ping"] = ok({"gecko_says": "ok"})
    out = make(engine, settings, t, {"BIP_PROVIDER_COINGECKO_KEY": CG_KEY}).test("coingecko")
    assert out["ok"] is True
    assert t.calls[-1].headers.get("x-cg-pro-api-key") == CG_KEY


def test_price_refresh_stores_native_and_token_prices(engine: Engine, settings: Settings) -> None:
    t = FakeRestTransport()
    coingecko_routes(t)
    svc = make(engine, settings, t, {})
    out = svc.refresh_prices()
    assert out["stored"] == 3
    prices = {(p["chain"], p["asset"]): p for p in svc.prices()["items"]}
    assert prices[("ethereum", "native")]["price"] == "3123.45"
    assert prices[("solana", "native")]["price"] == "150.5"
    assert prices[("ethereum", USDC)]["price"] == "0.9998"
    assert prices[("ethereum", "native")]["currency"] == "usd"
    assert prices[("ethereum", "native")]["provider_updated_at"] is not None
    assert prices[("ethereum", "native")]["evidence_class"] == "verified_observation"


def test_a_missing_or_invalid_price_is_skipped_never_zero(
    engine: Engine, settings: Settings
) -> None:
    t = FakeRestTransport()
    t.routes["api.coingecko.com/api/v3/simple/price"] = ok(
        {"ethereum": {"usd": "abc"}, "solana": {"usd": float("nan")}}  # NaN parses as JSON
    )
    t.routes["api.coingecko.com/api/v3/simple/token_price/ethereum"] = ok({})
    svc = make(engine, settings, t, {})
    out = svc.refresh_prices()
    assert out["stored"] == 0
    assert out["missing"] == 3
    assert svc.prices()["items"] == []


# ---------- Etherscan ----------


def etherscan_route(t: FakeRestTransport) -> list[Call]:
    seen: list[Call] = []

    def handle(call: Call) -> tuple[int, object]:
        seen.append(call)
        action = call.query.get("action")
        if action == "getsourcecode":
            return 200, {
                "status": "1",
                "message": "OK",
                "result": [
                    {
                        "SourceCode": "contract T {}",
                        "ABI": "[]",
                        "ContractName": "TetherToken",
                        "CompilerVersion": "v0.4.18",
                        "Proxy": "0",
                        "Implementation": "",
                        "LicenseType": "",
                    }
                ],
            }
        if action == "getcontractcreation":
            return 200, {
                "status": "1",
                "message": "OK",
                "result": [
                    {
                        "contractAddress": USDC,
                        "contractCreator": WALLET,
                        "txHash": "0x" + "ab" * 32,
                        "blockNumber": "4634748",
                        "timestamp": "1511829681",
                    }
                ],
            }
        if action in ("txlist", "txlistinternal"):
            if call.query.get("address") == WALLET:
                return 200, {
                    "status": "1",
                    "message": "OK",
                    "result": [
                        {
                            "blockNumber": "100",
                            "timeStamp": "1700000000",
                            "hash": "0x" + "cd" * 32,
                            "from": WALLET,
                            "to": USDC,
                            "value": "1000",
                            "isError": "0",
                        }
                    ],
                }
            return 200, {"status": "0", "message": "No transactions found", "result": []}
        return 200, {"status": "0", "message": "NOTOK", "result": "Invalid action"}

    t.routes["api.etherscan.io/v2/api"] = handle
    return seen


def test_etherscan_contract_metadata(engine: Engine, settings: Settings) -> None:
    t = FakeRestTransport()
    seen = etherscan_route(t)
    svc = make(engine, settings, t, {"BIP_PROVIDER_ETHERSCAN_KEY": ES_KEY})
    info = svc.etherscan_contract(USDC)
    assert info["verified"] is True
    assert info["name"] == "TetherToken"
    assert info["creator"] == WALLET
    assert info["creation_tx"] == "0x" + "ab" * 32
    assert all(c.query["chainid"] == "1" and c.query["apikey"] == ES_KEY for c in seen)
    again = svc.records(provider_id="etherscan", kind="contract")["items"]
    assert again[0]["subject"] == USDC


def test_etherscan_transactions_and_an_empty_result(engine: Engine, settings: Settings) -> None:
    t = FakeRestTransport()
    etherscan_route(t)
    svc = make(engine, settings, t, {"BIP_PROVIDER_ETHERSCAN_KEY": ES_KEY})
    internal = svc.etherscan_transactions(WALLET, kind="internal")
    assert internal["items"][0]["value_wei"] == "1000"
    assert internal["items"][0]["block_number"] == 100
    empty = svc.etherscan_transactions(USDC, kind="normal")
    assert empty["items"] == []  # status 0 + "No transactions found" is a valid empty answer


def test_etherscan_rejects_bad_addresses_and_error_answers(
    engine: Engine, settings: Settings
) -> None:
    t = FakeRestTransport()
    t.routes["api.etherscan.io/v2/api"] = ok(
        {"status": "0", "message": "NOTOK", "result": "Invalid API Key"}
    )
    svc = make(engine, settings, t, {"BIP_PROVIDER_ETHERSCAN_KEY": ES_KEY})
    with pytest.raises(SourceError, match="address"):
        svc.etherscan_contract("not-an-address")
    with pytest.raises(SourceError, match="Invalid API Key"):
        svc.etherscan_contract(USDC)


# ---------- Dune ----------


def test_dune_saved_query_results_are_stored(engine: Engine, settings: Settings) -> None:
    t = FakeRestTransport()

    def results(call: Call) -> tuple[int, object]:
        assert call.headers.get("X-Dune-Api-Key") == DUNE_KEY
        assert int(call.query["limit"]) <= 1000
        return 200, {
            "execution_id": "01HKZ",
            "state": "QUERY_STATE_COMPLETED",
            "execution_ended_at": "2026-11-23T10:00:00Z",
            "result": {
                "rows": [{"chain": "ethereum", "volume": 12.5}],
                "metadata": {"column_names": ["chain", "volume"], "total_row_count": 1},
            },
        }

    t.routes["api.dune.com/api/v1/query/3373921/results"] = results
    svc = make(engine, settings, t, {"BIP_PROVIDER_DUNE_KEY": DUNE_KEY})
    out = svc.refresh_dune()
    assert out["queries"][0] == {"query_id": 3373921, "ok": True, "rows": 1, "error": None}
    rec = svc.records(provider_id="dune", kind="query_result")["items"][0]
    assert rec["subject"] == "3373921"
    assert rec["payload"]["columns"] == ["chain", "volume"]
    assert rec["payload"]["rows"] == [{"chain": "ethereum", "volume": 12.5}]


def test_dune_without_queries_or_key(engine: Engine, tmp_path: Path) -> None:
    settings = load_settings(data_root=tmp_path / "d", env="test")
    svc = make(engine, settings, FakeRestTransport(), {})
    assert svc.refresh_dune() == {"queries": []}


# ---------- API ----------


def test_api_status_test_and_admin_actions(settings: Settings, engine: Engine) -> None:
    t = FakeRestTransport()
    coingecko_routes(t)
    app = create_app(
        settings,
        engine=engine,
        disk_guard=roomy_guard(settings.resolved_data_root),
        source_transport=t,
    )
    with TestClient(app) as c:
        status = c.get(f"{API}/sources").json()
        by = {s["provider_id"]: s for s in status["items"]}
        assert by["coingecko"]["configured"] is True  # keyless plan works without a key
        assert by["etherscan"]["configured"] is False
        assert by["dune"]["requires_subscription"] is True
        h = {"X-BIP-Client": "web"}
        assert c.post(f"{API}/sources/coingecko/test", headers=h).json()["ok"] is True
        assert c.post(f"{API}/sources/prices/refresh", headers=h).json()["stored"] == 3
        assert len(c.get(f"{API}/sources/prices").json()["items"]) == 3
    for path in ("/sources/coingecko/test", "/sources/prices/refresh", "/sources/dune/refresh"):
        assert required_role("POST", path) == "admin"
    assert required_role("POST", "/sources/etherscan/contract/0x1") == "admin"  # spends quota
    assert required_role("GET", "/sources/records") == "viewer"  # stored results only
