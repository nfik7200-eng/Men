from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from conftest import FIX, LAPTOPS, TABLETS, FakeFetcher
from watchtower import deals
from watchtower.collector import ensure_sources, run_source
from watchtower.fetcher import FetchBlocked, FetchError, Fetcher
from watchtower.ingest import ingest_listing, validate
from watchtower.matching import match_score, model_tokens
from watchtower.models import CollectionRun, Event, Listing, MatchCandidate, Observation, Source
from watchtower.money import fmt_money, parse_money
from watchtower.sources import ADAPTERS, RawListing
from watchtower.specs import parse_specs

T0 = datetime(2026, 11, 1, 12, 0)


# ---------- money / specs / adapters ----------
def test_money_parsing_and_missing_is_not_zero():
    assert parse_money("$1,299.00") == (129900, "USD")
    assert parse_money("Â£51.77") == (5177, "GBP")
    assert parse_money("") is None and parse_money("$0.00") is None and parse_money("N/A") is None
    assert fmt_money(None, "USD") == "Price unavailable"


def test_spec_parsing():
    s = parse_specs("Asus VivoBook X441NA-GA190", '14", Celeron N3450, 4GB, 128GB SSD, Endless OS')
    assert s["ram_gb"] == 4 and s["storage_gb"] == 128 and s["cpu_family"] == "celeron" and s["storage_type"] == "ssd"
    assert parse_specs("Tab", '10.1", 16GB, Android', "tablet") == {"screen_in": 10.1, "storage_gb": 16, "os": "Android"}


def test_webscraper_adapter_on_real_fixture():
    items, nxt = ADAPTERS["webscraper_io_static"].parse_page((FIX / "wsio_laptops_p1.html").read_text(), LAPTOPS)
    assert len(items) == 6 and nxt.endswith("laptops?page=2")
    first = items[0]
    assert (first.title, first.price_minor, first.currency) == ("Packard 255 G2", 41699, "USD")
    assert first.availability == "unknown"  # card does not state stock -> not assumed
    assert first.specs["ram_gb"] == 4


def test_books_adapter_on_real_fixture():
    items, nxt = ADAPTERS["books_toscrape"].parse_page(
        (FIX / "books_p1.html").read_text(), "https://books.toscrape.com/catalogue/page-1.html")
    assert len(items) == 20 and nxt.endswith("page-2.html")
    assert items[0].title == "A Light in the Attic" and items[0].price_minor == 5177 and items[0].currency == "GBP"
    assert items[0].availability == "in_stock"


# ---------- validation / ingestion / change detection ----------
def _src(db):
    ensure_sources(db)
    return db


def _raw(**kw):
    d = dict(external_id="1", url="https://x/1", title="Acme Laptop X100", price_minor=50000, currency="USD",
             availability="in_stock", category="laptop", brand="Acme", description="15.6\", 8GB, 256GB SSD")
    d.update(kw)
    return RawListing(**d)


def test_validate_never_stores_zero_price():
    r, problems = validate(_raw(price_minor=0))
    assert r.price_minor is None and problems
    r, _ = validate(_raw(original_price_minor=10))  # original < price is implausible
    assert r.original_price_minor is None
    assert validate(_raw(title=" "))[0] is None


def test_price_drop_rise_restock_and_observations(db):
    _src(db)
    with db.session() as s:
        src = s.query(Source).first()
        l, new, _ = ingest_listing(s, src, None, _raw(availability="out_of_stock"), T0)
        assert new
        ingest_listing(s, src, None, _raw(price_minor=40000, availability="in_stock"), T0 + timedelta(days=1))
        ingest_listing(s, src, None, _raw(price_minor=44000, availability="in_stock", description="changed"),
                       T0 + timedelta(days=2))
        ingest_listing(s, src, None, _raw(price_minor=None, description="changed"), T0 + timedelta(days=3))
        s.flush()
        kinds = [e.kind for e in s.scalars(select(Event).order_by(Event.id))]
        assert kinds == ["new_listing", "price_drop", "restock", "price_rise", "description_change", "price_unavailable"]
        drop = s.scalar(select(Event).where(Event.kind == "price_drop"))
        assert drop.pct == -20.0 and (drop.old_value, drop.new_value) == ("50000", "40000")
        obs = s.scalars(select(Observation).order_by(Observation.observed_at)).all()
        assert [o.price_minor for o in obs] == [50000, 40000, 44000, None]  # every observation kept; None != 0
        assert s.get(Listing, l.id).price_minor is None


