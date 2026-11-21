"""Public exchange REST adapters (no keys). Validates payloads; missing is None, not zero."""

from __future__ import annotations

import json
from dataclasses import dataclass

from bip.acquisition.fetcher import Fetcher, Provenance

BINANCE = "https://api.binance.com"


@dataclass(frozen=True, slots=True)
class Candle:
    open_time_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    provenance: Provenance


class ExchangeError(ValueError):
    pass


def parse_binance_klines(body: bytes, provenance: Provenance) -> list[Candle]:
    try:
        rows = json.loads(body)
    except json.JSONDecodeError as e:
        raise ExchangeError("invalid JSON") from e
    if not isinstance(rows, list):
        raise ExchangeError("expected list of klines")
    out: list[Candle] = []
    prev = -1
    for r in rows:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(r, list) or len(r) < 6:  # pyright: ignore[reportUnknownArgumentType]
            raise ExchangeError("malformed kline row")
        try:
            c = Candle(int(r[0]), *(float(x) for x in r[1:6]), provenance)  # type: ignore[arg-type]  # pyright: ignore
        except (TypeError, ValueError) as e:
            raise ExchangeError("non-numeric kline field") from e
        if c.open_time_ms <= prev:
            raise ExchangeError("klines not strictly increasing")
        prev = c.open_time_ms
        out.append(c)
    return out


def fetch_binance_klines(
    fetcher: Fetcher, symbol: str, interval: str = "1h", limit: int = 100
) -> list[Candle]:
    if not symbol.isalnum() or not 1 <= limit <= 1000:
        raise ExchangeError("invalid symbol or limit")
    url = f"{BINANCE}/api/v3/klines?symbol={symbol.upper()}&interval={interval}&limit={limit}"
    res = fetcher.fetch(url, provider_id="binance_public", cost_estimate_usd=0.0)
    if res.outcome != "ok" or res.body is None:
        raise ExchangeError(f"fetch {res.outcome}: {res.reason}")
    return parse_binance_klines(res.body, res.provenance)
