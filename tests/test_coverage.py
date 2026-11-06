"""Mission 005: coverage intelligence. A discovered URL is NOT an acquired product; a fetched page is NOT an extracted listing."""
from datetime import datetime, timedelta

from test_acquisition import NOW, _ada_listing, _spark_rows
from test_real_sources import live_db  # noqa: F401
from watchtower import coverage
from watchtower.collector import ensure_sources
from watchtower.models import CollectionRun, CrawlUrl, Source


def _world(s):
    """SparkFun with 10 discovered URLs in every state we must tell apart; Adafruit has one matching listing."""
    _ada_listing(s, "Raspberry Pi Zero 2 W", "5291")
    _ada_listing(s, "Raspberry Pi 5 - 4 GB RAM", "5812")
    src, rows = _spark_rows(s, [f"item-{i}" for i in range(10)])
    st = lambda i, **kw: [setattr(rows[i], k, v) for k, v in kw.items()]
    st(0, last_fetched_at=NOW, last_outcome="ok", listing_id=1)                     # acquired
    st(1, last_fetched_at=NOW, last_outcome="not_modified", listing_id=2)           # acquired (re-confirmed)
    st(2, last_fetched_at=NOW, last_outcome="no_product")                           # fetched, not a product
    st(3, last_fetched_at=NOW, last_outcome="parse_error")                          # fetched, extraction failed
    st(4, last_outcome="fetch_error", fail_count=2)                                 # request failed: pending retry
    st(5, gone=True, last_outcome="gone", last_fetched_at=NOW)                      # removed at the merchant
    st(6, last_outcome="disallowed", next_check_at=NOW + timedelta(days=3))         # robots says no
    st(7, selection_state="deferred", selection_reason="budget: rank 3 of 4")       # discovered, waiting for budget
    st(8, selection_state="deferred", selection_reason="budget: rank 4 of 4")
    st(9, last_fetched_at=NOW, last_outcome="alias")                                # second URL of an acquired listing
    run = CollectionRun(source_id=src.id, started_at=NOW, finished_at=NOW + timedelta(seconds=100), status="ok", pages_fetched=10, duration_s=100.0)
    s.add(run)
    s.flush()
    return src


def test_discovered_fetched_and_acquired_are_different_numbers(live_db):
    ensure_sources(live_db)
    with live_db.session() as s:
        src = _world(s)
        c = coverage.source_coverage(s, src, NOW + timedelta(hours=1))
        assert c["discovered"] == 10
        assert c["rejected"] == {"gone": 1, "disallowed": 1}
        # eligible = discovered minus what can never be a distinct product here: gone, disallowed, a duplicate URL, a non-product page
        assert c["eligible"] == 6 and c["duplicates"] == 1 and c["non_product"] == 1
        assert c["acquired"] == 2
        assert c["fetched_ok"] == 3  # eligible pages that answered 200/304: ok, not_modified, parse_error
        assert c["failed"] == {"parse_error": 1, "fetch_error": 1}
        assert c["pending_retry"] == 1 and c["deferred_budget"] == 2 and c["never_attempted"] == 2
        assert c["eligible_coverage"] == 2 / 6
        assert c["extraction_success_rate"] == 2 / 3  # of the 3 eligible pages that answered, 2 yielded a listing
        assert c["avg_seconds_per_page"] == 10.0
        assert c["unfetched_with_counterpart"] == 0


def test_legacy_rows_without_an_outcome_are_counted_from_their_listing(live_db):
    """Rows written before mission 005 have no last_outcome; a linked listing proves acquisition, a bare fetch proves nothing."""
    ensure_sources(live_db)
    with live_db.session() as s:
        _ada_listing(s, "Raspberry Pi Zero 2 W", "5291")
        src, rows = _spark_rows(s, ["old-a", "old-b", "old-c"])
        rows[0].last_fetched_at, rows[0].listing_id = NOW, 1
        rows[1].last_fetched_at = NOW
        c = coverage.source_coverage(s, src, NOW)
        assert c["acquired"] == 1 and c["unknown_outcome"] == 1 and c["never_attempted"] == 1


