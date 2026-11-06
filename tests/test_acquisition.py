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


# ============================================================ (2) explainable prioritisation of never-fetched URLs
from watchtower import acquisition  # noqa: E402
from watchtower.collector import ensure_sources  # noqa: E402
from watchtower.ingest import ingest_listing  # noqa: E402
from watchtower.models import Source  # noqa: E402
from watchtower.sources import RawListing  # noqa: E402

SPK = "https://www.sparkfun.com/"


def _ada_listing(s, title, ext, price=2500):
    src = s.query(Source).filter_by(key="adafruit").one()
    src.kind = "live"
    r = RawListing(external_id=ext, url=f"https://www.adafruit.com/product/{ext}", title=title, price_minor=price, currency="USD",
                   brand="Raspberry Pi", category="maker-electronics", availability="in_stock")
    return ingest_listing(s, src, None, r, NOW)[0]


def _spark_rows(s, slugs, tier=0):
    src = s.query(Source).filter_by(key="sparkfun").one()
    rows = []
    for slug in slugs:
        c = CrawlUrl(source_id=src.id, url=f"{SPK}{slug}.html", first_seen_at=NOW)
        s.add(c)
        rows.append(c)
    s.flush()
    return src, rows


def test_hint_from_slug_is_words_without_the_domain_or_extension():
    assert acquisition.hint_from_url("https://www.sparkfun.com/raspberry-pi-4-model-b-2gb.html") == "raspberry pi 4 model b 2gb"
    assert acquisition.hint_from_url("https://www.adafruit.com/product/5812") is None  # numeric id: no name


def test_a_url_whose_name_matches_a_product_at_another_merchant_outranks_an_unrelated_one(live_db):
    ensure_sources(live_db)
    with live_db.session() as s:
        _ada_listing(s, "Raspberry Pi Zero 2 W", "5291")
        src, rows = _spark_rows(s, ["raspberry-pi-zero-2-w", "raspberry-pi-4-model-b-2gb", "esp32-thing-plus"])
        hints = {c.url: acquisition.hint_from_url(c.url) for c in rows}
        ranked = acquisition.rank_fresh(s, src, rows, {c.url: 0 for c in rows}, hints, NOW)
        assert ranked[0].crawl.url.endswith("raspberry-pi-zero-2-w.html")
        top = ranked[0]
        assert top.score > ranked[1].score
        cross = next(c for c in top.components if c.name == "cross_merchant")
        assert cross.points >= 35 and "Adafruit" in cross.why and "Raspberry Pi Zero 2 W" in cross.why and "exact" in cross.why


def test_name_similarity_is_vetoed_by_kit_bundle_wireless_and_unit_conflicts(live_db):
    """Similar name != same product: vetoed candidates get no cross-merchant credit, and say why."""
    ensure_sources(live_db)
    with live_db.session() as s:
        _ada_listing(s, "Raspberry Pi Zero 2 W", "5291")
        _ada_listing(s, "Raspberry Pi 4 Model B - 4 GB RAM", "4296")
        src, rows = _spark_rows(s, ["sparkfun-raspberry-pi-zero-2-w-basic-kit", "raspberry-pi-4-model-b-8gb", "raspberry-pi-zero-w"])
        hints = {c.url: acquisition.hint_from_url(c.url) for c in rows}
        ranked = {r.crawl.url.rsplit("/", 1)[1]: r for r in acquisition.rank_fresh(s, src, rows, {c.url: 0 for c in rows}, hints, NOW)}
        for slug in ("sparkfun-raspberry-pi-zero-2-w-basic-kit.html", "raspberry-pi-4-model-b-8gb.html"):
            cross = next(c for c in ranked[slug].components if c.name == "cross_merchant")
            assert cross.points == 0, slug
        assert "bundle" in next(c for c in ranked["sparkfun-raspberry-pi-zero-2-w-basic-kit.html"].components if c.name == "cross_merchant").why
        assert "spec value" in next(c for c in ranked["raspberry-pi-4-model-b-8gb.html"].components if c.name == "cross_merchant").why


