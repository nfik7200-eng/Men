"""Dashboard behaviour on REAL captured pages: comparison, data-class separation, provenance, stats, admin health."""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from conftest import REAL, FakeFetcher, FakeSite
from shopping import assistant
from shopping.web.app import create_app
from watchtower import config
from watchtower.collector import ensure_sources, run_source
from watchtower.models import Listing, Observation, utcnow
from watchtower.policy import ensure_policies
from watchtower.sources.sparkfun import SparkFun

ADA = {n: (REAL / f"{n}.html").read_text() for n in ("ada_5291", "ada_5812")}
SPK = {n: (REAL / f"{n}.html").read_text() for n in ("spark_zero2w", "spark_zerow")}
U = {"a5291": "https://www.adafruit.com/product/5291", "a5812": "https://www.adafruit.com/product/5812",
     "z2": "https://www.sparkfun.com/raspberry-pi-zero-2-w.html", "zw": "https://www.sparkfun.com/raspberry-pi-zero-w.html"}
TOKEN = "dashboard-test-token-0123456789"


@pytest.fixture
def world(db, monkeypatch):
    """Two real merchants (live) + demo catalogues in one DB, collected over three days."""
    monkeypatch.setattr(config, "CONTACT", "crawler@watchtower-research.net")
    monkeypatch.setattr(config, "ADMIN_TOKEN", TOKEN)
    t0 = utcnow()
    ada = FakeSite({"https://www.adafruit.com/category/105": '<a href="/product/5291" title="Raspberry Pi Zero 2 W.">a</a><a href="/product/5812" title="Raspberry Pi 5 - 4 GB RAM.">b</a>',
                    "https://www.adafruit.com/category/17": "<html></html>", "https://www.adafruit.com/category/875": "<html></html>",
                    U["a5291"]: ADA["ada_5291"], U["a5812"]: ADA["ada_5812"]})
    spk = FakeSite({SparkFun.sitemap_url: (REAL / "sparkfun_sitemap_excerpt.xml").read_text(), U["z2"]: SPK["spark_zero2w"], U["zw"]: SPK["spark_zerow"]})
    for d in (0, 2, 4):
        run_source(db, "adafruit", fetcher=ada, now_fn=lambda d=d: t0 + timedelta(days=d))
        run_source(db, "sparkfun", fetcher=spk, max_pages=200, now_fn=lambda d=d: t0 + timedelta(days=d))
    run_source(db, "webscraper_io_static", fetcher=FakeFetcher(), max_pages=1, now_fn=lambda: t0)
    return db, TestClient(create_app(db)), t0


def test_default_views_show_only_real_data_once_it_exists(world):
    db, c, _ = world
    api = lambda q: c.get("/api/v1/listings?per_page=100" + q).json()
    assert {i["source_kind"] for i in api("&source=adafruit")["items"]} == {"live"}
    html = c.get("/search").text
    assert '<span class="badge ok">Real retailer</span>' in html and "Demo catalogue —" not in html and "Demo / test data only" not in html
    demo = c.get("/search?data=demo").text
    assert "Demo catalogue" in demo and '<span class="badge ok">Real retailer</span>' not in demo
    both = c.get("/search?data=all").text
    assert "Real retailer" in both and "Demo catalogue" in both


def test_home_reports_real_merchants_and_cross_store_products(world):
    _, c, _ = world
    html = c.get("/").text
    assert "real merchants" in html and "products compared across stores" in html and "Compare merchants" in html
    assert "Demo / test data only" not in html  # real data exists, so no demo banner


def test_product_page_compares_merchants_with_provenance(world):
    db, c, _ = world
    with db.session() as s:
        pid = s.query(Listing).filter_by(sku="5291").one().product_id
        assert s.query(Listing).filter_by(sku="DEV-18713").one().product_id == pid
    html = c.get(f"/product/{pid}").text
    assert "Raspberry Pi Zero 2 W" in html and "Adafruit Industries" in html and "SparkFun Electronics" in html
    assert "$19.05" in html and "$19.00" in html and html.count('<span class="badge ok">Real retailer</span>') >= 2
    assert "No merchant listed this product as in stock" in html and "Lowest (" not in html  # Adafruit: out of stock, SparkFun: backorder
    with db.session() as s:  # now SparkFun gets stock: it becomes the lowest in-stock USD price
        s.query(Listing).filter_by(sku="DEV-18713").one().availability = "in_stock"
    html = TestClient(create_app(db)).get(f"/product/{pid}").text
    assert "Lowest (USD)" in html and "Last checked" in html and "identical normalised name" in html
    assert "do <b>not</b> convert" not in html  # both USD: no currency warning
    assert "Zero W" not in html.replace("Zero 2 W", "")  # the Zero W is a different product and is not listed here


def test_product_page_never_ranks_across_currencies(world):
    db, c, t0 = world
    with db.session() as s:
        l = s.query(Listing).filter_by(sku="DEV-18713").one()
        l.currency, l.price_minor = "GBP", 1500
        pid = l.product_id
    html = TestClient(create_app(db)).get(f"/product/{pid}").text
    assert "do <b>not</b> convert or rank across currencies" in html
    assert "£15.00" in html and "$19.05" in html  # shown side by side, unconverted


def test_listing_page_shows_provenance_stats_and_honest_deal_state(world):
    db, c, t0 = world
    with db.session() as s:
        lid = s.query(Listing).filter_by(sku="5812").one().id
    html = c.get(f"/listing/{lid}").text
    assert "Adafruit Industries" in html and "Real retailer" in html and "observed" in html
    assert "Price statistics" in html and "3 over 4.0 days" in html and "Volatility" in html
    assert "indicative" in html  # 3 collections over 4 days: shown but explicitly not evidence yet
    assert "Verified price drop" not in html and "$130.00" in html
    assert "Demo" not in html.split("Price statistics")[0].split("<h1>")[1]  # a real listing is never labelled demo


def test_deals_default_to_real_only_and_never_mix_demo_peers(world):
    _, c, _ = world
    html = c.get("/deals").text
    assert "Demo / sandbox" in html  # the selector exists
    assert "Acer" not in html and "Packard" not in html  # no demo laptops in the default (real) ranking
    assert "demo/sandbox data" in c.get("/deals?data=demo").text


def test_assistant_answers_from_real_listings_without_demo_caveat(world):
    db, _, t0 = world
    with db.session() as s:
        r = assistant.answer(s, "cheapest raspberry pi zero 2 w", now=t0 + timedelta(days=4), use_llm=False)
    assert r["picks"] and all(p.listing.source.kind == "live" for p in r["picks"])
    assert not any("DEMO" in c for c in r["caveats"])
    assert [p.listing.title for p in r["picks"]] == ["Raspberry Pi Zero 2 W"] * len(r["picks"])  # not the Pi 5: brand + model words matched
    assert {p.listing.source.name for p in r["picks"]} == {"Adafruit Industries", "SparkFun Electronics"}
    assert any("none is in stock" in c for c in r["caveats"])  # shown honestly, never hidden
    assert all(any("stock" in m or "backorder" in m for m in p.misses) for p in r["picks"])


def test_admin_shows_health_policy_and_run_metrics(world):
    _, c, _ = world
    assert c.post("/admin/login", data={"token": TOKEN}, follow_redirects=False).status_code == 303
    html = c.get("/admin").text
    assert "Collection health" in html and "approved" in html and "crawl-delay 10s" in html
    assert "not required (demo)" in html and "Unchanged" in html and "pages/min" in html or "healthy" in html
    assert "ok 100%" in html