def test_deferred_urls_with_a_counterpart_elsewhere_are_reported(live_db):
    ensure_sources(live_db)
    with live_db.session() as s:
        src = _world(s)
        r = s.query(CrawlUrl).filter_by(source_id=src.id, url="https://www.sparkfun.com/item-7.html").one()
        r.priority_reasons = [{"name": "cross_merchant", "points": 40, "why": "exact name match with 'Raspberry Pi Zero 2 W' at Adafruit"}]
        c = coverage.source_coverage(s, src, NOW)
        assert c["unfetched_with_counterpart"] == 1


def test_cross_merchant_coverage_counts_products_not_listings(live_db):
    from watchtower.ingest import ingest_listing
    from watchtower.sources import RawListing
    ensure_sources(live_db)
    with live_db.session() as s:
        _ada_listing(s, "Raspberry Pi Zero 2 W", "5291")
        spk = s.query(Source).filter_by(key="sparkfun").one()
        spk.kind = "live"
        for ext, title in (("z", "Raspberry Pi Zero 2 W"), ("p", "Raspberry Pi Pico")):
            ingest_listing(s, spk, None, RawListing(external_id=ext, url=f"https://www.sparkfun.com/{ext}.html", title=title, price_minor=1500,
                                                   currency="USD", brand="Raspberry Pi", category="maker-electronics", availability="in_stock"), NOW)
        s.flush()
        rep = coverage.coverage_report(s, NOW)
        assert rep["cross_merchant_products"] == 1 and rep["live_merchants"] == 2 and rep["live_listings"] == 3
        assert rep["data_class"] == "live"  # demo/sandbox sources are not part of the report


def test_freshness_is_age_of_the_latest_observation(live_db):
    ensure_sources(live_db)
    with live_db.session() as s:
        l = _ada_listing(s, "Raspberry Pi Zero 2 W", "5291")
        src = s.get(Source, l.source_id)
        f = coverage.source_coverage(s, src, NOW + timedelta(hours=30))["freshness"]
        assert f["listings"] == 1 and f["median_age_hours"] == 30 and f["max_age_hours"] == 30 and f["stale"] == 0
        f = coverage.source_coverage(s, src, NOW + timedelta(hours=200))["freshness"]
        assert f["stale"] == 1  # older than the source's freshness window


def test_cli_coverage_refuses_the_live_database_and_reports_a_copy(tmp_path, capsys, monkeypatch):
    from watchtower import cli, config
    from watchtower.db import Database
    path = tmp_path / "copy.db"
    db = Database(f"sqlite:///{path}")
    db.init()
    ensure_sources(db)
    db.engine.dispose()
    monkeypatch.setattr(config, "DB_URL", f"sqlite:///{path}")
    assert cli.main(["coverage", "--db", str(path)]) == 2  # that IS the configured DB
    monkeypatch.setattr(config, "DB_URL", f"sqlite:///{tmp_path / 'other.db'}")
    assert cli.main(["coverage", "--db", str(path)]) == 0
    assert "live merchants=0" in capsys.readouterr().out


def test_two_urls_of_one_listing_count_once_legacy_aliases_included(live_db):
    """Independent consult: on the real snapshot 195 Adafruit rows pointed at 192 listings (aliases from before the alias fix)."""
    ensure_sources(live_db)
    with live_db.session() as s:
        l = _ada_listing(s, "Raspberry Pi Zero 2 W", "5291")
        src = s.get(Source, l.source_id)
        l.url = "https://www.adafruit.com/product/5291"
        for u in ("https://www.adafruit.com/product/5291", "https://www.adafruit.com/product/5291?x=1"):
            s.add(CrawlUrl(source_id=src.id, url=u, listing_id=l.id, last_fetched_at=NOW, last_outcome="ok", first_seen_at=NOW))
        s.flush()
        c = coverage.source_coverage(s, src, NOW)
        assert c["acquired"] == 1 and c["duplicates"] == 1 and c["eligible"] == 1  # the row matching listing.url is the canonical one
        assert c["eligible_coverage"] == 1.0
