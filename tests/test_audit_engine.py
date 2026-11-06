"""Regression tests for the independent audit (docs/AUDIT_REPORT.md): engine-side findings."""
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import event, select

from conftest import FIX, FakeFetcher
from watchtower import deals, monitor
from watchtower.collector import SourceBusy, SourceDisabled, run_source, ensure_sources
from watchtower.fetcher import FetchBlocked, Fetcher
from watchtower.ingest import ingest_listing
from watchtower.matching import match_score
from watchtower.models import CollectionRun, Event, Listing, Observation, Source
from watchtower.sources import ADAPTERS, RawListing
from watchtower.specs import parse_specs

T0 = datetime(2026, 11, 1, 12, 0)
BOOKS = "https://books.toscrape.com/catalogue/page-1.html"


def _books_page(next_link=False):
    html = (FIX / "books_p1.html").read_text()
    return html if next_link else html.replace('<li class="next"><a href="page-2.html">next</a></li>', "")


def _raw(**kw):
    d = dict(external_id="1", url="https://x/1", title="Acme Laptop X100", price_minor=50000, currency="USD",
             availability="in_stock", category="laptop", brand="Acme", description="15.6\", 8GB, 256GB SSD")
    d.update(kw)
    return RawListing(**d)


# ---------- F1: empty/soft-blocked page must not wipe a category ----------
def test_f1_zero_item_page_makes_run_incomplete_and_removes_nothing(db):
    run_source(db, "books_toscrape", fetcher=FakeFetcher({BOOKS: _books_page()}), now_fn=lambda: T0)
    captcha = "<html><body><h1>Please complete the CAPTCHA</h1></body></html>"
    rid = run_source(db, "books_toscrape", fetcher=FakeFetcher({BOOKS: captcha}), now_fn=lambda: T0 + timedelta(days=1))
    with db.session() as s:
        r = s.get(CollectionRun, rid)
        assert r.complete is False and r.status in ("failed", "partial")
        assert s.query(Listing).filter_by(active=False).count() == 0
        assert s.query(Event).filter_by(kind="removed").count() == 0


def test_f1_mass_removal_guard(db):
    run_source(db, "books_toscrape", fetcher=FakeFetcher({BOOKS: _books_page()}), now_fn=lambda: T0)
    # a "complete" crawl that suddenly shows only 2 of 20 books must not delete the other 18
    html = _books_page()
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    for art in soup.select("article.product_pod")[2:]:
        art.decompose()
    rid = run_source(db, "books_toscrape", fetcher=FakeFetcher({BOOKS: str(soup)}), now_fn=lambda: T0 + timedelta(days=1))
    with db.session() as s:
        assert s.query(Listing).filter_by(active=False).count() == 0
        assert s.get(CollectionRun, rid).status == "partial" and "removal guard" in (s.get(CollectionRun, rid).error or "")


# ---------- F3: one observation per (listing, run) ----------
def test_f3_duplicate_listing_in_one_run_yields_one_observation(db):
    ensure_sources(db)
    with db.session() as s:
        src = s.query(Source).first()
        run = CollectionRun(source_id=src.id, started_at=T0)
        s.add(run); s.flush()
        for _ in range(3):
            ingest_listing(s, src, run, _raw(), T0)
        assert s.query(Observation).count() == 1


# ---------- F10 / F11 / F22: run lifecycle ----------
def test_f10_unexpected_exception_marks_run_failed(db, monkeypatch):
    ad = ADAPTERS["books_toscrape"]
    orig = ad.parse_page
    calls = {"n": 0}

    def boom(html, url):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("layout changed")
        return orig(html, url)

    monkeypatch.setattr(ad, "parse_page", boom)
    pages = {BOOKS: _books_page(next_link=True), "https://books.toscrape.com/catalogue/page-2.html": _books_page()}
    with pytest.raises(RuntimeError):
        run_source(db, "books_toscrape", fetcher=FakeFetcher(pages), max_pages=5, now_fn=lambda: T0)
    with db.session() as s:
        r = s.query(CollectionRun).one()
        assert r.status == "failed" and r.finished_at is not None and "layout changed" in r.error
        assert s.query(Source).filter_by(key="books_toscrape").one().last_status == "failed"


