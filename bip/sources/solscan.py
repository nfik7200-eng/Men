"""Solscan API (docs checked 2026-11-23: docs.solscan.io).

``public-api.solscan.io`` with header ``token``.
"""

from __future__ import annotations

from typing import Any

from bip.sources.client import SourceClient, SourceResponse, as_dict, as_list

PROVIDER = "solscan"
BASE = "https://public-api.solscan.io"


def ping(client: SourceClient) -> SourceResponse:
    key = client.require_key(PROVIDER)
    return client.get(
        PROVIDER,
        f"{BASE}/chain/info",
        headers={"token": key},
    )


def account_transactions(
    client: SourceClient, address: str, limit: int = 10
) -> tuple[list[dict[str, Any]], SourceResponse]:
    key = client.require_key(PROVIDER)
    resp = client.get(
        PROVIDER,
        f"{BASE}/account/transactions",
        params={"account": address, "limit": str(max(1, min(limit, 50)))},
        headers={"token": key},
    )
    body = as_dict(resp.data)
    data = as_list(body.get("data")) or as_list(body.get("result")) or []
    out: list[dict[str, Any]] = []
    for r in data:
        d = as_dict(r)
        out.append(
            {
                "tx_hash": d.get("txHash") or d.get("signature"),
                "block_time": d.get("blockTime") or d.get("time"),
                "slot": d.get("slot"),
                "status": d.get("status"),
                "fee": d.get("fee"),
            }
        )
    return out, resp