def test_a_hint_never_matches_or_changes_any_listing(live_db):
    """Name hints are priority-only evidence: no product_id/match_status writes, no match candidates."""
    from watchtower.models import MatchCandidate
    ensure_sources(live_db)
    with live_db.session() as s:
        l = _ada_listing(s, "Raspberry Pi Zero 2 W", "5291")
        before = (l.product_id, l.match_status)
        src, rows = _spark_rows(s, ["raspberry-pi-zero-2-w"])
        acquisition.rank_fresh(s, src, rows, {rows[0].url: 0}, {rows[0].url: "raspberry pi zero 2 w"}, NOW)
        s.flush()
        assert (l.product_id, l.match_status) == before and s.query(MatchCandidate).count() == 0 and s.query(Listing).count() == 1


def test_ranking_is_deterministic_and_ties_do_not_depend_on_alphabet_alone(live_db):
    ensure_sources(live_db)
    with live_db.session() as s:
        src, rows = _spark_rows(s, [f"esp32-board-{i:02d}" for i in range(12)])
        hints = {c.url: acquisition.hint_from_url(c.url) for c in rows}
        tiers = {c.url: 2 for c in rows}
        a = [r.crawl.url for r in acquisition.rank_fresh(s, src, rows, tiers, hints, NOW)]
        b = [r.crawl.url for r in acquisition.rank_fresh(s, src, list(reversed(rows)), tiers, hints, NOW)]
        assert a == b
        for c in rows[6:]:
            c.defer_count = 4  # these have waited through four runs
        a2 = [r.crawl.url for r in acquisition.rank_fresh(s, src, rows, tiers, hints, NOW)]
        assert set(a2[:6]) == {c.url for c in rows[6:]}  # waiting is rewarded: the tail overtakes the alphabetical head


# ============================================================ (3) the per-run plan: split, states and recorded reasons
from datetime import timedelta  # noqa: E402,F811


def _plan(s, src, rows, now=NOW, cap=4, tiers=None, discovered=None):
    known = {c.url: c for c in rows}
    return acquisition.plan_run(s, src, known, set(discovered if discovered is not None else known), tiers or {c.url: 2 for c in rows},
                                {c.url: acquisition.hint_from_url(c.url) for c in rows}, cap, now, recheck_share=0.5)


def test_plan_keeps_the_recheck_first_split_and_records_why_everything_else_waits(live_db):
    ensure_sources(live_db)
    with live_db.session() as s:
        src, rows = _spark_rows(s, [f"esp32-board-{i:02d}" for i in range(10)])
        for c in rows[:3]:  # three already-fetched URLs due for a re-check
            c.last_fetched_at = NOW - timedelta(days=3)
            c.next_check_at = NOW - timedelta(hours=1)
        plan = _plan(s, src, rows, cap=4)
        assert len(plan.recheck) == 2 and len(plan.fresh) == 2  # 50% of the cap re-checks, the rest discovery
        assert {c.url for c in plan.recheck} == {rows[0].url, rows[1].url}  # oldest fetched first
        states = {c.url: (c.selection_state, c.selection_reason) for c in rows}
        assert [v[0] for v in states.values()].count("selected") == 4
        deferred = [c for c in rows if c.selection_state == "deferred"]
        assert len(deferred) == 6 and all("budget" in c.selection_reason and "rank" in c.selection_reason for c in deferred)
        assert all(c.priority_score is not None and c.priority_reasons for c in rows if c.last_fetched_at is None)
        assert all(c.defer_count == 1 for c in deferred if c.last_fetched_at is None)
        assert all(c.last_considered_at == NOW for c in rows)