def test_f11_disabled_source_is_not_collected(db):
    ensure_sources(db)
    with db.session() as s:
        s.query(Source).filter_by(key="books_toscrape").one().enabled = False
    f = FakeFetcher({BOOKS: _books_page()})
    with pytest.raises(SourceDisabled):
        run_source(db, "books_toscrape", fetcher=f)
    assert f.requested == []


def test_f22_concurrent_run_refused_but_abandoned_run_recovered(db):
    ensure_sources(db)
    from watchtower.models import utcnow
    with db.session() as s:
        src = s.query(Source).filter_by(key="books_toscrape").one()
        s.add(CollectionRun(source_id=src.id, started_at=utcnow(), status="running"))
    with pytest.raises(SourceBusy):
        run_source(db, "books_toscrape", fetcher=FakeFetcher({BOOKS: _books_page()}))
    with db.session() as s:  # a run "running" for 3 hours is abandoned, not a lock
        r = s.query(CollectionRun).one()
        r.started_at = utcnow() - timedelta(hours=3)
    run_source(db, "books_toscrape", fetcher=FakeFetcher({BOOKS: _books_page()}))
    with db.session() as s:
        assert sorted(r.status for r in s.query(CollectionRun)) == ["failed", "ok"]


# ---------- F9: SSRF / redirect safety ----------
def _mock(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_f9_redirect_to_internal_host_is_blocked_without_request():
    hits = []

    def h(req):
        hits.append(str(req.url))
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})

    f = Fetcher(_mock(h), min_delay=0, sleep=lambda s: None, allowed_hosts={"shop.test"})
    with pytest.raises(FetchBlocked):
        f.get("https://shop.test/a")
    assert not any("169.254" in u for u in hits)


def test_f9_off_host_and_private_ip_urls_blocked():
    f = Fetcher(_mock(lambda r: httpx.Response(404)), min_delay=0, sleep=lambda s: None, allowed_hosts={"shop.test"})
    for bad in ("https://evil.test/x", "http://127.0.0.1:6379/", "http://[::1]/", "file:///etc/passwd", "ftp://shop.test/x"):
        with pytest.raises(FetchBlocked):
            f.get(bad)
    g = Fetcher(_mock(lambda r: httpx.Response(404)), min_delay=0, sleep=lambda s: None)  # no allowlist: still no private IPs
    with pytest.raises(FetchBlocked):
        g.get("http://10.0.0.5/admin")


