"""Mission 005: acquisition intelligence. Sections: (1) per-URL disallow vs server block, ..."""
from datetime import datetime, timedelta

import pytest

from conftest import FakeSite
from test_real_sources import NOW, U, _site, live_db  # noqa: F401  (fixture reuse)
from watchtower.collector import run_source
from watchtower.fetcher import FetchBlocked, FetchDisallowed
from watchtower.models import CollectionRun, CrawlUrl, Listing


def test_a_disallowed_url_is_recorded_and_skipped_but_the_run_continues(live_db):
    """Independent trace (mission 005): FetchBlocked meant both 'robots disallows THIS url' and HTTP 401/403/451, and the
    collector stopped the whole run on either, so one disallowed URL starved every later page."""
    site = _site(disallow={U["a5291"]})
    rid = run_source(live_db, "adafruit", fetcher=site, now_fn=lambda: NOW)
    with live_db.session() as s:
        r = s.get(CollectionRun, rid)
        assert r.status != "blocked" and r.pages_fetched == 1  # 5812 was still collected
        assert s.query(Listing).filter_by(sku="5812").count() == 1 and s.query(Listing).filter_by(sku="5291").count() == 0
        c = s.query(CrawlUrl).filter_by(url=U["a5291"]).one()
        assert c.last_outcome == "disallowed" and "robots" in (c.last_outcome_detail or "")
        assert c.next_check_at > NOW + timedelta(days=5)  # not hammered every run; re-checked when robots may have changed
        assert c.listing_id is None and not c.gone


def test_a_server_block_still_stops_the_whole_run(live_db):
    """CLAUDE.md: on 401/403/451 STOP — no bypass, no 'try the next URL'."""
    site = _site(forbid={U["a5812"], U["a5291"]})
    rid = run_source(live_db, "adafruit", fetcher=site, now_fn=lambda: NOW)
    with live_db.session() as s:
        r = s.get(CollectionRun, rid)
        assert r.status == "blocked" and r.pages_fetched == 0 and "403" in r.error
        assert s.query(CrawlUrl).filter(CrawlUrl.last_outcome == "disallowed").count() == 0
    assert [u for u in site.requested if "/product/" in u] == [U["a5291"]]  # it did not go on to the second URL


def test_disallowed_is_a_blocked_subclass_so_existing_handlers_stay_conservative():
    assert issubclass(FetchDisallowed, FetchBlocked)