# ---------- collector end-to-end ----------
def test_collector_end_to_end_and_idempotent_listings(db):
    f = FakeFetcher()
    rid = run_source(db, "webscraper_io_static", fetcher=f, max_pages=2, now_fn=lambda: T0)
    with db.session() as s:
        r = s.get(CollectionRun, rid)
        assert r.status in ("ok", "partial")  # phones URL absent in fixtures -> partial
        assert r.items_seen == 18 and r.items_new == 18  # laptops p1+p2 (12) + tablets p1 (6)
        assert s.query(Observation).count() == 18
    run_source(db, "webscraper_io_static", fetcher=FakeFetcher(), max_pages=2, now_fn=lambda: T0 + timedelta(days=1))
    with db.session() as s:
        assert s.query(Listing).count() == 18          # no duplicates
        assert s.query(Observation).count() == 36      # history grows
        assert s.query(Source).filter_by(key="webscraper_io_static").one().last_success_at is not None


def test_removed_only_after_complete_crawl(db):
    pages = {"https://books.toscrape.com/catalogue/page-1.html": (FIX / "books_p1.html").read_text()}
    page_no_next = pages["https://books.toscrape.com/catalogue/page-1.html"].replace('<li class="next"><a href="page-2.html">next</a></li>', "")
    run_source(db, "books_toscrape", fetcher=FakeFetcher({list(pages)[0]: page_no_next}), now_fn=lambda: T0)
    # second crawl: complete (no next) but one book vanished
    gone = page_no_next.replace("A Light in the Attic", "Zzz Replacement Title").replace("a-light-in-the-attic_1000", "zzz_9999")
    run_source(db, "books_toscrape", fetcher=FakeFetcher({list(pages)[0]: gone}), now_fn=lambda: T0 + timedelta(days=1))
    with db.session() as s:
        old = s.query(Listing).filter_by(external_id="a-light-in-the-attic_1000").one()
        assert old.active is False and s.query(Event).filter_by(kind="removed").count() == 1
    # capped crawl (page-1 has a next link, max_pages=1) must NOT mark removals
    run_source(db, "books_toscrape", fetcher=FakeFetcher({list(pages)[0]: pages[list(pages)[0]]}), max_pages=1,
               now_fn=lambda: T0 + timedelta(days=2))
    with db.session() as s:
        assert s.query(Event).filter_by(kind="removed").count() == 1  # unchanged
        assert s.query(Event).filter_by(kind="reappeared").count() == 1


# ---------- fetcher politeness ----------
def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_fetcher_respects_robots_and_never_retries_403():
    calls = []

    def h(req):
        calls.append(str(req.url))
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private/")
        if req.url.path == "/blocked":
            return httpx.Response(403)
        return httpx.Response(200, text="ok")

    f = Fetcher(_client(h), min_delay=0, sleep=lambda s: None)
    assert f.get("https://shop.test/public") == "ok"
    with pytest.raises(FetchBlocked):
        f.get("https://shop.test/private/x")
    assert not any("/private/x" in c for c in calls)  # never even requested
    with pytest.raises(FetchBlocked):
        f.get("https://shop.test/blocked")
    assert calls.count("https://shop.test/blocked") == 1  # no retry/bypass on 403


