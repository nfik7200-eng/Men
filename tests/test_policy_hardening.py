"""Second-review compliance gaps (docs/AUDIT_REPORT.md style): fail-closed review dates, placeholder contacts, transient
robots errors, robots-hash changes, minimum delay for live sources."""
import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from conftest import REAL, FakeSite
from watchtower import config, policy
from watchtower.collector import ensure_sources, run_source
from watchtower.fetcher import Fetcher
from watchtower.models import Source, SourcePolicy, utcnow
from watchtower.policy import SourceNotApproved, ensure_policies

GOOD = "crawler@watchtower-research.net"


@pytest.fixture
def live(db, monkeypatch):
    monkeypatch.setattr(config, "CONTACT", GOOD)
    ensure_sources(db)
    ensure_policies(db)
    return db


@pytest.mark.parametrize("c,ok", [(GOOD, True), ("https://watchtower.dev/bot", True), ("you@example.com", False), ("ops@example.org", False),
                                  ("a@b", False), ("set-WATCHTOWER_CONTACT", False), ("", False), ("http://localhost", False),
                                  ("x@sub.example.net", False), ("just some words http", False)])
def test_contact_must_be_real_not_a_documentation_placeholder(c, ok):
    assert config.contact_ok(c) is ok


def test_placeholder_contact_stops_live_run(live, monkeypatch):
    monkeypatch.setattr(config, "CONTACT", "you@example.com")
    site = FakeSite()
    with pytest.raises(SourceNotApproved, match="real contact"):
        run_source(live, "adafruit", fetcher=site)
    assert site.requested == []


def test_policy_entry_without_reviewed_at_is_unverified_and_never_runs(db, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONTACT", GOOD)
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"policies": {"adafruit": {"status": "approved", "robots_sha": "x"}}}))  # approved but never reviewed
    monkeypatch.setattr(policy, "POLICY_FILE", f)
    ensure_sources(db)
    ensure_policies(db)
    with db.session() as s:
        p = s.scalar(select(SourcePolicy))
        assert p.status == "unverified" and p.review_due_at.year == 1970 and "no reviewed_at" in p.notes
    with pytest.raises(SourceNotApproved):
        run_source(db, "adafruit", fetcher=FakeSite(), now_fn=lambda: utcnow() + timedelta(days=3000))  # "never expires" is not possible


def test_a_transient_robots_error_does_not_reject_the_source(live):
    class Flaky(FakeSite):
        def robots_unreliable(self, url):
            return True
    with pytest.raises(SourceNotApproved, match="temporarily unreadable"):
        run_source(live, "adafruit", fetcher=Flaky())
    with live.session() as s:
        assert s.scalar(select(SourcePolicy).join(Source).where(Source.key == "adafruit")).status == "approved"  # unchanged


def test_fetcher_retries_robots_5xx_before_treating_it_as_unreliable():
    calls = {"n": 0}

    def h(req):
        if req.url.path == "/robots.txt":
            calls["n"] += 1
            return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
        return httpx.Response(200, text="ok", headers={"content-type": "text/html"})

    f = Fetcher(httpx.Client(transport=httpx.MockTransport(h)), min_delay=0, sleep=lambda s: None, allowed_hosts={"shop.test"})
    assert f.get("https://shop.test/a") == "ok" and not f.robots_unreliable("https://shop.test/") and calls["n"] == 3
    g = Fetcher(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503) if r.url.path == "/robots.txt" else httpx.Response(200))),
                min_delay=0, sleep=lambda s: None, allowed_hosts={"shop.test"})
    assert g.robots_unreliable("https://shop.test/") is True and g.allowed("https://shop.test/a") is False  # conservative, flagged


def test_robots_change_that_still_allows_us_shortens_the_review_fuse(live):
    run_source(live, "adafruit", fetcher=FakeSite(robots_sha="a-new-hash"), now_fn=utcnow)
    with live.session() as s:
        p = s.scalar(select(SourcePolicy).join(Source).where(Source.key == "adafruit"))
        assert p.robots_sha == "a-new-hash" and "re-review within 7 days" in p.notes
        assert p.review_due_at <= utcnow() + timedelta(days=7, minutes=1)
    with pytest.raises(SourceNotApproved, match="review expired"):
        run_source(live, "adafruit", fetcher=FakeSite(robots_sha="a-new-hash"), now_fn=lambda: utcnow() + timedelta(days=8))


def test_live_sources_have_a_two_second_floor_even_if_configured_lower(live, monkeypatch):
    monkeypatch.setattr(config, "MIN_REQUEST_DELAY_S", 0.1)
    seen = {}
    orig = Fetcher.__init__

    def spy(self, *a, **k):
        seen["min_delay"] = k.get("min_delay")
        orig(self, *a, **k)
    monkeypatch.setattr(Fetcher, "__init__", spy)
    monkeypatch.setattr(Fetcher, "robots_info", lambda self, url: (None, None))
    monkeypatch.setattr(Fetcher, "allowed", lambda self, url: True)
    monkeypatch.setattr(Fetcher, "get", lambda self, url, headers=None: "<html></html>")
    monkeypatch.setattr(Fetcher, "get_ex", lambda self, *a, **k: (_ for _ in ()).throw(AssertionError("no product pages expected")))
    run_source(live, "adafruit", max_pages=1)
    assert seen["min_delay"] >= 2.0


def test_committed_fixtures_contain_no_product_descriptions():
    for name in ("ada_5291", "ada_5812", "spark_zero2w", "spark_zerow"):
        text = (REAL / f"{name}.html").read_text()
        assert "[description removed from fixture]" in text and len(text) < 5000
        assert '"description":""' in text.replace('": ""', '":""')
