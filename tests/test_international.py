"""Landed cost, sourced FX, and affiliate readiness (mission 002 §10-11)."""
import re
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import FakeFetcher
from shopping import affiliate, assistant, fx, importcost
from shopping.web.app import create_app
from watchtower.collector import run_source
from watchtower.models import AffiliateClick, Listing, utcnow

RULES = {"DEFAULT": {"duty_pct": 10, "vat_pct": 20, "verified": False, "note": "placeholder"},
         "XX": {"duty_pct": 5, "vat_pct": 18, "verified": True, "source": "https://customs.example/rates", "retrieved": "2026-11-06",
                "handling_pct": 2, "insurance_pct": 1},
         "YY": {"duty_pct": 5, "vat_pct": 18, "verified": True},  # claims verified but names no source => NOT verified
         "ZZ": {"duty_pct": 10, "vat_pct": 10, "verified": True, "source": "u", "retrieved": "2026-11-06", "de_minimis_minor": 20000}}


# ---------------- landed cost ----------------
def test_all_components_are_separate_and_sum_exactly():
    e = importcost.estimate(100000, "USD", "XX", 5000, RULES)
    by = {l["key"]: l for l in e["lines"]}
    assert by["item"]["amount_minor"] == 100000 and by["shipping"]["amount_minor"] == 5000
    assert by["insurance"]["amount_minor"] == round(105000 * 0.01) == 1050
    cif = 100000 + 5000 + 1050
    assert by["customs_value"]["amount_minor"] == cif
    duty = round(cif * 0.05); vat = round((cif + duty) * 0.18); hnd = round(cif * 0.02)
    assert (by["duty"]["amount_minor"], by["vat"]["amount_minor"], by["handling"]["amount_minor"]) == (duty, vat, hnd)
    assert e["total_minor"] == 100000 + 5000 + 1050 + duty + vat + hnd
    assert e["rule_verified"] and by["duty"]["evidence"] == "verified" and not e["partial"]


def test_unverified_rules_are_never_presented_as_verified():
    for dest in ("DEFAULT", "YY", None, "nowhere"):
        e = importcost.estimate(100000, "USD", dest, 0, RULES)
        assert not e["rule_verified"]
        assert {l["evidence"] for l in e["lines"] if l["key"] in ("duty", "vat")} == {"assumed"}
        assert any("ASSUMED" in a for a in e["assumptions"])


def test_missing_inputs_are_missing_not_zero():
    e = importcost.estimate(100000, "USD", "DEFAULT", None, RULES)
    by = {l["key"]: l for l in e["lines"]}
    assert by["shipping"]["amount_minor"] is None and by["shipping"]["evidence"] == "missing"
    assert by["handling"]["amount_minor"] is None and by["insurance"]["amount_minor"] is None
    assert e["partial"] and set(e["missing_inputs"]) == {"shipping", "handling/clearance fees"}
    assert any("lower bound" in n for n in e["notes"])
    assert importcost.estimate(None, "USD", "XX", 100, RULES)["available"] is False


def test_user_inputs_and_de_minimis():
    e = importcost.estimate(100000, "USD", "DEFAULT", 4000, RULES, insurance_minor=500, handling_minor=2500)
    by = {l["key"]: l for l in e["lines"]}
    assert (by["insurance"]["evidence"], by["handling"]["evidence"], by["shipping"]["evidence"]) == ("user-provided",) * 3
    small = importcost.estimate(15000, "USD", "ZZ", 1000, RULES)
    assert small["total_minor"] == 15000 + 1000 + 0 + 0 and "de-minimis" in " ".join(l["basis"] for l in small["lines"])


# ---------------- sourced FX ----------------
def test_fx_requires_a_source_and_sane_values(db):
    with db.session() as s:
        now = datetime(2026, 11, 6)
        for bad in (dict(rate="0"), dict(rate="-3"), dict(rate="inf"), dict(rate="abc"), dict(source=""), dict(base="USD", quote="USD"),
                    dict(base="US", quote="UGX")):
            kw = dict(base="USD", quote="UGX", rate="3700", as_of=now, source="Central bank X")
            kw.update(bad)
            with pytest.raises(ValueError):
                fx.record_rate(s, **kw)


