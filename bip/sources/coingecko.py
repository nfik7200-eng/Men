"""CoinGecko prices (docs checked 2026-11-23: docs.coingecko.com authentication pages).

Demo plan: ``api.coingecko.com/api/v3`` with header ``x-cg-demo-api-key``; without a key the same
host answers keyless with lower limits. Pro plan: ``pro-api.coingecko.com/api/v3`` with header
``x-cg-pro-api-key``. A price that is missing or not a finite number is skipped, never zero.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from bip.sources.client import SourceClient, SourceResponse, as_dict

PROVIDER = "coingecko"
NATIVE_IDS = {"ethereum": "ethereum", "solana": "solana"}
"""BIP chain -> CoinGecko coin id of its native asset."""
PLATFORM_CHAIN = {"ethereum": "ethereum", "solana": "solana"}
"""CoinGecko asset platform -> BIP chain."""


@dataclass(frozen=True, slots=True)
class Price:
    chain: str
    asset: str
    """``native`` or the token contract address (lower-cased for EVM)."""
    currency: str
    price: str
    """Decimal string exactly as parsed (no float rounding on storage)."""
    provider_updated_at: datetime | None


def _base(client: SourceClient) -> tuple[str, dict[str, str]]:
    plan = client.cfg.coingecko_plan
    key = client.key(PROVIDER)
    if plan == "pro":
        return "https://pro-api.coingecko.com/api/v3", {
            "x-cg-pro-api-key": client.require_key(PROVIDER)
        }
    if plan == "demo" and key:
        return "https://api.coingecko.com/api/v3", {"x-cg-demo-api-key": key}
    return "https://api.coingecko.com/api/v3", {}


def ping(client: SourceClient) -> SourceResponse:
    base, headers = _base(client)
    return client.get(PROVIDER, f"{base}/ping", headers=headers)


def _price(entry: object, currency: str) -> tuple[str | None, datetime | None]:
    data = as_dict(entry)
    if not data:
        return None, None
    raw = data.get(currency)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
        return None, None
    try:
        value = str(Decimal(str(raw)))
    except InvalidOperation:
        return None, None
    ts = data.get("last_updated_at")
    when = datetime.fromtimestamp(ts, UTC) if isinstance(ts, int) and ts > 0 else None
    return value, when


def native_prices(
    client: SourceClient, chains: list[str]
) -> tuple[list[Price], int, SourceResponse]:
    base, headers = _base(client)
    cur = client.cfg.price_currency
    ids = [NATIVE_IDS[c] for c in chains if c in NATIVE_IDS]
    resp = client.get(
        PROVIDER,
        f"{base}/simple/price",
        params={"ids": ",".join(ids), "vs_currencies": cur, "include_last_updated_at": "true"},
        headers=headers,
    )
    out: list[Price] = []
    missing = 0
    body = as_dict(resp.data)
    for chain in chains:
        if chain not in NATIVE_IDS:
            continue
        value, when = _price(body.get(NATIVE_IDS[chain]), cur)
        if value is None:
            missing += 1
        else:
            out.append(Price(chain, "native", cur, value, when))
    return out, missing, resp


def token_prices(
    client: SourceClient, platform: str, addresses: list[str]
) -> tuple[list[Price], int, SourceResponse]:
    base, headers = _base(client)
    cur = client.cfg.price_currency
    resp = client.get(
        PROVIDER,
        f"{base}/simple/token_price/{platform}",
        params={
            "contract_addresses": ",".join(addresses),
            "vs_currencies": cur,
            "include_last_updated_at": "true",
        },
        headers=headers,
    )
    body = as_dict(resp.data)
    lowered = {str(k).lower(): v for k, v in body.items()}
    chain = PLATFORM_CHAIN.get(platform, platform)
    out: list[Price] = []
    missing = 0
    for address in addresses:
        key = address.lower() if address.startswith("0x") else address
        value, when = _price(lowered.get(address.lower()), cur)
        if value is None:
            missing += 1
        else:
            out.append(Price(chain, key, cur, value, when))
    return out, missing, resp
