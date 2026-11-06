"""Real-source engine: structured extraction, identifier hygiene, staged matching, compliance gate, incremental crawl.
Fixtures in tests/fixtures/real are REAL captured pages (see README there)."""
import json
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from conftest import REAL, FakeSite
from watchtower import config, policy
from watchtower.collector import ensure_sources, run_source
from watchtower.fetcher import FetchBlocked, Fetcher
from watchtower.identifiers import clean_mpn, normalise_gtin
from watchtower.matching import match_score, name_signature
from watchtower.models import CollectionRun, CrawlUrl, Event, Listing, Observation, Source, SourcePolicy, utcnow
from watchtower.policy import SourceNotApproved, ensure_policies, text_sha
from watchtower.sources import ADAPTERS
from watchtower.sources.adafruit import Adafruit
from watchtower.sources.sparkfun import SparkFun
from watchtower.structured import (map_availability, normalise_url, offer_from_product, parse_price, parse_sitemap)

NOW = datetime(2026, 11, 6, 12, 0)
ADA = {n: (REAL / f"{n}.html").read_text() for n in ("ada_5291", "ada_5812")}
SPK = {n: (REAL / f"{n}.html").read_text() for n in ("spark_zero2w", "spark_zerow")}
U = {"a5291": "https://www.adafruit.com/product/5291", "a5812": "https://www.adafruit.com/product/5812",
     "szero2w": "https://www.sparkfun.com/raspberry-pi-zero-2-w.html", "szerow": "https://www.sparkfun.com/raspberry-pi-zero-w.html"}


# ------------------------------------------------------------------ structured extraction
@pytest.mark.parametrize("v,dc,exp", [(19.05, False, 1905), (130, False, 13000), ("1,299.00", False, 129900), ("1.299,00", False, 129900),
                                      ("12,50", False, None), ("12,50", True, 1250), ("1,299", False, 129900), ("£12.5", False, 1250),
                                      ("USD 19.00", False, 1900), ("0", False, None), ("-5", False, None), ("1e400", False, None),
                                      ("from 5", False, None), ("N/A", False, None), (None, False, None), (True, False, None),
                                      (float("inf"), False, None), (float("nan"), False, None)])
def test_parse_price_is_strict_and_never_guesses(v, dc, exp):
    assert parse_price(v, dc) == exp


