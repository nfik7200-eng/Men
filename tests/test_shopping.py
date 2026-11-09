import json
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import FakeFetcher
from shopping import affiliate, assistant, importcost, llm, plans
from shopping.web.app import create_app
from watchtower import config, monitor
from watchtower.collector import run_source
from watchtower.models import AffiliateClick, Listing, MatchCandidate, Observation, Source, WatchItem, utcnow


@pytest.fixture
def seeded(db):
    t = utcnow()
    run_source(db, "webscraper_io_static", fetcher=FakeFetcher(), max_pages=2, now_fn=lambda: t)
    run_source(db, "books_toscrape", fetcher=FakeFetcher(), max_pages=1, now_fn=lambda: t)
    return db


@pytest.fixture
def client(seeded):
    return TestClient(create_app(seeded))


# ---------------- pages ----------------
@pytest.mark.parametrize("path", ["/", "/search", "/search?q=lenovo", "/search?category=book&sort=price_desc",
                                  "/deals", "/research", "/watchlist", "/admin/login", "/health"])
def test_pages_render(client, path):
    r = client.get(path)
    assert r.status_code == 200, path


def test_home_is_honest_about_demo_data_and_no_history(client):
    html = client.get("/").text
    assert "Demo / test data" in html and "single observation" in html


def test_search_filters(client):
    j = client.get("/api/v1/listings?category=laptop&max_price=500&currency=USD&per_page=100").json()
    assert j["total"] > 0 and all(i["category"] == "laptop" and i["price_minor"] <= 50000 for i in j["items"])
    assert client.get("/api/v1/listings?q=zzzznotathing").json()["total"] == 0
    books = client.get("/api/v1/listings?category=book").json()["items"]
    assert books and all(b["currency"] == "GBP" and b["source_kind"] == "demo" for b in books)


def test_listing_detail_single_observation_has_no_fake_chart(client):
    lid = client.get("/api/v1/listings?category=laptop").json()["items"][0]["id"]
    html = client.get(f"/listing/{lid}").text
    assert "1 observation so far" in html and "<svg" not in html
    j = client.get(f"/api/v1/listings/{lid}").json()
    assert j["deal"]["components"] and j["price_status"] == "verified"
    assert len(client.get(f"/api/v1/listings/{lid}/history").json()["observations"]) == 1
    assert client.get("/listing/999999").status_code == 404


def test_chart_appears_with_two_observations(seeded):
    with seeded.session() as s:
        l = s.query(Listing).filter_by(category="laptop").first()
        s.add(Observation(listing_id=l.id, observed_at=utcnow() + timedelta(days=1), price_minor=l.price_minor - 1000,
                          currency=l.currency, availability="unknown"))
        lid = l.id
    html = TestClient(create_app(seeded)).get(f"/listing/{lid}").text
    assert "<svg" in html and 'aria-label="Price history' in html  # label now carries a data summary


def test_xss_is_escaped(seeded):
    with seeded.session() as s:
        l = s.query(Listing).first()
        l.title = '<script>alert(1)</script>'
        lid = l.id
    c = TestClient(create_app(seeded))
    assert "<script>alert(1)</script>" not in c.get(f"/listing/{lid}").text
    assert "<script>alert(1)</script>" not in c.get("/search").text


# ---------------- watchlist ----------------
def test_watch_target_and_plan_limit(seeded):
    c = TestClient(create_app(seeded))
    with seeded.session() as s:
        ids = [i for (i,) in s.query(Listing.id).filter(Listing.price_minor.is_not(None)).limit(7)]
        price = s.get(Listing, ids[0]).price_minor
    r = c.post(f"/watch/{ids[0]}", data={"target": f"{price / 100 + 1:.2f}"}, follow_redirects=False)
    assert r.status_code == 303
    owner = c.cookies.get("wt_owner")
    assert owner and len(owner) == 32
    with seeded.session() as s:
        st = monitor.evaluate(s, owner)[0]
        assert st.triggered and "Target reached" in st.reasons[0]
    for i in ids[1:6]:
        c.post(f"/watch/{i}", data={"target": ""})
    r = c.post(f"/watch/{ids[6]}", data={"target": ""}, follow_redirects=False)
    assert r.headers["location"].endswith("msg=limit") and "allows 5" in c.get(r.headers["location"]).text
    with seeded.session() as s:
        assert s.query(WatchItem).count() == 5
    assert c.post(f"/watch/{ids[0]}", data={"target": "-5"}, follow_redirects=False).headers["location"].endswith("msg=invalid")


def test_restock_alert(seeded):
    with seeded.session() as s:
        l = s.query(Listing).filter_by(category="book").first()
        l.availability = "out_of_stock"
        monitor.add_watch(s, l.id)
        l.availability = "in_stock"
        s.flush()
        assert any("Back in stock" in r for r in monitor.evaluate(s)[0].reasons)


# ---------------- admin protection: see test_audit_web.py ----------------


