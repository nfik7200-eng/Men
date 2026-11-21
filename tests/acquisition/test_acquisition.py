from __future__ import annotations

import json

import pytest

from bip.acquisition.bigquery import BigQuerySandbox, BudgetExceededError
from bip.acquisition.exchanges import ExchangeError, fetch_binance_klines, parse_binance_klines
from bip.acquisition.feeds import FeedParseError, fetch_nitter_user, parse_rss
from bip.acquisition.fetcher import Fetcher, RawResponse, TokenBucket


class FakeTransport:
    def __init__(self, routes: dict[str, RawResponse]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, headers: dict[str, str], timeout: float) -> RawResponse:
        self.calls.append((url, headers))
        return self.routes.get(url, RawResponse(404, {}, b""))


def _fetcher(routes: dict[str, RawResponse]) -> tuple[Fetcher, FakeTransport, list[float]]:
    t = FakeTransport(routes)
    sleeps: list[float] = []
    now = [0.0]
    return (
        Fetcher(t, default_rate=1.0, clock=lambda: now[0], sleep=sleeps.append),
        t,
        sleeps,
    )


def test_token_bucket_waits() -> None:
    sleeps: list[float] = []
    b = TokenBucket(2.0, 1.0, clock=lambda: 0.0, sleep=sleeps.append)
    assert b.acquire() == 0.0
    assert b.acquire() == pytest.approx(0.5)


def test_robots_blocks() -> None:
    f, t, _ = _fetcher(
        {"https://x.test/robots.txt": RawResponse(200, {}, b"User-agent: *\nDisallow: /p")}
    )
    r = f.fetch("https://x.test/p/1")
    assert r.outcome == "robots_blocked"
    assert all(u.endswith("robots.txt") for u, _ in t.calls)


def test_robots_unreachable_fails_closed() -> None:
    class Boom:
        def get(self, url: str, headers: dict[str, str], timeout: float) -> RawResponse:
            raise OSError("down")

    r = Fetcher(Boom(), sleep=lambda _: None).fetch("https://x.test/a")
    assert r.outcome == "robots_blocked"


def test_conditional_get_and_provenance() -> None:
    u = "https://x.test/a"
    f, t, sleeps = _fetcher({u: RawResponse(200, {"etag": '"v1"'}, b"hello")})
    first = f.fetch(u)
    assert first.outcome == "ok"
    assert first.provenance.etag == '"v1"'
    assert first.provenance.content_sha256
    assert first.provenance.request_id
    t.routes[u] = RawResponse(304, {}, b"")
    second = f.fetch(u)
    assert second.outcome == "not_modified"
    assert t.calls[-1][1]["If-None-Match"] == '"v1"'
    assert sleeps  # rate limiting applied between same-domain requests


def test_http_error_not_data() -> None:
    f, _, _ = _fetcher({"https://x.test/a": RawResponse(500, {}, b"x")})
    r = f.fetch("https://x.test/a")
    assert r.outcome == "error"
    assert r.body is None


RSS = b"""<rss><channel><item><title>t</title><guid>1</guid><link>l</link></item></channel></rss>"""


def test_nitter_rss() -> None:
    f, _, _ = _fetcher({"https://n.test/bob/rss": RawResponse(200, {}, RSS)})
    res, items = fetch_nitter_user(f, "https://n.test/", "bob")
    assert res.outcome == "ok"
    assert items[0].guid == "1"
    assert items[0].published is None
    assert items[0].author is None


def test_rss_rejects_entities_and_garbage() -> None:
    with pytest.raises(FeedParseError):
        parse_rss(b'<!DOCTYPE x [<!ENTITY a "b">]><rss/>', "u", "r")
    with pytest.raises(FeedParseError):
        parse_rss(b"<html/>", "u", "r")


def test_binance_klines() -> None:
    rows = [[1, "1", "2", "0.5", "1.5", "10"], [2, "1", "2", "0.5", "1.5", "11"]]
    url = "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1h&limit=2"
    f, _, _ = _fetcher({url: RawResponse(200, {}, json.dumps(rows).encode())})
    c = fetch_binance_klines(f, "btcusdt", "1h", 2)
    assert [x.close for x in c] == [1.5, 1.5]
    assert c[0].provenance.provider_id == "binance_public"


def test_binance_validation() -> None:
    f, _, _ = _fetcher({})
    prov = (
        _fetcher({"https://x.test/a": RawResponse(200, {}, b"1")})[0]
        .fetch("https://x.test/a")
        .provenance
    )
    with pytest.raises(ExchangeError):
        parse_binance_klines(b'[[2,"1","1","1","1","1"],[1,"1","1","1","1","1"]]', prov)
    with pytest.raises(ExchangeError):
        parse_binance_klines(b'[[1,"x","1","1","1","1"]]', prov)
    with pytest.raises(ExchangeError):
        fetch_binance_klines(f, "BTC/USDT")


class FakeBQ:
    def __init__(self, est: int) -> None:
        self.est = est
        self.ran = 0

    def dry_run_bytes(self, sql: str) -> int:
        return self.est

    def run(self, sql: str) -> list[dict[str, int]]:
        self.ran += 1
        return [{"n": 1}]


def test_bigquery_budget_and_readonly() -> None:
    bq = FakeBQ(100)
    s = BigQuerySandbox(bq, per_query_cap_bytes=150, monthly_cap_bytes=250)
    assert s.query("SELECT 1") == [{"n": 1}]
    s.query("select 2")
    with pytest.raises(BudgetExceededError):
        s.query("select 3")  # monthly
    with pytest.raises(ValueError, match="read-only"):
        s.query("DROP TABLE x")
    big = BigQuerySandbox(FakeBQ(1000), per_query_cap_bytes=150)
    with pytest.raises(BudgetExceededError):
        big.query("select 1")
    assert bq.ran == 2
    assert s.used_bytes == 200
