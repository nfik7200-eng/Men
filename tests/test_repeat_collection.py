"""Mission 005 §17D — repeated collection on a disposable FILE database (real captured pages via FakeSite: fixture-based, NOT a
live crawl). Afterwards `integrity_report` (the same checks `cli verify` runs) must pass."""
from datetime import timedelta

import re

import pytest
from sqlalchemy import func

from conftest import REAL, FakeSite
from test_real_sources import ADA, NOW, SPK, U
from watchtower import config
from watchtower.collector import ensure_sources, run_source
from watchtower.db import Database
from watchtower.integrity import integrity_report
from watchtower.models import CollectionRun, CrawlUrl, Event, Listing, Observation
from watchtower.policy import ensure_policies
from watchtower.sources.sparkfun import SparkFun


@pytest.fixture
def filedb(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONTACT", "crawler@watchtower-research.net")
    path = tmp_path / "disposable.db"
    db = Database(f"sqlite:///{path}")
    db.init()
    ensure_sources(db)
    ensure_policies(db)
    return db, path


def ada_site(**kw):
    pages = {"https://www.adafruit.com/category/105": '<a href="/product/5291" title="Raspberry Pi Zero 2 W.">a</a><a href="/product/5812" title="Raspberry Pi 5 - 4 GB RAM.">b</a>',
             "https://www.adafruit.com/category/17": "<html></html>", "https://www.adafruit.com/category/875": "<html></html>",
             U["a5291"]: ADA["ada_5291"], U["a5812"]: ADA["ada_5812"]}
    return FakeSite(pages, **kw)


def counts(db):
    with db.session() as s:
        return {"listings": s.query(Listing).count(), "obs": s.query(Observation).count(),
                "events": {k: n for k, n in s.query(Event.kind, func.count()).group_by(Event.kind)}}


def test_unchanged_repeat_collections_add_one_observation_each_and_no_false_changes(filedb):
    db, path = filedb
    for d in range(3):
        run_source(db, "adafruit", fetcher=ada_site(), now_fn=lambda d=d: NOW + timedelta(days=d))
    c = counts(db)
    assert c["listings"] == 2  # no duplicate listings
    assert c["obs"] == 6  # exactly one observation per listing per run
    assert set(c["events"]) == {"new_listing"}  # unchanged prices produce no price_drop/price_rise/restock events
    r = integrity_report(path)
    assert r["ok"], [x for x in r["checks"] if not x["ok"]]
    assert r["metrics"]["live_observation_days"] == 3 and r["metrics"]["verified_history_listings"] == 0  # 3 days is not 7+


def test_a_changed_price_appends_an_observation_and_keeps_the_old_one(filedb):
    db, path = filedb
    run_source(db, "adafruit", fetcher=ada_site(), now_fn=lambda: NOW)
    site = ada_site()
    m = re.search(r'"price":\s*"?([0-9.]+)"?', ADA["ada_5812"])
    new = f"{float(m.group(1)) + 1:.2f}"
    site.pages[U["a5812"]] = ADA["ada_5812"].replace(m.group(0), m.group(0).replace(m.group(1), new))
    run_source(db, "adafruit", fetcher=site, now_fn=lambda: NOW + timedelta(days=1))
    with db.session() as s:
        l = s.query(Listing).filter_by(sku="5812").one()
        prices = [o.price_minor for o in s.query(Observation).filter_by(listing_id=l.id).order_by(Observation.observed_at)]
        assert len(prices) == 2 and prices[1] == prices[0] + 100  # history kept, new price appended
        assert s.query(Event).filter_by(listing_id=l.id, kind="price_rise").count() == 1
        other = s.query(Listing).filter_by(sku="5291").one()
        assert s.query(Event).filter_by(listing_id=other.id).filter(Event.kind.in_(("price_rise", "price_drop"))).count() == 0
    assert integrity_report(path)["ok"]


def test_blocked_incomplete_and_failed_collections_never_remove_or_alter_listings(filedb):
    db, path = filedb
    run_source(db, "adafruit", fetcher=ada_site(), now_fn=lambda: NOW)
    before = counts(db)
    # a server block (403) in the middle of discovery/product fetch: STOP, nothing removed
    run_source(db, "adafruit", fetcher=ada_site(forbid={U["a5291"], U["a5812"]}), now_fn=lambda: NOW + timedelta(days=1))
    # robots disallows one page: that URL is recorded, the other is still read
    run_source(db, "adafruit", fetcher=ada_site(disallow={U["a5291"]}), now_fn=lambda: NOW + timedelta(days=2))
    # a network failure of both pages
    flaky = ada_site()
    flaky.pages.pop(U["a5291"]); flaky.pages.pop(U["a5812"])
    run_source(db, "adafruit", fetcher=flaky, now_fn=lambda: NOW + timedelta(days=3))
    # and one 404 (single, unconfirmed)
    run_source(db, "adafruit", fetcher=ada_site(gone={U["a5812"]}), now_fn=lambda: NOW + timedelta(days=4))
    with db.session() as s:
        assert s.query(Listing).filter_by(active=False).count() == 0, "an incomplete/blocked/failed run removed a listing"
        assert s.query(Event).filter_by(kind="removed").count() == 0
        outcomes = {c.url: c.last_outcome for c in s.query(CrawlUrl)}
        assert outcomes[U["a5291"]] in ("disallowed", "ok", "fetch_error") and outcomes[U["a5812"]] in ("gone", "ok", "fetch_error")
        statuses = [r.status for r in s.query(CollectionRun).order_by(CollectionRun.id)]
        assert "blocked" in statuses and all(r.complete is False for r in s.query(CollectionRun))
    after = counts(db)
    assert after["listings"] == before["listings"] and after["events"].get("removed", 0) == 0
    assert integrity_report(path)["ok"]


def test_cross_merchant_collection_makes_the_auto_match_and_stays_consistent(filedb):
    db, path = filedb
    run_source(db, "adafruit", fetcher=ada_site(), now_fn=lambda: NOW)
    sp = FakeSite({SparkFun.sitemap_url: (REAL / "sparkfun_sitemap_excerpt.xml").read_text(),
                   U["szero2w"]: SPK["spark_zero2w"], U["szerow"]: SPK["spark_zerow"]})
    run_source(db, "sparkfun", fetcher=sp, max_pages=10, now_fn=lambda: NOW + timedelta(hours=1))
    run_source(db, "sparkfun", fetcher=sp, max_pages=10, now_fn=lambda: NOW + timedelta(days=1))
    r = integrity_report(path)
    assert r["ok"], [x for x in r["checks"] if not x["ok"]]
    assert r["metrics"]["multi_merchant_products"] >= 1 and r["metrics"]["live_merchants"] == 2