def test_review_queue_accept_merges(seeded, monkeypatch):
    with seeded.session() as s:
        a, b = s.query(Listing).filter(Listing.price_minor.is_not(None)).limit(2).all()
        s.add(MatchCandidate(listing_id=b.id, product_id=a.product_id, score=0.7, reasons=["test"]))
        aid, bid = a.product_id, b.id
    monkeypatch.setattr(config, "ADMIN_TOKEN", "t" * 32)
    c = TestClient(create_app(seeded))
    assert c.post("/admin/login", data={"token": "t" * 32}, follow_redirects=False).status_code == 303
    assert "Possible counterpart" in c.get("/admin").text  # side-by-side review wording (was "possibly the same")
    c.post("/admin/match/1/accept", data={})
    with seeded.session() as s:
        assert s.get(Listing, bid).product_id == aid and s.get(Listing, bid).match_status == "confirmed"


# ---------------- affiliate ----------------
def test_affiliate_plain_link_by_default_and_templated_when_configured(seeded):
    c = TestClient(create_app(seeded))
    with seeded.session() as s:
        lid = s.query(Listing).first().id
    r = c.get(f"/go/{lid}", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("http")
    with seeded.session() as s:
        click = s.query(AffiliateClick).one()
        assert click.network is None  # no programme assumed
        l = s.get(Listing, lid)
        cfg = {l.source.key: {"network": "demo_net", "template": "{url}?tag=ABC&sub={click_id}"}}
        link = affiliate.record_click(s, l, cfg)
        assert link.is_affiliate and "tag=ABC" in link.url and link.network == "demo_net"
        assert s.query(AffiliateClick).count() == 2


# ---------------- import cost ----------------
def test_import_cost_marks_estimates_and_missing_shipping():
    rules = {"DEFAULT": {"duty_pct": 10, "vat_pct": 20, "verified": False, "note": "n"}}
    e = importcost.estimate(100000, "USD", "XX", None, rules)
    assert e["partial"] and e["total_minor"] == round(100000 * 1.1 * 1.2) and e["rule"] == "DEFAULT"
    assert any(l["amount_minor"] is None and l["evidence"] == "missing" for l in e["lines"])
    e2 = importcost.estimate(100000, "USD", "XX", 5000, rules)
    assert not e2["partial"] and e2["total_minor"] == round(105000 * 1.1 * 1.2)
    assert importcost.estimate(None, "USD", rules=rules)["available"] is False


# ---------------- assistant ----------------
def test_parse_query():
    i = assistant.parse_query("Find me the best programming laptop under $700, estimate importing to Uganda. Compare build quality")
    assert i["budget_max_minor"] == 70000 and i["currency"] == "USD" and i["category"] == "laptop"
    assert i["programming"] and i["want_import"] and i["destination"] == "UG" and "build quality" in i["gaps"]
    assert assistant.parse_query("laptop under 500")["currency"] is None  # never silently assumed; resolved against the catalogue


def test_assistant_uses_real_rows_and_respects_budget(seeded):
    with seeded.session() as s:
        r = assistant.answer(s, "best programming laptop under $700 import to Uganda", use_llm=False)
        assert r["picks"], r["text"]
        for p in r["picks"]:
            assert p.listing.price_minor <= 70000 and p.listing.category == "laptop"
            assert p.listing.id in r["retrieved_listing_ids"] and f"#{p.listing.id}" in r["text"]
            assert (p.listing.specs.get("ram_gb") or 8) >= 8  # no <8GB machines recommended for programming
        assert any("DEMO" in c for c in r["caveats"])
        assert r["picks"][0].import_estimate["partial"]  # shipping unknown => honest lower bound


def test_assistant_reports_gaps_and_never_invents(seeded):
    with seeded.session() as s:
        r = assistant.answer(s, "which laptop has best build quality and upgradeability?", use_llm=False)
        assert any("build quality" in g for g in r["gaps"]) and any("upgradeability" in g for g in r["gaps"])
        none = assistant.answer(s, "best gaming smartwatch under $50", use_llm=False)
        assert none["picks"] == [] and "could not find" in none["text"]
        tiny = assistant.answer(s, "laptop under $10", use_llm=False)
        assert tiny["picks"] == [] and any("above your budget" in c for c in tiny["caveats"])


def test_assistant_api_and_page(client):
    j = client.get("/api/v1/research", params={"q": "best programming laptop under $700"}).json()
    assert j["picks"] and j["retrieved_listing_ids"] and j["llm_summary"] is None
    assert "Estimated" in client.get("/research", params={"q": "laptop under $700 import to Kenya"}).text


def test_llm_is_optional_and_failure_safe(monkeypatch):
    assert llm.summarise({"x": 1}, "q") is None  # disabled by default
    monkeypatch.setattr(config, "OLLAMA_URL", "http://ollama.invalid:11434")
    seen = {}

    def h(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"response": "  Short summary.  "})

    c = httpx.Client(transport=httpx.MockTransport(h))
    assert llm.summarise({"picks": []}, "q", c) == "Short summary."
    assert "UNTRUSTED" in seen["body"]["system"] and seen["body"]["stream"] is False
    bad = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert llm.summarise({}, "q", bad) is None  # failure => deterministic answer only


def test_plans_are_hypotheses_not_billing():
    assert plans.PLANS["pro"].price_hypothesis_usd_month == (5, 10) and plans.plan_for("anyone").key == "free"
