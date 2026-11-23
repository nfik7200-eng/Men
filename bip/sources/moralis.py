"""Moralis Data API v2.2 (docs checked 2026-11-23: docs.moralis.com).

``deep-index.moralis.io/api/v2.2`` with header ``X-API-Key``.
"""

from __future__ import annotations

from typing import Any

from bip.sources.client import SourceClient, SourceError, SourceResponse, as_dict, as_list

PROVIDER = "moralis"
BASE = "https://deep-index.moralis.io/api/v2.2"


def ping(client: SourceClient) -> SourceResponse:
    key = client.require_key(PROVIDER)
    return client.get(
        PROVIDER,
        f"{BASE}/wallets/0x0000000000000000000000000000000000000000/tokens",
        params={"chain": "eth"},
        headers={"X-API-Key": key},
    )


def token_balances(
    client: SourceClient, address: str, chain: str = "eth"
) -> tuple[list[dict[str, Any]], SourceResponse]:
    key = client.require_key(PROVIDER)
    resp = client.get(
        PROVIDER,
        f"{BASE}/wallets/{address.lower()}/tokens",
        params={"chain": chain, "exclude_spam": "true"},
        headers={"X-API-Key": key},
    )
    body = as_dict(resp.data)
    result = as_list(body.get("result"))
    if result is None:
        raise SourceError("provider_error", "Moralis: unexpected response structure")
    out: list[dict[str, Any]] = []
    for r in result:
        d = as_dict(r)
        out.append(
            {
                "token_address": d.get("token_address"),
                "symbol": d.get("symbol"),
                "name": d.get("name"),
                "decimals": int(d["decimals"]) if str(d.get("decimals", "")).isdigit() else None,
                "balance": d.get("balance"),
                "balance_formatted": d.get("balance_formatted"),
                "usd_price": d.get("usd_price"),
                "usd_value": d.get("usd_value"),
                "possible_spam": d.get("possible_spam"),
            }
        )
    return out, resp
