"""Tests for Moralis, Birdeye, Solscan, and Bitquery integrations."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine

from bip.config import Settings, load_settings
from bip.db.engine import create_db_engine
from bip.db.repository import Repository
from bip.providers.registry import default_registry
from bip.providers.state import ProviderStateStore
from bip.sources.client import SourceClient
from bip.sources.service import SourcesService
from tests.sources.fakes import FakeRestTransport, ok


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return load_settings(data_root=tmp_path / "data", env="test")


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


def test_moralis_token_balances(engine: Engine, settings: Settings) -> None:
    t = FakeRestTransport()
    route_path = (
        "deep-index.moralis.io/api/v2.2/wallets/0xd8da6bf26964af9d7eed9e03e53415d37aa96045/tokens"
    )
    t.routes[route_path] = ok(
        {
            "result": [
                {
                    "token_address": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                    "symbol": "USDC",
                    "name": "USD Coin",
                    "decimals": "6",
                    "balance": "1000000000",
                    "balance_formatted": "1000.0",
                    "usd_price": "1.0",
                    "usd_value": 1000.0,
                    "possible_spam": False,
                }
            ]
        }
    )
    svc = make(engine, settings, t, {"BIP_PROVIDER_MORALIS_KEY": "mkey"})
    from bip.sources import moralis

    addr = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
    balances, _resp = moralis.token_balances(svc.client, addr)
    assert len(balances) == 1
    assert balances[0]["symbol"] == "USDC"
    assert balances[0]["decimals"] == 6


def test_birdeye_token_price(engine: Engine, settings: Settings) -> None:
    t = FakeRestTransport()
    t.routes["public-api.birdeye.so/defi/price"] = ok(
        {"data": {"value": 150.5, "updateUnixTime": 1795000000, "priceChange24h": 2.5}}
    )
    svc = make(engine, settings, t, {"BIP_PROVIDER_BIRDEYE_KEY": "bkey"})
    from bip.sources import birdeye

    price, _resp = birdeye.token_price(svc.client, "So11111111111111111111111111111111111111112")
    assert price["value"] == 150.5


def test_solscan_account_transactions(engine: Engine, settings: Settings) -> None:
    t = FakeRestTransport()
    t.routes["public-api.solscan.io/account/transactions"] = ok(
        {
            "data": [
                {
                    "txHash": "5" * 88,
                    "blockTime": 1700000000,
                    "slot": 250000000,
                    "status": "Success",
                    "fee": 5000,
                }
            ]
        }
    )
    svc = make(engine, settings, t, {"BIP_PROVIDER_SOLSCAN_KEY": "skey"})
    from bip.sources import solscan

    mint = "So11111111111111111111111111111111111111112"
    txs, _resp = solscan.account_transactions(svc.client, mint)
    assert len(txs) == 1
    assert txs[0]["status"] == "Success"


def test_bitquery_graphql(engine: Engine, settings: Settings) -> None:
    t = FakeRestTransport()
    t.routes["streaming.bitquery.io/graphql"] = ok({"data": {"EIP20": {"Transacts": []}}})
    svc = make(engine, settings, t, {"BIP_PROVIDER_BITQUERY_KEY": "bitkey"})
    from bip.sources import bitquery

    body, _resp = bitquery.query_graphql(svc.client, "{ ping }")
    assert "EIP20" in body["data"]