def test_fx_lookup_direct_inverse_stale_and_no_triangulation(db):
    with db.session() as s:
        t = datetime(2026, 11, 6)
        fx.record_rate(s, "USD", "UGX", "3700", t - timedelta(days=1), "Publisher A", "https://a.example")
        fx.record_rate(s, "USD", "UGX", "3650", t - timedelta(days=10), "Publisher A")
        fx.record_rate(s, "GBP", "USD", "1.25", t, "Publisher B")
        d = fx.get_rate(s, "USD", "UGX", now=t)
        assert d.rate == Decimal("3700") and d.source == "Publisher A" and not d.stale  # latest valid-as-of wins
        inv = fx.get_rate(s, "UGX", "USD", now=t)
        assert inv.inverted and abs(inv.rate - Decimal(1) / Decimal(3700)) < Decimal("1e-12")
        assert fx.get_rate(s, "GBP", "UGX", now=t) is None  # no USD triangulation: unsourced composite is refused
        assert fx.get_rate(s, "USD", "UGX", now=t + timedelta(days=30)).stale
        assert fx.get_rate(s, "USD", "UGX", now=t - timedelta(days=20)) is None  # no rate existed yet at that time
        assert fx.get_rate(s, "USD", "USD", now=t).rate == 1
        c = fx.convert(s, 10000, "USD", "UGX", now=t)
        assert c["minor"] == 37000000 and c["source"] == "Publisher A" and c["as_of"] == t - timedelta(days=1)
        assert fx.convert(s, 100, "USD", "JPY", now=t) is None


def test_estimate_shows_conversion_with_attribution_only_when_rate_exists(db):
    with db.session() as s:
        t = utcnow()
        fx.record_rate(s, "USD", "UGX", "3700", t - timedelta(days=30), "Publisher A")
        q = fx.get_rate(s, "USD", "UGX", now=t)
        e = importcost.estimate(100000, "USD", "DEFAULT", 5000, RULES, fx=q)
        assert e["converted"]["currency"] == "UGX" and e["converted"]["source"] == "Publisher A" and e["converted"]["stale"]
        assert any("stale" in a for a in e["assumptions"])
        assert importcost.estimate(100000, "USD", "DEFAULT", 5000, RULES)["converted"] is None


def test_listing_page_discloses_assumptions_and_missing_fx(db):
    run_source(db, "webscraper_io_static", fetcher=FakeFetcher(), max_pages=1, now_fn=utcnow)
    c = TestClient(create_app(db))
    lid = c.get("/api/v1/listings").json()["items"][0]["id"]
    html = c.get(f"/listing/{lid}", params={"dest": "UG", "ship": "30", "to": "UGX"}).text
    assert "assumed" in html and "Missing inputs" in html and "No sourced USD→UGX exchange rate" in html
    with db.session() as s:
        fx.record_rate(s, "USD", "UGX", "3700", utcnow(), "Publisher A")
    html = c.get(f"/listing/{lid}", params={"dest": "UG", "ship": "30", "to": "UGX"}).text
    assert "Publisher A" in html and "UGX" in html


# ---------------- affiliate readiness ----------------
def _listing(db, **kw):
    run_source(db, "webscraper_io_static", fetcher=FakeFetcher(), max_pages=1, now_fn=utcnow)
    with db.session() as s:
        l = s.query(Listing).first()
        for k, v in kw.items():
            setattr(l, k, v)
        s.flush()
        return l.id


