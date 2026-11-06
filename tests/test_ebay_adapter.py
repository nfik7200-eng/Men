"""eBay Browse adapter against MOCKED transport + SYNTHETIC fixtures. This proves the adapter contract only;
it does NOT prove the integration works against eBay (no credentials exist). See docs/DATA_SOURCE_RESEARCH.md."""
import base64
import json
from urllib.parse import parse_qs

import httpx
import pytest

from conftest import FIX
from watchtower.collector import SourceNotConfigured, run_source
from watchtower.fetcher import Fetcher
from watchtower.models import CollectionRun, Listing, Observation, Source
from watchtower.sources import ADAPTERS

P1 = json.loads((FIX / "ebay_search_synthetic.json").read_text())
P2 = json.loads((FIX / "ebay_search_synthetic_p2.json").read_text())
HOST = "https://api.sandbox.ebay.com"


@pytest.fixture
def ebay(monkeypatch):
    monkeypatch.setenv("EBAY_CLIENT_ID", "app-id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "SUPER-SECRET-VALUE")
    monkeypatch.setenv("EBAY_ENV", "sandbox")
    monkeypatch.setenv("EBAY_MARKETPLACE", "EBAY_GB")
    monkeypatch.setenv("EBAY_QUERIES", json.dumps([{"q": "laptop", "category": "laptop"}]))
    monkeypatch.delenv("EBAY_CAMPAIGN_ID", raising=False)
    a = ADAPTERS["ebay_browse"]
    a._token, a._expires = None, 0.0
    clock = {"t": 1000.0}
    a.now = lambda: clock["t"]
    yield a, clock
    a.now = __import__("time").monotonic
    a._token = None


class Server:
    def __init__(self, token_status=200, page2_next=None):
        self.calls, self.token_status = [], token_status

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append((req.method, str(req.url), dict(req.headers), req.content.decode()))
        if req.url.path == "/identity/v1/oauth2/token":
            return httpx.Response(self.token_status, json={"access_token": "tok-%d" % len(self.calls), "expires_in": 7200})
        if req.url.params.get("offset") == "2":
            return httpx.Response(200, json=P2)
        return httpx.Response(200, json=P1)


def _fetcher(server):
    c = httpx.Client(transport=httpx.MockTransport(server))
    return Fetcher(c, min_delay=0, sleep=lambda s: None, allowed_hosts=ADAPTERS["ebay_browse"].allowed_hosts(), respect_robots=False)


def test_unconfigured_source_does_nothing_and_says_what_is_missing(db, monkeypatch):
    monkeypatch.delenv("EBAY_CLIENT_ID", raising=False)
    monkeypatch.delenv("EBAY_CLIENT_SECRET", raising=False)
    srv = Server()
    with pytest.raises(SourceNotConfigured) as e:
        run_source(db, "ebay_browse", fetcher=_fetcher(srv))
    assert "EBAY_CLIENT_ID" in str(e.value) and "EBAY_CLIENT_SECRET" in str(e.value) and srv.calls == []
    with db.session() as s:
        assert s.query(CollectionRun).count() == 0 and s.query(Listing).count() == 0  # nothing fabricated


def test_oauth_basic_auth_scope_and_token_cache(db, ebay):
    a, clock = ebay
    srv = Server()
    f = _fetcher(srv)
    run_source(db, "ebay_browse", fetcher=f, max_pages=2)
    token_calls = [c for c in srv.calls if c[0] == "POST"]
    assert len(token_calls) == 1  # cached across all page requests
    _, url, headers, body = token_calls[0]
    assert url == f"{HOST}/identity/v1/oauth2/token"
    assert headers["authorization"] == "Basic " + base64.b64encode(b"app-id:SUPER-SECRET-VALUE").decode()
    form = parse_qs(body)
    assert form["grant_type"] == ["client_credentials"] and form["scope"] == ["https://api.ebay.com/oauth/api_scope"]
    clock["t"] += 7200  # expired
    run_source(db, "ebay_browse", fetcher=f, max_pages=1)
    assert len([c for c in srv.calls if c[0] == "POST"]) == 2


def test_request_headers_marketplace_and_affiliate_context(db, ebay, monkeypatch):
    srv = Server()
    run_source(db, "ebay_browse", fetcher=_fetcher(srv), max_pages=1)
    get = [c for c in srv.calls if c[0] == "GET"][0]
    h = get[2]
    assert h["authorization"].startswith("Bearer tok-") and h["x-ebay-c-marketplace-id"] == "EBAY_GB"
    assert h["x-ebay-c-enduserctx"] == "contextualLocation=country%3DGB"  # no campaign => no affiliate params
    monkeypatch.setenv("EBAY_CAMPAIGN_ID", "5338000000")
    srv2 = Server()
    ADAPTERS["ebay_browse"]._token = None
    run_source(db, "ebay_browse", fetcher=_fetcher(srv2), max_pages=1)
    get2 = [c for c in srv2.calls if c[0] == "GET"][0]
    assert get2[2]["x-ebay-c-enduserctx"] == "affiliateCampaignId=5338000000,contextualLocation=country%3DGB"


def test_end_to_end_ingest_preserves_currency_condition_and_flags_unknowns(db, ebay):
    srv = Server()
    rid = run_source(db, "ebay_browse", fetcher=_fetcher(srv), max_pages=3)
    with db.session() as s:
        run = s.get(CollectionRun, rid)
        assert run.pages_fetched == 2 and run.status == "partial"  # page 2 had 2 bad items
        assert run.items_invalid == 2 and run.items_new == 3
        src = s.query(Source).filter_by(key="ebay_browse").one()
        assert src.kind == "sandbox" and src.last_success_at is not None
        tp = s.query(Listing).filter_by(external_id="v1|000000000001|0").one()
        assert (tp.price_minor, tp.currency, tp.original_price_minor, tp.shipping_minor) == (54999, "GBP", 69999, 1250)
        assert tp.condition == "new" and tp.location_country == "GB" and tp.identifiers == {"epid": "1234567890"}
        assert tp.brand == "Lenovo" and tp.specs["ram_gb"] == 16 and tp.specs["storage_gb"] == 512 and tp.availability == "unknown"
        assert tp.affiliate_url and "campid=" in tp.affiliate_url and tp.url.startswith("https://www.ebay.co.uk/")
        mb = s.query(Listing).filter_by(external_id="v1|000000000002|0").one()
        assert mb.price_minor is None and mb.condition == "refurbished"  # "0.00" is unavailable, never a zero price
        assert s.query(Observation).filter_by(listing_id=mb.id).one().price_minor is None
        assert s.query(Listing).filter_by(external_id="v1|000000000004|0").count() == 0  # off-site URL rejected
        assert "SUPER-SECRET-VALUE" not in json.dumps([run.error, src.last_error])


def test_next_link_to_foreign_host_is_blocked(db, ebay):
    bad = dict(P1, next="http://169.254.169.254/latest/meta-data/")

    def h(req):
        if req.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 7200})
        return httpx.Response(200, json=bad)

    rid = run_source(db, "ebay_browse", fetcher=_fetcher(h), max_pages=3)
    with db.session() as s:
        r = s.get(CollectionRun, rid)
        assert r.status == "blocked" and "169.254" in r.error and r.complete is False


def test_rejected_credentials_block_without_retry_or_leak(db, ebay):
    srv = Server(token_status=401)
    rid = run_source(db, "ebay_browse", fetcher=_fetcher(srv), max_pages=1)
    with db.session() as s:
        r = s.get(CollectionRun, rid)
        assert r.status in ("blocked", "failed") and "SUPER-SECRET-VALUE" not in (r.error or "")
    assert len([c for c in srv.calls if c[0] == "POST"]) == 1  # no retry loop on bad credentials


def test_production_flag_changes_kind_and_hosts(monkeypatch):
    a = ADAPTERS["ebay_browse"]
    monkeypatch.setenv("EBAY_ENV", "production")
    assert a.kind == "live" and a.base_url == "https://api.ebay.com" and a.allowed_hosts() == {"api.ebay.com"}
    monkeypatch.delenv("EBAY_ENV")
    assert a.kind == "sandbox"  # default is the safe one