def test_plan_classifies_every_non_selected_url_with_a_reason(live_db):
    ensure_sources(live_db)
    with live_db.session() as s:
        src, rows = _spark_rows(s, ["a-board-one", "a-board-two", "a-board-three", "a-board-four", "a-board-five", "a-board-six"])
        rows[0].gone = True
        rows[1].last_outcome, rows[1].next_check_at, rows[1].last_fetched_at = "disallowed", NOW + timedelta(days=3), NOW
        rows[2].last_fetched_at, rows[2].next_check_at = NOW - timedelta(hours=2), NOW + timedelta(hours=20)  # fetched, not yet due
        rows[3].fail_count = acquisition.MAX_FAILS  # never acquired, keeps failing
        plan = _plan(s, src, rows, cap=10, discovered={r.url for r in rows[:5]})  # row 5 vanished from discovery
        got = {c.url.rsplit("/", 1)[1][:-5]: (c.selection_state, c.selection_reason) for c in rows}
        assert got["a-board-one"] == ("rejected", "gone")
        assert got["a-board-two"] == ("rejected", "disallowed")
        assert got["a-board-three"] == ("deferred", "not_due")
        assert got["a-board-four"] == ("rejected", "retry_limit")
        assert got["a-board-five"][0] == "selected"
        assert got["a-board-six"] == ("skipped", "not_in_discovery")
        assert [c.url for c in plan.fresh] == [rows[4].url]


def test_every_eligible_url_is_eventually_selected_even_in_the_lowest_tier(live_db):
    """Starvation guard: with 40 equal-tier URLs and 4 new slots per run, nothing waits more than a bounded number of runs."""
    ensure_sources(live_db)
    with live_db.session() as s:
        src, rows = _spark_rows(s, [f"esp32-board-{i:02d}" for i in range(40)])
        first_pick = {}
        for run in range(40):
            plan = _plan(s, src, rows, now=NOW + timedelta(days=run), cap=4)
            for c in plan.fresh:
                first_pick.setdefault(c.url, run)
                c.last_fetched_at, c.next_check_at = NOW + timedelta(days=run), NOW + timedelta(days=run + 365)
            if len(first_pick) == 40:
                break
        assert len(first_pick) == 40, "some eligible URLs were never selected"
        assert max(first_pick.values()) <= 40 // 2  # 40 URLs / 2 new slots = 20 runs at most (re-check half is idle here)


def test_domain_budget_is_enforced_and_configurable(live_db, monkeypatch):
    from watchtower import config
    assert config.page_budget("sparkfun", 40) == 40
    monkeypatch.setenv("WATCHTOWER_BUDGET_SPARKFUN", "7")
    assert config.page_budget("sparkfun", 40) == 7
    monkeypatch.setenv("WATCHTOWER_BUDGET_SPARKFUN", "-3")
    assert config.page_budget("sparkfun", 40) == 40  # nonsense falls back to the adapter default, never unbounded
    monkeypatch.setenv("WATCHTOWER_BUDGET_SPARKFUN", "999999")
    assert config.page_budget("sparkfun", 40) == config.MAX_PAGE_BUDGET  # hard ceiling: no accidental crawl-budget bypass


# ============================================================ (4) the collector records what happened to every URL it tried
def test_collector_records_outcome_and_selection_reason_per_url(live_db):
    site = _site()
    site.pages[U["a5291"]] = "<html><h1>Please complete the CAPTCHA</h1></html>"
    run_source(live_db, "adafruit", fetcher=site, max_pages=1, now_fn=lambda: NOW)
    with live_db.session() as s:
        rows = {c.url: c for c in s.query(CrawlUrl)}
        picked = [c for c in rows.values() if c.selection_state == "selected"]
        assert len(picked) == 1 and picked[0].last_outcome in ("no_product", "ok")
        waiting = [c for c in rows.values() if c.selection_state == "deferred"]
        assert len(waiting) == 1 and "budget" in waiting[0].selection_reason and waiting[0].last_outcome is None
        assert waiting[0].defer_count == 1 and waiting[0].priority_reasons
        assert all(c.name_hint is None or isinstance(c.name_hint, str) for c in rows.values())
    run_source(live_db, "adafruit", fetcher=_site(), max_pages=5, now_fn=lambda: NOW + timedelta(days=2))
    with live_db.session() as s:
        outcomes = {c.last_outcome for c in s.query(CrawlUrl)}
        assert "ok" in outcomes and all(o for o in outcomes)  # every tried URL has an outcome
