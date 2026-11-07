"""Security review A2/B2: the response-size cap must apply to DECODED bytes while reading (not after the whole body was
inflated into memory), robots.txt gets its own small cap, and credentials never travel over a downgraded or foreign hop."""
import gzip
import tracemalloc

import httpx
import pytest

from watchtower import fetcher as F
from watchtower.fetcher import FetchError, Fetcher

MB = 1024 * 1024


class _Wire(httpx.SyncByteStream):
    """A body that arrives in network-sized chunks and is NOT pre-read (httpx.Response(content=...) would inflate it up front)."""

    def __init__(self, data: bytes, chunk: int = 16 * 1024):
        self.data, self.chunk = data, chunk

    def __iter__(self):
        for i in range(0, len(self.data), self.chunk):
            yield self.data[i:i + self.chunk]


def _f(handler, **kw):
    return Fetcher(httpx.Client(transport=httpx.MockTransport(handler)), min_delay=0, sleep=lambda s: None, allowed_hosts={"shop.test"}, **kw)


def _page(handler_body, ctype="text/html", enc=None):
    def h(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        headers = {"content-type": ctype}
        if enc:
            headers["content-encoding"] = enc
        return httpx.Response(200, headers=headers, stream=_Wire(handler_body))
    return h


def test_a_gzip_bomb_is_rejected_without_inflating_it_in_memory(monkeypatch):
    monkeypatch.setattr(F, "MAX_BYTES", 1 * MB)
    bomb = gzip.compress(b"a" * (80 * MB))  # ~80 KB on the wire, 80 MB decoded
    f = _f(_page(bomb, enc="gzip"))
    tracemalloc.start()
    with pytest.raises(FetchError, match="larger than"):
        f.get_ex("https://shop.test/p")
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert peak < 20 * MB, peak  # the old code materialised the whole body first (hundreds of MB)


def test_a_normal_page_below_the_cap_still_works(monkeypatch):
    monkeypatch.setattr(F, "MAX_BYTES", 1 * MB)
    r = _f(_page(gzip.compress(b"<html>ok</html>" * 100), enc="gzip")).get_ex("https://shop.test/p")
    assert r.status == 200 and "ok" in r.text and r.nbytes == len(b"<html>ok</html>" * 100)


def test_robots_txt_is_capped_at_512_kib_and_the_rest_ignored():
    rules = "User-agent: *\nDisallow: /early\n" + "# pad\n" * (200 * 1024) + "Disallow: /late\n"  # > 1 MB: /late is beyond the cap

    def h(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, headers={"content-type": "text/plain"}, content=rules.encode())
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"ok")

    f = _f(h)
    assert f.allowed("https://shop.test/late") is True and f.allowed("https://shop.test/early") is False


def test_a_huge_robots_txt_does_not_exhaust_memory():
    bomb = gzip.compress(b"#" * (120 * MB))  # built BEFORE tracing: only the fetcher's own memory is measured

    def h(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, headers={"content-type": "text/plain", "content-encoding": "gzip"}, stream=_Wire(bomb))
        return httpx.Response(200, content=b"ok")

    f = _f(h)
    tracemalloc.start()
    f.allowed("https://shop.test/x")
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert peak < 20 * MB, peak


def test_credentials_are_not_sent_on_an_https_to_http_downgrade_or_to_another_host():
    seen = []

    def h(req):
        seen.append((str(req.url), req.headers.get("authorization")))
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if req.url.scheme == "https" and req.url.path == "/a":
            return httpx.Response(302, headers={"location": "http://shop.test/b"})  # downgrade
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"ok")

    f = _f(h)
    with pytest.raises(F.FetchBlocked, match="downgrade"):
        f.get_ex("https://shop.test/a", headers={"Authorization": "Bearer SECRET"})
    assert all(auth is None for url, auth in seen if url.startswith("http://")), seen

    seen.clear()

    def h2(req):
        seen.append((req.url.host, req.headers.get("authorization")))
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if req.url.host == "shop.test":
            return httpx.Response(302, headers={"location": "https://cdn.shop.test/b"})
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"ok")

    g = Fetcher(httpx.Client(transport=httpx.MockTransport(h2)), min_delay=0, sleep=lambda s: None, allowed_hosts={"shop.test"})
    g.get_ex("https://shop.test/a", headers={"Authorization": "Bearer SECRET"})
    assert ("cdn.shop.test", None) in seen and ("shop.test", "Bearer SECRET") in seen  # dropped on the host change
