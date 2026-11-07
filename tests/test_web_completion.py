"""Completion programme (frontend): honest labels, plain-language evidence, provenance, admin visibility, side-by-side review."""
import re

from test_dashboard import TOKEN, U, world  # noqa: F401  (fixture reuse)
from watchtower.models import Listing, MatchCandidate, Source


def _first_listing_id(db, sku="5291"):
    with db.session() as s:
        return s.query(Listing).filter_by(sku=sku).one().id


def test_a_single_observation_is_never_labelled_verified(world):
    db, c, _ = world
    html = c.get(f"/listing/{_first_listing_id(db)}").text
    assert "Price verified" not in html and "Price observed" in html  # 'Verified' is reserved for 3+ observations over 7+ days


def test_match_evidence_is_plain_language_not_python_reprs(world):
    db, c, _ = world
    with db.session() as s:
        pid = s.query(Listing).filter(Listing.match_status.in_(("auto", "confirmed"))).first()
        pid = pid.product_id if pid else None
    if pid is None:
        return
    html = c.get(f"/product/{pid}").text
    assert not re.search(r"\['[a-z0-9]", html) and "self ·" not in html and "auto ·" not in html
    assert "Automatic match" in html or "Confirmed" in html


def test_filters_translate_status_and_reasons():
    from shopping.web.app import match_label, plain_reason
    assert match_label("self", 0.6) == "Only listing for this product"
    assert match_label("auto", 0.95) == "Automatic match, 95% confidence"
    assert match_label("confirmed", None) == "Confirmed by a person"
    assert match_label("review", 0.6) == "Awaiting review"
    assert plain_reason("identical normalised name ['4', 'b', 'model']; 2 agreeing spec value(s)") == "identical normalised name (4, b, model); 2 agreeing spec value(s)"


def test_listing_page_shows_data_provenance_a_user_can_check(world):
    db, c, _ = world
    html = c.get(f"/listing/{_first_listing_id(db)}").text
    assert "Where this data comes from" in html
    assert "https://www.adafruit.com/product/5291" in html  # the exact page the data was read from (as text)
    assert "jsonld" in html.lower() or "structured data" in html.lower()  # extraction method
    assert "Source policy" in html and "approved" in html.lower() and "not legal permission" in html.lower()
    assert "HTTP" in html and "Method" in html  # per-observation columns


def test_demo_listing_provenance_says_it_is_not_a_real_shop(world):
    db, c, _ = world
    with db.session() as s:
        from watchtower.models import Source
        lid = s.query(Listing).join(Source, Source.id == Listing.source_id).filter(Source.kind == "demo").first().id
    html = c.get(f"/listing/{lid}").text
    assert "Where this data comes from" in html and "Demo catalogue" in html


def _admin(c):
    assert c.post("/admin/login", data={"token": TOKEN}, follow_redirects=False).status_code == 303


def test_admin_shows_budgets_schedule_status_and_policy_expiry(world):
    db, c, _ = world
    _admin(c)
    html = c.get("/admin").text
    assert "Budgets &amp; schedule" in html or "Budgets & schedule" in html
    assert "Scheduler:" in html and "disabled" in html and "data/schedule.json" in html
    assert "page budget" in html.lower() and "ceiling 500" in html
    assert "policy review due" in html.lower()
    assert "read-only" in html.lower()  # settings are shown, not edited, from the web UI


def test_review_queue_is_side_by_side_with_a_confirmation_and_a_decision_log(world):
    db, c, _ = world
    with db.session() as s:
        a = s.query(Listing).filter_by(sku="5812").one()  # Adafruit
        b = s.query(Listing).join(Source, Source.id == Listing.source_id).filter(Source.key == "sparkfun").first()  # a SparkFun listing
        s.add(MatchCandidate(listing_id=a.id, product_id=b.product_id, score=0.6, reasons=["similar name ['5', 'pi']; spec differs"]))
    _admin(c)
    html = c.get("/admin").text
    assert "Candidate listing" in html and "Possible counterpart" in html  # two columns
    assert f"/listing/{a.id}" in html and f"/listing/{b.id}" in html and "https://www.adafruit.com/product/5812" in html
    assert "data-confirm" in html and "Merge" in html  # accepting merges products: asks first
    assert "['5'" not in html and "(5, pi)" in html
    cid = re.search(r"/admin/match/(\d+)/reject", html).group(1)
    assert c.post(f"/admin/match/{cid}/reject", follow_redirects=False).status_code == 303
    html2 = c.get("/admin").text
    assert "Recently decided" in html2 and "rejected" in html2.lower() and "admin" in html2


# --- batch 2: D6, D8, D10, D12, D13 --------------------------------------------------------------------------------
def test_pager_links_encode_the_query_string(world):
    db, c, _ = world
    from shopping.web.app import urlencode_params
    assert urlencode_params([("q", "a&b #c"), ("page", "2"), ("sort", "price")], drop="page") == "q=a%26b+%23c&sort=price"


def test_flat_price_series_does_not_invent_a_price_range():
    from datetime import datetime, timedelta
    from shopping.web.charts import price_chart
    t = datetime(2026, 11, 1)
    svg = price_chart([(t, 6795), (t + timedelta(days=1), 6795)], "USD")
    assert "67.95" in svg and "64.55" not in svg and "71.35" not in svg  # no invented +/-5% axis
    assert "constant" in svg.lower() or "unchanged" in svg.lower()
    moving = price_chart([(t, 6000), (t + timedelta(days=1), 7000)], "USD")
    assert "aria-label=\"Price history" in moving and "60.00" in moving and "70.00" in moving


def test_pages_have_a_skip_link_focus_ring_and_current_nav_marker(world):
    db, c, _ = world
    html = c.get("/coverage").text
    assert 'class="skip"' in html and 'href="#main"' in html and 'id="main"' in html
    assert 'aria-current="page"' in html
    css = c.get("/static/style.css").text
    assert ":focus-visible" in css and ".skip" in css


def test_public_nav_calls_the_admin_page_admin(world):
    db, c, _ = world
    html = c.get("/").text
    assert ">Admin</a>" in html and ">Sources</a>" not in html


def test_price_statistics_show_currency_on_every_amount(world):
    db, c, _ = world
    html = c.get(f"/listing/{_first_listing_id(db)}").text
    assert not re.search(r"<th>Mean</th><td>\d", html) and not re.search(r"<th>Median</th><td>\d", html)


def test_review_does_not_flag_a_shops_own_category_wording_as_a_spec_conflict(world):
    db, c, _ = world
    with db.session() as s:
        a = s.query(Listing).filter_by(sku="5812").one()
        b = s.query(Listing).join(Source, Source.id == Listing.source_id).filter(Source.key == "sparkfun").first()
        a.specs = {**(a.specs or {}), "merchant_category": "Raspberry Pi > Boards"}
        b.specs = {**(b.specs or {}), "merchant_category": "Development Boards"}
        s.add(MatchCandidate(listing_id=a.id, product_id=b.product_id, score=0.6, reasons=["x"]))
    _admin(c)
    html = c.get("/admin").text
    assert "Different spec values: merchant category" not in html