def test_fetcher_retries_5xx_with_backoff_then_gives_up():
    n = {"c": 0}
    sleeps = []

    def h(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        n["c"] += 1
        return httpx.Response(503) if n["c"] < 3 else httpx.Response(200, text="fine")

    f = Fetcher(_client(h), min_delay=0, max_retries=3, sleep=sleeps.append)
    assert f.get("https://shop.test/a") == "fine" and n["c"] == 3 and sleeps

    f2 = Fetcher(_client(lambda r: httpx.Response(404) if r.url.path == "/robots.txt" else httpx.Response(500)),
                 min_delay=0, max_retries=1, sleep=lambda s: None)
    with pytest.raises(FetchError):
        f2.get("https://shop.test/b")


# ---------- matching ----------
def _l(title, source_id=1, **kw):
    d = dict(title=title, source_id=source_id, identifiers={}, specs={}, category="laptop", brand=None)
    d.update(kw)
    return Listing(**d)


def test_model_tokens_ignore_spec_noise():
    assert model_tokens("Asus VivoBook X441NA-GA190 15.6in 8GB 256GB") == {"x441na", "ga190"}


def test_identifier_match_is_deterministic():
    a = _l("Foo", identifiers={"gtin": "0123-456 789012"})
    b = _l("Completely different title", source_id=2, identifiers={"gtin": "0123456789012"})
    r = match_score(a, b)
    assert r.decision == "auto" and r.score == 1.0


def test_same_product_different_store_auto_match():
    sp = {"ram_gb": 8, "storage_gb": 256, "screen_in": 15.6}
    a = _l("Acme Aspire X441NA-GA190 15.6in", brand="Acme", specs=sp)
    b = _l("ACME Aspire X441NA GA190 laptop", source_id=2, brand="Acme", specs=dict(sp))
    assert match_score(a, b).decision == "auto"


def test_spec_or_brand_conflict_never_matches():
    a = _l("Acme Aspire X441NA-GA190", brand="Acme", specs={"ram_gb": 8, "storage_gb": 256})
    b = _l("Acme Aspire X441NA-GA190", source_id=2, brand="Acme", specs={"ram_gb": 16, "storage_gb": 256})
    assert match_score(a, b).decision == "none"
    c = _l("Acme Aspire X441NA-GA190", source_id=2, brand="Other", specs={"ram_gb": 8})
    assert match_score(a, c).decision == "none"


def test_weak_evidence_goes_to_review_not_auto(db):
    a = _l("Acme Aspire X441NA", brand="Acme", specs={"ram_gb": 8})
    b = _l("Acme Aspire X441NA Laptop Black", source_id=2, brand="Acme", specs={})
    r = match_score(a, b)
    assert r.decision == "review"


def test_cross_source_attach_and_review_queue(db):
    ensure_sources(db)
    with db.session() as s:
        s1, s2, *_ = s.query(Source).order_by(Source.id).all()
        sp = {"ram_gb": 8, "storage_gb": 256}
        l1, *_ = ingest_listing(s, s1, None, _raw(external_id="a", title="Acme Aspire X441NA-GA190", specs=sp), T0)
        l2, *_ = ingest_listing(s, s2, None, _raw(external_id="b", title="Acme Aspire X441NA GA190 15.6", specs=dict(sp),
                                                  price_minor=47000), T0)
        s.flush()
        assert l1.product_id == l2.product_id and l2.match_status == "auto" and l2.match_confidence >= 0.85
        l3, *_ = ingest_listing(s, s2, None, _raw(external_id="c", title="Acme Aspire X441NA-GA190 Laptop Black", specs={}), T0)
        s.flush()
        assert l3.product_id != l1.product_id and l3.match_status == "review"
        assert s.query(MatchCandidate).filter_by(status="pending").count() == 1


# ---------- deals ----------
def _seed_peers(s, src, n=8, base=60000):
    for i in range(n):
        ingest_listing(s, src, None, _raw(external_id=f"p{i}", title=f"Peer Model Q{i}00X", price_minor=base + i * 1000,
                                          specs={"ram_gb": 8}), T0)


def test_no_history_means_no_verified_label(db):
    ensure_sources(db)
    with db.session() as s:
        src = s.query(Source).first()
        _seed_peers(s, src)
        l, *_ = ingest_listing(s, src, None, _raw(external_id="cheap", title="Cheap Z900Y", price_minor=30000,
                                                  original_price_minor=90000, specs={"ram_gb": 8}), T0)
        ds = deals.score_listing(s, l, now=T0)
        assert ds.label == "Below typical price (estimate)" and ds.confidence == "medium"
        assert next(c for c in ds.components if c.name == "history_drop").evidence == "none"
        assert next(c for c in ds.components if c.name == "markdown").evidence == "unverified"
        assert any("history" in w for w in ds.warnings)


def test_verified_drop_needs_history(db):
    ensure_sources(db)
    with db.session() as s:
        src = s.query(Source).first()
        _seed_peers(s, src)
        for d, p in [(0, 70000), (4, 70000), (8, 69000), (12, 52000)]:
            l, *_ = ingest_listing(s, src, None, _raw(external_id="x", title="Drop D500Q", price_minor=p,
                                                      specs={"ram_gb": 8}), T0 + timedelta(days=d))
        ds = deals.score_listing(s, l, now=T0 + timedelta(days=12))
        assert ds.label == "Verified price drop" and ds.confidence == "high" and ds.n_obs == 4
        assert next(c for c in ds.components if c.name == "history_drop").evidence == "verified"


def test_suspicious_original_price_flagged(db):
    ensure_sources(db)
    with db.session() as s:
        src = s.query(Source).first()
        _seed_peers(s, src)
        for d in (0, 5, 10):
            l, *_ = ingest_listing(s, src, None, _raw(external_id="y", title="Fake F777K", price_minor=50000,
                                                      original_price_minor=99900, specs={"ram_gb": 8}), T0 + timedelta(days=d))
        ds = deals.score_listing(s, l, now=T0 + timedelta(days=10))
        assert any("Suspicious original price" in w for w in ds.warnings)


def test_missing_price_scores_none(db):
    ensure_sources(db)
    with db.session() as s:
        l, *_ = ingest_listing(s, s.query(Source).first(), None, _raw(price_minor=None), T0)
        ds = deals.score_listing(s, l)
        assert ds.score is None and ds.label == "No price"
