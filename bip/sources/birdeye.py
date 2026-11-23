"""Birdeye API (docs checked 2026-11-23: docs.birdeye.so).

``public-api.birdeye.so`` with header ``X-API-KEY`` (or ``X-Chain: solana``).
"""

from __future__ import annotations

from typing import Any

from bip.sources.client import SourceClient, SourceResponse, as_dict

PROVIDER = "birdeye"
BASE = "https://public-api.birdeye.so"


def ping(client: SourceClient) -> SourceResponse:
    key = client.require_key(PROVIDER)
    return client.get(
        PROVIDER,
        f"{BASE}/defi/supported_chains",
        headers={"X-API-KEY": key},
    )


def token_price(client: SourceClient, address: str) -> tuple[dict[str, Any], SourceResponse]:
    key = client.require_key(PROVIDER)
    resp = client.get(
        PROVIDER,
        f"{BASE}/defi/price",
        params={"address": address},
        headers={"X-API-KEY": key, "X-Chain": "solana"},
    )
    body = as_dict(resp.data)
    data = as_dict(body.get("data"))
    return {
        "address": address,
        "value": data.get("value"),
        "update_unix_time": data.get("updateUnixTime"),
        "price_change_24h": data.get("priceChange24h"),
    }, resp