def test_f9_same_host_redirect_is_followed_and_robots_applies_per_hop():
    def h(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private/")
        if req.url.path == "/a":
            return httpx.Response(302, headers={"location": "/private/x"})
        return httpx.Response(200, text="secret")

    f = Fetcher(_mock(h), min_delay=0, sleep=lambda s: None, allowed_hosts={"shop.test"})
    with pytest.raises(FetchBlocked):
        f.get("https://shop.test/a")


# ---------- F12: currency changes ----------
def test_f12_currency_change_event_and_watch_not_compared_across_currencies(db):
    ensure_sources(db)
    with db.session() as s:
        src = s.query(Source).first()
        l, *_ = ingest_listing(s, src, None, _raw(price_minor=70000), T0)
        monitor.add_watch(s, l.id, target_price_minor=60000)
        ingest_listing(s, src, None, _raw(price_minor=55000, currency="GBP"), T0 + timedelta(days=1))
        s.flush()
        assert s.query(Event).filter_by(kind="currency_change").count() == 1
        st = monitor.evaluate(s)[0]
        assert not st.triggered and any("currency" in r.lower() for r in st.reasons)


# ---------- F21: spec parsing order ----------
@pytest.mark.parametrize("desc,ram,sto", [("128GB SSD 4GB RAM", 4, 128), ("4GB RAM, 128GB SSD", 4, 128),
                                          ("8GB DDR4, 256GB SSD", 8, 256), ("16GB memory 1TB NVMe", 16, 1024)])
def test_f21_spec_parser_uses_labels_not_position(desc, ram, sto):
    s = parse_specs("X", desc)
    assert (s.get("ram_gb"), s.get("storage_gb")) == (ram, sto)


# ---------- F4 / F5: product matching must not falsely merge ----------
def _l(title, sid=1, **kw):
    d = dict(title=title, source_id=sid, identifiers={}, specs={"ram_gb": 8, "storage_gb": 128}, category="phone", brand="Samsung")
    d.update(kw)
    return Listing(**d)


NOT_AUTO = [
    ("Samsung Galaxy S21 5G", "Samsung Galaxy S21 Ultra 5G"),
    ("Samsung Galaxy S21+ 5G", "Samsung Galaxy S21 5G"),
    ("Samsung Galaxy S21 5G", "Samsung Galaxy S21 5G + Wireless Charger Bundle"),
    ("Samsung Galaxy S21 5G", "Samsung Galaxy S21 5G Refurbished"),
    ("Samsung Galaxy S21 5G Phantom Black", "Samsung Galaxy S21 5G Phantom White"),
    ("Samsung Galaxy S21 5G", "Samsung Galaxy S21 FE 5G"),
    ("Samsung Galaxy S21 5G", "Samsung Galaxy S21 5G Pack of 2"),
    ("Samsung Galaxy S21 5G", "Samsung Galaxy S21 5G (Used - Good)"),
]


@pytest.mark.parametrize("a,b", NOT_AUTO)
def test_f4_variants_never_auto_merge(a, b):
    assert match_score(_l(a), _l(b, 2)).decision != "auto", (a, b)


def test_f4_region_suffix_goes_to_review_not_auto():
    a = _l("Acer Aspire A315-51-31GK", category="laptop", brand="Acer")
    b = _l("Acer Aspire A315-51-31GK-US", 2, category="laptop", brand="Acer")
    assert match_score(a, b).decision != "auto"


def test_f4_true_same_product_still_matches():
    a = _l("Samsung Galaxy S21 5G 128GB", brand="Samsung")
    b = _l("SAMSUNG Galaxy S21 5G (128 GB)", 2, brand="Samsung")
    assert match_score(a, b).decision == "auto"
    c = _l("Acme Aspire X441NA-GA190", category="laptop", brand="Acme")
    d = _l("ACME Aspire X441NA GA190 laptop", 2, category="laptop", brand="Acme")
    assert match_score(c, d).decision == "auto"


def test_f5_mpn_collision_across_categories_or_without_brands_is_not_auto():
    a = _l("Thing", category="laptop", brand=None, identifiers={"mpn": "A1"})
    b = _l("Other", 2, category="phone", brand=None, identifiers={"mpn": "A-1"})
    assert match_score(a, b).decision != "auto"
    c = _l("Thing", category="laptop", brand="Acme", identifiers={"mpn": "AB-12345"})
    d = _l("Thing2", 2, category="laptop", brand="Acme", identifiers={"mpn": "AB-12345"})
    assert match_score(c, d).decision == "auto"  # a sufficiently long MPN with same brand is deterministic


# ---------- deals: F2 / F3 / F13 / F14 / F15 ----------
def _src(db):
    ensure_sources(db)
    return db


def _peers(s, src, when):
    for i in range(8):
        ingest_listing(s, src, None, _raw(external_id=f"p{i}", title=f"Peer Model Q{i}00X", price_minor=60000 + i * 1000,
                                          specs={"ram_gb": 8}), when)


def test_f2_price_spike_then_return_to_normal_is_not_a_verified_drop(db):
    _src(db)
    with db.session() as s:
        src = s.query(Source).first()
        _peers(s, src, T0 + timedelta(days=8))
        for d, p in [(0, 10000), (1, 20000), (8, 10000)]:
            l, *_ = ingest_listing(s, src, None, _raw(external_id="x", title="Spiky S900Q", price_minor=p, specs={"ram_gb": 8}),
                                   T0 + timedelta(days=d))
        ds = deals.score_listing(s, l, now=T0 + timedelta(days=8))
        assert ds.label != "Verified price drop"
        assert next(c for c in ds.components if c.name == "history_drop").points == 0


def test_f3_burst_observations_do_not_count_as_history(db):
    _src(db)
    with db.session() as s:
        src = s.query(Source).first()
        _peers(s, src, T0 + timedelta(days=7))
        for sec, p in [(0, 70000), (5, 70000), (7 * 86400, 40000)]:
            l, *_ = ingest_listing(s, src, None, _raw(external_id="b", title="Burst B700Z", price_minor=p, specs={"ram_gb": 8}),
                                   T0 + timedelta(seconds=sec))
        ds = deals.score_listing(s, l, now=T0 + timedelta(days=7))
        assert ds.label != "Verified price drop" and ds.n_obs == 2  # 3 rows, but 2 distinct collections


def test_genuine_drop_after_stable_history_is_verified(db):
    _src(db)
    with db.session() as s:
        src = s.query(Source).first()
        _peers(s, src, T0 + timedelta(days=12))
        for d, p in [(0, 70000), (4, 70000), (8, 69000), (12, 52000)]:
            l, *_ = ingest_listing(s, src, None, _raw(external_id="g", title="Drop D500Q", price_minor=p, specs={"ram_gb": 8}),
                                   T0 + timedelta(days=d))
        ds = deals.score_listing(s, l, now=T0 + timedelta(days=12))
        assert ds.label == "Verified price drop" and ds.confidence == "high"


def test_f14_original_price_seen_once_is_not_verified_markdown(db):
    _src(db)
    with db.session() as s:
        src = s.query(Source).first()
        _peers(s, src, T0 + timedelta(days=10))
        for d, p in [(0, 10000), (3, 10000), (5, 30000), (7, 10000), (10, 10000)]:
            l, *_ = ingest_listing(s, src, None, _raw(external_id="m", title="Mark M333K", price_minor=p, original_price_minor=30000 if d == 10 else None,
                                                      specs={"ram_gb": 8}), T0 + timedelta(days=d))
        ds = deals.score_listing(s, l, now=T0 + timedelta(days=10))
        md = next(c for c in ds.components if c.name == "markdown")
        assert md.evidence != "verified" and md.points == 0


def test_f13_stale_listing_is_not_verified_and_excluded_from_top_deals(db):
    _src(db)
    with db.session() as s:
        src = s.query(Source).first()
        _peers(s, src, T0)
        for d, p in [(0, 70000), (4, 70000), (8, 69000), (12, 52000)]:
            l, *_ = ingest_listing(s, src, None, _raw(external_id="s", title="Old O500Q", price_minor=p, specs={"ram_gb": 8}),
                                   T0 + timedelta(days=d))
        later = T0 + timedelta(days=400)
        ds = deals.score_listing(s, l, now=later)
        assert ds.label == "Stale data" and ds.confidence == "low"
        assert all(x[0].id != l.id for x in deals.top_deals(s, 20, now=later))


def test_f15_top_deals_query_count_is_bounded(db):
    _src(db)
    with db.session() as s:
        src = s.query(Source).first()
        for i in range(120):
            ingest_listing(s, src, None, _raw(external_id=f"n{i}", title=f"Bulk Model Z{i:03d}K", price_minor=50000 + i * 100,
                                              specs={"ram_gb": 8}), T0)
        s.flush()
        n = {"c": 0}
        eng = s.get_bind()
        ev = lambda *a, **k: n.__setitem__("c", n["c"] + 1)
        event.listen(eng, "before_cursor_execute", ev)
        try:
            deals.top_deals(s, 10, now=T0)
        finally:
            event.remove(eng, "before_cursor_execute", ev)
        assert n["c"] <= 12, n["c"]


def test_f15_peer_median_without_self_matches_brute_force_and_scales():
    import random
    from statistics import median
    random.seed(7)
    for _ in range(500):
        vals = sorted(random.choices(range(1, 30), k=random.randint(1, 25)))
        own = random.choice(vals + [999])
        n, m = deals.PeerContext._median_without(vals, own)
        rest = list(vals)
        if own in rest:
            rest.remove(own)
        assert n == len(rest) and (m == median(rest) if rest else m is None)


def test_f15_top_deals_is_fast_on_a_large_catalogue(db):
    import time
    _src(db)
    with db.session() as s:
        src = s.query(Source).first()
        for i in range(2000):
            ingest_listing(s, src, None, _raw(external_id=f"f{i}", title=f"Fast Model Y{i:04d}K", price_minor=40000 + (i % 300) * 100,
                                              specs={"ram_gb": [4, 8, 16][i % 3]}), T0)
        s.flush()
        t = time.time()
        deals.top_deals(s, 10, now=T0)
        assert time.time() - t < 2.0  # was ~1.3s per 1000 listings before (quadratic); now linear
