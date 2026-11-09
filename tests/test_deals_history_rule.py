"""The verified-deal history rule: 3+ distinct collections spanning at least MIN_HISTORY_SPAN_DAYS (eight).
Synthetic fixtures only: these prices are test inputs, not market data."""
from datetime import timedelta

from watchtower import config, deals
from watchtower.models import Observation, utcnow
from tests._ml_helpers import mk_listing, mk_source


def _drop_history(s, span_days: float):
    """Eight-ish collections of a usual price, then a lower current price, over `span_days`."""
    src = mk_source(s, "histrule")
    l = mk_listing(s, src, "h1", "Synthetic board", price=6000)
    t0 = utcnow() - timedelta(days=span_days)
    times = [t0 + timedelta(days=span_days * i / 7) for i in range(8)]
    for i, t in enumerate(times):
        s.add(Observation(listing_id=l.id, observed_at=t, price_minor=10000 if i < 7 else 6000, currency="USD", availability="in_stock"))
    l.last_observed_at = times[-1]          # the latest collection, which is what freshness is judged on
    s.flush()
    return l


def _verified_components(ds):
    return [c for c in ds.components if c.evidence == "verified" and c.points > 0]


def test_a_drop_over_seven_and_a_half_days_is_not_verified(db):
    with db.session() as s:
        l = _drop_history(s, span_days=7.5)
        ds = deals.score_listing(s, l)
        assert not _verified_components(ds), "a history shorter than eight days must not verify a drop"
        assert ds.label != "Verified price drop"


def test_a_drop_over_eight_days_can_be_verified(db):
    with db.session() as s:
        l = _drop_history(s, span_days=config.MIN_HISTORY_SPAN_DAYS + 1)
        ds = deals.score_listing(s, l)
        assert _verified_components(ds), "eight or more days of collections is enough to verify"


def test_a_listing_never_observed_is_stale_and_scoring_does_not_crash(db):
    with db.session() as s:
        src = mk_source(s, "neverseen")
        l = mk_listing(s, src, "n1", "Unseen board", price=6000)
        l.last_observed_at = None
        s.flush()
        ds = deals.score_listing(s, l)
        assert ds.label == "Stale data"
        assert any("Never observed" in w for w in ds.warnings)