def test_normalise_url_and_availability_and_hostile_sitemap():
    assert normalise_url("HTTPS://WWW.Adafruit.com:443/product/5812/?utm_source=x&b=2&a=1#f") == "https://www.adafruit.com/product/5812/?a=1&b=2"
    assert normalise_url("http://x.com//a//b") == "http://x.com/a/b"
    assert [map_availability(x) for x in ("http://schema.org/InStock", "https://schema.org/OutOfStock", "BackOrder", "PreOrder", None, "??")] == \
        ["in_stock", "out_of_stock", "backorder", "preorder", "unknown", "unknown"]
    bomb = ('<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            '<url><loc>https://a/p/1</loc><lastmod>2026-01-01</lastmod></url><url><loc>&b;</loc></url></urlset>')
    kids, pages = parse_sitemap(bomb)
    assert [p.loc for p in pages] == ["https://a/p/1"] and pages[0].lastmod == "2026-01-01"  # entities are not expanded
    assert parse_sitemap("not xml at all") == ([], [])


def test_offer_selection_never_invents_a_price():
    base = {"@type": "Product", "name": "X"}
    one = offer_from_product({**base, "offers": {"@type": "Offer", "price": "19.05", "priceCurrency": "usd", "availability": "https://schema.org/InStock"}})
    assert (one.price_minor, one.currency, one.availability) == (1905, "USD", "in_stock")
    agg = offer_from_product({**base, "offers": {"@type": "AggregateOffer", "lowPrice": 5, "highPrice": 50, "priceCurrency": "USD"}})
    assert agg.price_minor is None and any("range" in n for n in agg.notes)
    multi = offer_from_product({**base, "offers": [{"@type": "Offer", "price": 10, "priceCurrency": "USD"}, {"@type": "Offer", "price": 20, "priceCurrency": "USD"}]})
    assert multi.price_minor is None and any("ambiguous" in n for n in multi.notes)  # never the minimum
    same = offer_from_product({**base, "offers": [{"@type": "Offer", "price": 10, "priceCurrency": "USD"}, {"@type": "Offer", "price": "10.00", "priceCurrency": "USD"}]})
    assert same.price_minor == 1000
    expired = offer_from_product({**base, "offers": {"@type": "Offer", "price": 10, "priceCurrency": "USD", "priceValidUntil": "2026-01-01"}}, now=NOW)
    assert expired.price_minor is None and any("expired" in n for n in expired.notes)
    spec = offer_from_product({**base, "offers": {"@type": "Offer", "priceSpecification": {"price": 7.5, "priceCurrency": "GBP"}}})
    assert (spec.price_minor, spec.currency) == (750, "GBP")


def test_jsonld_graph_wrapper_list_types_and_broken_json_are_handled():
    ad = Adafruit()
    page = '''<html><head><script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"Organization","name":"S"},
      {"@type":["Product","Thing"],"name":"Widget One","sku":"W1","offers":{"@type":"Offer","price":"4.50","priceCurrency":"USD",},}]}</script></head></html>'''
    r = ad.parse_product(page, "https://www.adafruit.com/product/1")
    assert (r.title, r.price_minor, r.currency, r.sku) == ("Widget One", 450, "USD", "W1")  # trailing commas repaired
    assert ad.parse_product("<html><h1>Category</h1></html>", "https://www.adafruit.com/category/105") is None
    nocur = ad.parse_product(page.replace('"priceCurrency":"USD",', ""), "https://www.adafruit.com/product/1")
    assert nocur.price_minor is None  # a price with no stated currency is not guessed into one


# ------------------------------------------------------------------ real fixtures through the adapters
def test_adafruit_adapter_on_real_pages():
    ad = Adafruit()
    z = ad.parse_product(ADA["ada_5291"], U["a5291"], NOW)
    assert (z.title, z.price_minor, z.currency, z.availability, z.sku, z.brand, z.mpn, z.gtin) == \
        ("Raspberry Pi Zero 2 W", 1905, "USD", "out_of_stock", "5291", "Raspberry Pi", None, None)
    assert z.description == "" and z.image_url is None and z.desc_hash  # no descriptions/images stored; hash for change detection
    assert z.extraction_method == "jsonld" and z.http_status == 200 and z.content_hash and z.category == "maker-electronics"
    p5 = ad.parse_product(ADA["ada_5812"], U["a5812"], NOW)
    assert (p5.price_minor, p5.availability, p5.specs["ram_gb"]) == (13000, "in_stock", 4)


def test_sparkfun_adapter_on_real_pages_sku_is_not_an_mpn():
    sp = SparkFun()
    z = sp.parse_product(SPK["spark_zero2w"], U["szero2w"], NOW)
    assert (z.title, z.price_minor, z.currency, z.availability, z.sku) == ("Raspberry Pi Zero 2 W", 1900, "USD", "out_of_stock", "DEV-18713")
    assert z.mpn is None  # sparkfun copies its SKU into "mpn"; that is not a manufacturer part number
    assert z.specs["merchant_category"].startswith("Development Boards")
    assert sp.parse_product(SPK["spark_zerow"], U["szerow"], NOW).price_minor == 1650


def test_sparkfun_discovery_is_scoped_to_overlapping_categories():
    site = FakeSite({SparkFun.sitemap_url: (REAL / "sparkfun_sitemap_excerpt.xml").read_text()})
    entries = SparkFun().discover(site)
    assert entries and all("raspberry-pi" in e.loc or "arduino" in e.loc or "micro" in e.loc or "pico" in e.loc for e in entries)
    assert all(e.loc.startswith("https://www.sparkfun.com/") and e.loc.endswith(".html") for e in entries)


def test_adafruit_discovery_reads_category_pages_only_and_tiers_by_name():
    def card(i, name):
        return f'<a href="/product/{i}" title="{name}"><img alt="{name}"></a><a href="/product/{i}?utm_source=z">dup</a>'
    cat105 = card(5291, "Raspberry Pi Zero 2 W.") + card(3082, "Aluminum Heat Sink for Raspberry Pi 3.") + '<a href="/search?q=x">s</a><a href="/api/x">a</a>'
    cat17 = card(50, "Adafruit Metro M0 Express.") + card(5812, "Raspberry Pi 5 - 4 GB RAM.")
    cat875 = card(4864, "Raspberry Pi Pico RP2040.")
    site = FakeSite({"https://www.adafruit.com/category/105": cat105, "https://www.adafruit.com/category/17": cat17,
                     "https://www.adafruit.com/category/875": cat875})
    entries = Adafruit().discover(site)
    tier = {e.loc.rsplit("/", 1)[-1]: e.priority for e in entries}
    assert tier == {"5291": 0, "5812": 0, "4864": 0, "3082": 1, "50": 2}  # deduped; search/api links ignored
    assert all(e.loc.startswith("https://www.adafruit.com/product/") for e in entries)


def test_crawl_budget_is_spent_on_core_overlapping_products_first(live_db):
    pages = {"https://www.adafruit.com/category/105": '<a href="/product/5291" title="Heat sink for Pi">h</a><a href="/product/5812" title="Raspberry Pi 5 - 4 GB RAM.">b</a>',
             "https://www.adafruit.com/category/17": "<html></html>", "https://www.adafruit.com/category/875": "<html></html>",
             U["a5291"]: ADA["ada_5291"], U["a5812"]: ADA["ada_5812"]}
    site = FakeSite(pages)
    run_source(live_db, "adafruit", fetcher=site, max_pages=1, now_fn=lambda: NOW)
    assert [u for u in site.requested if "/product/" in u] == [U["a5812"]]  # tier 0 first although 5291 has the lower id


# ------------------------------------------------------------------ identifier hygiene
def _gtin(body):
    t = sum(int(c) * (3 if i % 2 == 0 else 1) for i, c in enumerate(reversed(body)))
    return body + str((10 - t % 10) % 10)


def test_gtin_validation_rejects_bad_and_merchant_internal_codes():
    assert normalise_gtin("5056561800400") == "05056561800400"  # valid EAN-13 -> GTIN-14
    assert normalise_gtin(_gtin("01234567890")) == "00" + _gtin("01234567890")  # valid UPC-A padded
    assert normalise_gtin("5056561800401") is None and normalise_gtin("00000000") is None and normalise_gtin("12345") is None
    assert normalise_gtin(_gtin("200000000000")) is None  # EAN 2xx: restricted circulation (in-store / variable weight)
    assert normalise_gtin(_gtin("42100000526")) is None  # UPC-A 4xxxxx: in-store
    assert normalise_gtin(None) is None and normalise_gtin("abc") is None


def test_mpn_equal_to_sku_or_tiny_is_rejected():
    assert clean_mpn("DEV-18713", "DEV-18713") is None and clean_mpn("dev18713", "DEV-18713") is None
    assert clean_mpn("SC1112", "5812") == "SC1112" and clean_mpn("A1") is None and clean_mpn(None) is None


# ------------------------------------------------------------------ staged matching on real-world names
def _l(title, sid, **kw):
    d = dict(title=title, source_id=sid, identifiers={}, specs={}, category="maker-electronics", brand="Raspberry Pi", sku=None, mpn=None, gtin=None)
    d.update(kw)
    return Listing(**d)


def test_real_cross_store_pair_matches_by_brand_and_name_signature():
    a = ADAPTERS_PARSE = Adafruit().parse_product(ADA["ada_5291"], U["a5291"], NOW)
    b = SparkFun().parse_product(SPK["spark_zero2w"], U["szero2w"], NOW)
    la = _l(a.title, 1, brand=a.brand, specs=a.specs, sku=a.sku)
    lb = _l(b.title, 2, brand=b.brand, specs=b.specs, sku=b.sku)
    r = match_score(la, lb)
    assert r.decision == "auto" and "identical normalised name" in " ".join(r.reasons) and "no shared GTIN/MPN" in " ".join(r.reasons)


@pytest.mark.parametrize("a,b,expected", [
    ("Raspberry Pi Zero W", "Raspberry Pi Zero 2 W", "none"),                        # different generation
    ("Raspberry Pi 5 - 4 GB RAM", "Raspberry Pi 5 - 8 GB RAM", "none"),               # RAM variant (spec veto)
    ("Raspberry Pi 4 Model B", "Raspberry Pi 5", "none"),
    ("Raspberry Pi Pico", "Raspberry Pi Pico W", "review"),                           # one name extends the other
    ("Raspberry Pi Zero W", "Raspberry Pi Zero W with Headers", "none"),    # soldered headers = different SKU
    ("Raspberry Pi Zero 2 W", "Raspberry Pi Zero 2 W Starter Kit Bundle", "none"),     # bundle vs single
    ("Raspberry Pi Pico", "Raspberry Pi Pico (Refurbished)", "none"),                 # condition
    ("Adafruit Feather M4 Express", "Adafruit Feather M0 Express", "none"),           # different model codes
])
def test_matching_traps_for_maker_electronics(a, b, expected):
    sa = {"ram_gb": int(a.split("GB")[0].split()[-1])} if "GB" in a else {}
    sb = {"ram_gb": int(b.split("GB")[0].split()[-1])} if "GB" in b else {}
    brand = "Adafruit" if "Adafruit" in a else "Raspberry Pi"
    r = match_score(_l(a, 1, brand=brand, specs=sa), _l(b, 2, brand=brand, specs=sb))
    assert r.decision == expected and r.decision != "auto" or expected == "auto", (a, b, r)


def test_brand_aliases_do_not_block_and_different_brands_do():
    assert match_score(_l("Raspberry Pi Zero 2 W", 1, brand="Raspberry Pi Ltd"), _l("Raspberry Pi Zero 2 W", 2, brand="RASPBERRY PI")).decision == "auto"
    assert match_score(_l("Zero 2 W", 1, brand="Raspberry Pi"), _l("Zero 2 W", 2, brand="Pimoroni")).decision == "none"


def test_identifier_stages_still_check_variants_and_never_use_sku():
    g = normalise_gtin("5056561800400")
    assert match_score(_l("Pi 5", 1, gtin=g, specs={"ram_gb": 4}), _l("Pi Five board", 2, gtin=g, specs={"ram_gb": 4})).decision == "auto"
    r = match_score(_l("Pi 5", 1, gtin=g, specs={"ram_gb": 4}), _l("Pi 5", 2, gtin=g, specs={"ram_gb": 8}))
    assert r.decision == "review" and "shared GTIN" in r.reasons[0]  # same barcode but RAM differs: suspicious, human decides
    same_mpn_diff_ram = match_score(_l("Pi 5", 1, mpn="SC1112", specs={"ram_gb": 4}), _l("Pi 5", 2, mpn="SC1112", specs={"ram_gb": 8}))
    assert same_mpn_diff_ram.decision == "none"  # merchants copy MPNs across variants
    assert match_score(_l("Pi 5", 1, mpn="SC1112", specs={"ram_gb": 8}), _l("Pi 5", 2, mpn="sc-1112", specs={"ram_gb": 8})).decision == "auto"
    assert match_score(_l("Foo", 1, sku="X-1"), _l("Totally different thing", 2, sku="X-1")).decision == "none"  # SKU never matches


def test_name_signature_glues_units_and_drops_brand_and_filler():
    assert name_signature("Raspberry Pi 5 - 4 GB RAM", "Raspberry Pi") == name_signature("RASPBERRY PI 5 4GB", "Raspberry Pi") == frozenset({"5"})


# ------------------------------------------------------------------ compliance gate
@pytest.fixture
def live_db(db, monkeypatch):
    monkeypatch.setattr(config, "CONTACT", "crawler@watchtower-research.net")
    ensure_sources(db)
    ensure_policies(db)
    return db


def test_live_source_without_approved_policy_never_runs(db, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONTACT", "crawler@watchtower-research.net")
    monkeypatch.setattr(policy, "POLICY_FILE", tmp_path / "none.json")  # no reviewed-policy file: only the DB row counts
    ensure_sources(db)
    with db.session() as s:
        s.add(SourcePolicy(source_id=s.query(Source).filter_by(key="adafruit").one().id, status="unverified"))
    site = FakeSite()
    with pytest.raises(SourceNotApproved, match="unverified"):
        run_source(db, "adafruit", fetcher=site)
    assert site.requested == []  # nothing fetched


def test_approved_policy_is_loaded_from_versioned_data_and_needs_a_real_contact(live_db, monkeypatch):
    with live_db.session() as s:
        pols = {p.source.key: p for p in s.scalars(select(SourcePolicy))}
        assert pols["adafruit"].status == "approved" and pols["sparkfun"].status == "approved"
        assert "absence of a prohibition, not a permission" in pols["adafruit"].tos_finding and "Privacy & Legal" in pols["adafruit"].tos_finding
        assert pols["sparkfun"].crawl_delay_s == 10 and pols["adafruit"].review_due_at > utcnow() - timedelta(days=1)
    monkeypatch.setattr(config, "CONTACT", "set-WATCHTOWER_CONTACT")
    with pytest.raises(SourceNotApproved, match="CONTACT"):
        run_source(live_db, "adafruit", fetcher=FakeSite())


def test_expired_review_and_robots_disallow_stop_the_source(live_db):
    with pytest.raises(SourceNotApproved, match="review expired"):  # reviews are valid 90 days, then a human must re-review
        run_source(live_db, "adafruit", fetcher=FakeSite(), now_fn=lambda: utcnow() + timedelta(days=100))
    site = FakeSite(disallow={"https://www.adafruit.com/product/5812"})
    with pytest.raises(SourceNotApproved, match="now disallows"):
        run_source(live_db, "adafruit", fetcher=site)
    with live_db.session() as s:  # auto-downgraded and persisted as a NEWER decision than the versioned file
        p = s.scalar(select(SourcePolicy).join(Source).where(Source.key == "adafruit"))
        assert p.status == "rejected" and "AUTO-REJECTED" in p.notes and p.reviewed_by == "automatic robots.txt re-check"
    with pytest.raises(SourceNotApproved, match="rejected"):  # the file must NOT silently re-approve it
        run_source(live_db, "adafruit", fetcher=FakeSite())


def test_demo_and_sandbox_sources_skip_the_gate(db):
    from conftest import FakeFetcher
    rid = run_source(db, "books_toscrape", fetcher=FakeFetcher(), max_pages=1, now_fn=utcnow)
    with db.session() as s:
        assert s.get(CollectionRun, rid).status in ("ok", "partial")


def test_crawl_delay_from_robots_is_honoured_by_the_fetcher():
    robots = (REAL / "www.sparkfun.com.robots.txt").read_text()
    sleeps = []

    def h(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text=robots)
        return httpx.Response(200, text="<html>ok</html>", headers={"content-type": "text/html"})

    f = Fetcher(httpx.Client(transport=httpx.MockTransport(h)), min_delay=0, sleep=sleeps.append, allowed_hosts={"www.sparkfun.com"})
    f.get("https://www.sparkfun.com/a.html")
    f.get("https://www.sparkfun.com/b.html")
    assert sleeps and max(sleeps) > 9.0, sleeps  # second request waited ~10 s (Crawl-delay: 10)
    sha, delay = f.robots_info("https://www.sparkfun.com/")
    assert delay == 10.0 and len(sha) == 64
    assert f.allowed("https://www.sparkfun.com/raspberry-pi-zero-2-w.html") and not f.allowed("https://www.sparkfun.com/checkout")
    assert not f.allowed("https://www.sparkfun.com/search?q=pi")


# ------------------------------------------------------------------ incremental crawl, 304, removals
def _site(**kw):
    pages = {"https://www.adafruit.com/category/105": '<a href="/product/5291">a</a><a href="/product/5812">b</a>',
             "https://www.adafruit.com/category/17": "<html></html>", "https://www.adafruit.com/category/875": "<html></html>",
             U["a5291"]: ADA["ada_5291"], U["a5812"]: ADA["ada_5812"]}
    return FakeSite(pages, **kw)


def test_product_crawl_collects_real_pages_with_provenance(live_db):
    site = _site()
    rid = run_source(live_db, "adafruit", fetcher=site, now_fn=lambda: NOW)
    with live_db.session() as s:
        r = s.get(CollectionRun, rid)
        assert (r.status, r.complete, r.items_seen, r.items_new, r.pages_fetched) == ("ok", False, 2, 2, 2)
        assert r.extraction["jsonld"] == 2 and r.duration_s is not None and r.bytes_fetched > 1000
        l = s.query(Listing).filter_by(sku="5812").one()
        o = s.query(Observation).filter_by(listing_id=l.id).one()
        assert (l.price_minor, l.currency, l.availability, l.source.kind) == (13000, "USD", "in_stock", "live")
        assert (o.extraction_method, o.http_status, o.observed_at, len(o.content_hash)) == ("jsonld", 200, NOW, 40)
        assert l.description == "" and l.image_url is None
        assert s.query(CrawlUrl).filter(CrawlUrl.listing_id.is_not(None)).count() == 2


def test_repeat_crawl_uses_304_and_records_reconfirmed_observations_without_duplicates(live_db):
    site = _site()
    run_source(live_db, "adafruit", fetcher=site, now_fn=lambda: NOW)
    rid2 = run_source(live_db, "adafruit", fetcher=site, now_fn=lambda: NOW + timedelta(days=2))
    with live_db.session() as s:
        r2 = s.get(CollectionRun, rid2)
        assert site.conditional_hits == 2 and r2.pages_unchanged == 2 and r2.extraction["not_modified"] == 2
        assert s.query(Listing).count() == 2  # no duplicate listings
        for l in s.query(Listing):
            obs = s.scalars(select(Observation).where(Observation.listing_id == l.id).order_by(Observation.observed_at)).all()
            assert [o.extraction_method for o in obs] == ["jsonld", "not_modified"] and obs[1].price_minor == obs[0].price_minor
            assert l.last_seen_at == NOW + timedelta(days=2) and l.active
        assert s.query(Event).filter(Event.kind.in_(("price_drop", "price_rise", "removed"))).count() == 0


def test_not_due_urls_are_not_refetched_and_price_changes_are_detected(live_db):
    site = _site()
    run_source(live_db, "adafruit", fetcher=site, now_fn=lambda: NOW)
    n = len(site.requested)
    rid = run_source(live_db, "adafruit", fetcher=site, now_fn=lambda: NOW + timedelta(hours=1))  # within check interval
    assert len([u for u in site.requested[n:] if "/product/" in u]) == 0
    with live_db.session() as s:
        assert s.get(CollectionRun, rid).pages_fetched == 0 and s.get(CollectionRun, rid).status == "ok"
    import re
    cheaper = re.sub(r'"price"\s*:\s*130\b', '"price":119.5', ADA["ada_5812"]).replace('content="130.00"', 'content="119.50"')
    assert cheaper != ADA["ada_5812"]
    site.pages[U["a5812"]] = cheaper
    rid3 = run_source(live_db, "adafruit", fetcher=site, now_fn=lambda: NOW + timedelta(days=2))
    with live_db.session() as s:
        ev = s.query(Event).filter_by(kind="price_drop").one()
        assert (ev.old_value, ev.new_value) == ("13000", "11950") and ev.pct < 0
        assert s.get(CollectionRun, rid3).price_changes == 1


def test_sampling_never_infers_removal_but_404_on_the_listing_url_does(live_db):
    site = _site()
    run_source(live_db, "adafruit", fetcher=site, now_fn=lambda: NOW)
    site.pages["https://www.adafruit.com/category/105"] = '<a href="/product/5812">b</a>'  # 5291 vanished from the index
    run_source(live_db, "adafruit", fetcher=site, now_fn=lambda: NOW + timedelta(days=2))
    with live_db.session() as s:
        assert s.query(Listing).filter_by(active=False).count() == 0  # absence from an index is not evidence of removal
    site.gone = {U["a5291"]}
    site.pages["https://www.adafruit.com/category/105"] = '<a href="/product/5291">a</a><a href="/product/5812">b</a>'
    site.pages[U["a5291"]] = ADA["ada_5291"] + " "  # changed body so the conditional request is not a 304
    run_source(live_db, "adafruit", fetcher=site, now_fn=lambda: NOW + timedelta(days=4))
    with live_db.session() as s:  # ONE 404 is not enough (could be a transient CDN/maintenance error)
        first = s.query(Listing).filter_by(sku="5291").one()
        assert first.active and s.query(Event).filter_by(kind="removed", listing_id=first.id).count() == 0
        assert s.query(CrawlUrl).filter_by(gone=True).count() == 0
    run_source(live_db, "adafruit", fetcher=site, now_fn=lambda: NOW + timedelta(days=4, hours=3))  # a second, later 404 confirms
    with live_db.session() as s:
        gone = s.query(Listing).filter_by(sku="5291").one()
        assert gone.active is False and s.query(Event).filter_by(kind="removed", listing_id=gone.id).count() == 1
        assert s.query(CrawlUrl).filter_by(gone=True).count() == 1
        assert s.query(Listing).filter_by(sku="5812").one().active


def test_budget_blocked_failed_and_non_product_pages(live_db):
    site = _site()
    rid = run_source(live_db, "adafruit", fetcher=site, max_pages=1, now_fn=lambda: NOW)
    with live_db.session() as s:
        assert s.get(CollectionRun, rid).pages_fetched == 1 and s.query(Listing).count() == 1  # budget respected
    blocked = _site(forbid={U["a5812"], U["a5291"]})
    rid = run_source(live_db, "adafruit", fetcher=blocked, now_fn=lambda: NOW + timedelta(days=3))
    with live_db.session() as s:
        r = s.get(CollectionRun, rid)
        assert r.status == "blocked" and "403" in r.error and r.complete is False
    junk = _site()
    junk.pages[U["a5291"]] = "<html><h1>Please complete the CAPTCHA</h1></html>"
    junk.pages[U["a5812"]] = ADA["ada_5812"]
    rid = run_source(live_db, "adafruit", fetcher=junk, now_fn=lambda: NOW + timedelta(days=6))
    with live_db.session() as s:
        r = s.get(CollectionRun, rid)
        assert r.extraction["no_product"] >= 1 and s.query(Listing).filter_by(active=False).count() == 0
        c = s.query(CrawlUrl).filter_by(url=U["a5291"]).one()
        assert c.next_check_at > NOW + timedelta(days=20)  # non-product pages are remembered, not hammered


def test_cross_store_match_is_made_by_the_collector_on_real_pages(live_db):
    run_source(live_db, "adafruit", fetcher=_site(), now_fn=lambda: NOW)
    sp = FakeSite({SparkFun.sitemap_url: (REAL / "sparkfun_sitemap_excerpt.xml").read_text(),
                   U["szero2w"]: SPK["spark_zero2w"], U["szerow"]: SPK["spark_zerow"]})
    run_source(live_db, "sparkfun", fetcher=sp, max_pages=200, now_fn=lambda: NOW)
    with live_db.session() as s:
        z2 = s.query(Listing).filter_by(sku="DEV-18713").one()
        a2 = s.query(Listing).filter_by(sku="5291").one()
        assert z2.product_id == a2.product_id and z2.match_status == "auto" and z2.match_confidence >= 0.9
        zw = s.query(Listing).filter_by(sku="DEV-14277").one()
        assert zw.product_id != a2.product_id  # Zero W is NOT the Zero 2 W


def test_crawl_budget_favours_rechecks_of_known_listings_over_discovery(live_db):
    pages = {"https://www.adafruit.com/category/105": '<a href="/product/5291" title="Raspberry Pi Zero 2 W.">a</a><a href="/product/5812" title="Raspberry Pi 5 - 4 GB RAM.">b</a>',
             "https://www.adafruit.com/category/17": "<html></html>", "https://www.adafruit.com/category/875": "<html></html>",
             U["a5291"]: ADA["ada_5291"], U["a5812"]: ADA["ada_5812"]}
    site = FakeSite(pages)
    run_source(live_db, "adafruit", fetcher=site, max_pages=1, now_fn=lambda: NOW)        # collects the tier-0 page only
    with live_db.session() as s:
        first = s.query(CrawlUrl).filter(CrawlUrl.last_fetched_at.is_not(None)).one()
        first_url = first.url
    n = len(site.requested)
    run_source(live_db, "adafruit", fetcher=site, max_pages=2, now_fn=lambda: NOW + timedelta(days=2))  # first is due again AND one new page exists
    fetched = [u for u in site.requested[n:] if "/product/" in u]
    assert set(fetched) == {U["a5291"], U["a5812"]} and fetched[0] == first_url  # re-check first, then discovery
    with live_db.session() as s:
        assert s.query(Listing).count() == 2  # no duplicates


def test_sparkfun_stock_is_read_from_the_main_product_never_a_related_card():
    """Real defect: the first div.stock on a SparkFun page is a RELATED product's badge. The captured fixture keeps that
    layout (related card first, main product second)."""
    sp = SparkFun()
    assert "stock-info-catalog" in SPK["spark_zero2w"] and SPK["spark_zero2w"].index("stock-info-catalog") < SPK["spark_zero2w"].index("product-info-main")
    assert sp.parse_product(SPK["spark_zero2w"], U["szero2w"], NOW).availability == "out_of_stock"
    page = SPK["spark_zero2w"].replace("Out of stock", "In stock")  # main product flips; the related card is unchanged
    assert sp.parse_product(page, U["szero2w"], NOW).availability == "in_stock"
    no_main = SPK["spark_zero2w"].replace('<div class="product-info-main">', '<div class="x">')
    assert sp.parse_product(no_main, U["szero2w"], NOW).availability == "unknown"  # no main-product badge: unknown, not a neighbour's