def test_provider_affiliate_url_only_trusted_on_provider_domains(db):
    from watchtower.sources import ADAPTERS
    _ok = "https://www.ebay.co.uk/itm/1?campid=5338000000&customid=7"
    with db.session() as s:
        run_source(db, "webscraper_io_static", fetcher=FakeFetcher(), max_pages=1, now_fn=utcnow)
        l = s.query(Listing).first()
        l.source.key  # webscraper source: hosts = webscraper.io
        l.affiliate_url = "https://evil.example/redirect?to=x"
        assert affiliate.build_link(l, cfg={}).is_affiliate is False and affiliate.build_link(l, cfg={}).url == l.url
        l.affiliate_url = "javascript:alert(1)"
        assert affiliate.build_link(l, cfg={}).is_affiliate is False
        l.affiliate_url = "https://webscraper.io/aff?x=1"  # same provider domain => trusted
        link = affiliate.build_link(l, cfg={})
        assert link.is_affiliate and link.url == l.affiliate_url and link.network.endswith("_affiliate")


def test_template_must_yield_http_and_clicks_are_attributed(db):
    lid = _listing(db)
    with db.session() as s:
        l = s.get(Listing, lid)
        cfg = {l.source.key: {"network": "demo_net", "template": "{url}?tag=ABC&sub={click_id}"}}
        link = affiliate.record_click(s, l, cfg)
        assert "tag=ABC" in link.url and f"sub={s.query(AffiliateClick).one().id}" in link.url
        assert s.query(AffiliateClick).one().network == "demo_net"
        bad = {l.source.key: {"network": "x", "template": "javascript:alert(1)//{url_enc}"}}
        assert affiliate.build_link(l, cfg=bad).is_affiliate is False


def test_disclosures_use_amazons_verified_sentence():
    assert affiliate.disclosures(set()) == []
    d = affiliate.disclosures({"amazon_associates"})
    assert "As an Amazon Associate I earn from qualifying purchases." in d and any("commission" in x for x in d)
    assert "As an Amazon Associate I earn from qualifying purchases." not in affiliate.disclosures({"ebay_epn"})


def test_footer_disclosure_appears_only_when_affiliate_is_active(db, tmp_path, monkeypatch):
    _lid = _listing(db)
    c = TestClient(create_app(db))
    assert "affiliate links: we may earn" not in c.get("/").text
    cfgfile = tmp_path / "aff.json"
    cfgfile.write_text('{"sources": {"webscraper_io_static": {"network": "demo_net", "template": "{url}?t=1"}}}')
    monkeypatch.setattr("watchtower.config.AFFILIATE_CONFIG", str(cfgfile))
    assert "we may earn a commission" in c.get("/").text


def test_ranking_cannot_see_affiliate_data(db, tmp_path, monkeypatch):
    # 1) structural: ranking/scoring modules never import or mention affiliate data
    for mod in ("shopping/assistant.py", "shopping/search.py", "watchtower/deals.py", "watchtower/matching.py"):
        src = Path(mod).read_text()
        assert not re.search(r"affiliate|AffiliateClick|sponsor", src, re.I), mod
    # 2) behavioural: enabling affiliate config and clicks changes no order
    run_source(db, "webscraper_io_static", fetcher=FakeFetcher(), max_pages=2, now_fn=utcnow)
    with db.session() as s:
        before = [(p.listing.id) for p in assistant.answer(s, "best programming laptop under $900", use_llm=False)["picks"]]
        _deals_before = [l.id for l in s.query(Listing).order_by(Listing.id)][:5]
    cfgfile = tmp_path / "aff.json"
    cfgfile.write_text('{"sources": {"webscraper_io_static": {"network": "demo_net", "template": "{url}?t=1"}}}')
    monkeypatch.setattr("watchtower.config.AFFILIATE_CONFIG", str(cfgfile))
    c = TestClient(create_app(db))
    for lid in before:
        c.get(f"/go/{lid}", follow_redirects=False)
    with db.session() as s:
        after = [(p.listing.id) for p in assistant.answer(s, "best programming laptop under $900", use_llm=False)["picks"]]
    assert before == after and before
