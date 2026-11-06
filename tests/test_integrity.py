"""Loop F, iteration 3: a read-only integrity report that every improvement loop runs before/after a change."""
from datetime import datetime

from watchtower import cli
from watchtower.collector import ensure_sources
from watchtower.db import Database
from watchtower.ingest import ingest_listing
from watchtower.integrity import integrity_report
from watchtower.models import Listing, MatchCandidate, Observation, Source
from watchtower.sources import RawListing

T0 = datetime(2026, 11, 6, 12, 0)


def _raw(ext, title, price=2500):
    return RawListing(external_id=ext, url=f"https://x.example/{ext}", title=title, price_minor=price, currency="USD",
                      brand="Raspberry Pi", category="maker-electronics", availability="in_stock")


def _db(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'w.db'}")
    db.init()
    ensure_sources(db)
    with db.session() as s:
        for key in ("shopa", "shopb"):
            s.add(Source(key=key, name=key, base_url=f"https://{key}.example", currency="USD", kind="live"))
        s.flush()
        a, b = (s.query(Source).filter_by(key=k).one() for k in ("shopa", "shopb"))
        ingest_listing(s, a, None, _raw("a1", "Raspberry Pi Zero 2 W"), T0)
        ingest_listing(s, b, None, _raw("b1", "Raspberry Pi Zero 2 W", 1900), T0)
        ingest_listing(s, a, None, _raw("a2", "Raspberry Pi 3 A+"), T0)
    db.engine.dispose()
    return tmp_path / "w.db"


def test_clean_database_passes_and_reports_metrics(tmp_path):
    path = _db(tmp_path)
    before = path.read_bytes()
    r = integrity_report(path)
    assert r["ok"], [c for c in r["checks"] if not c["ok"]]
    assert r["metrics"]["live_listings"] == 3 and r["metrics"]["live_merchants"] == 2
    assert r["metrics"]["multi_merchant_products"] == 1 and r["metrics"]["live_observation_days"] == 1
    assert r["metrics"]["verified_history_listings"] == 0  # nobody has 3+ collections over 7+ days
    assert path.read_bytes() == before  # read-only


def test_each_invariant_violation_is_reported(tmp_path):
    path = _db(tmp_path)
    db = Database(f"sqlite:///{path}")
    with db.session() as s:
        a1, b1, a2 = (s.query(Listing).filter_by(external_id=e).one() for e in ("a1", "b1", "a2"))
        s.add(Observation(listing_id=a2.id, observed_at=T0, price_minor=0, currency="USD"))      # zero price
        b1.description = "copied marketing text"                                                 # stored description
        s.add(MatchCandidate(listing_id=a2.id, product_id=b1.product_id, score=0.6, reasons=[]))
        s.add(MatchCandidate(listing_id=b1.id, product_id=a2.product_id, score=0.6, reasons=[]))  # its mirror
        s.add(MatchCandidate(listing_id=a1.id, product_id=a1.product_id, score=0.6, reasons=[]))  # points at itself
    db.engine.dispose()
    bad = {c["name"] for c in integrity_report(path)["checks"] if not c["ok"]}
    assert {"nonpositive_prices", "listing_price_matches_last_observation", "live_text_or_images_stored",
            "mirrored_pending_candidates", "candidate_points_at_own_product"} <= bad


def test_cli_verify_exit_code(tmp_path, capsys):
    path = _db(tmp_path)
    assert cli.main(["verify", "--db", str(path)]) == 0
    assert "integrity: OK" in capsys.readouterr().out
    assert cli.main(["verify", "--db", str(tmp_path / "missing.db")]) == 2


def test_a_recorded_currency_change_is_not_an_integrity_violation(tmp_path):
    """Independent review: older observations legitimately keep the old currency after a currency_change event."""
    path = _db(tmp_path)
    db = Database(f"sqlite:///{path}")
    with db.session() as s:
        a = s.query(Source).filter_by(key="shopa").one()
        raw = _raw("a1", "Raspberry Pi Zero 2 W")
        raw.currency = "EUR"
        ingest_listing(s, a, None, raw, datetime(2026, 11, 7, 12, 0))
    db.engine.dispose()
    r = integrity_report(path)
    assert r["ok"], [c for c in r["checks"] if not c["ok"]]


def test_verify_reports_a_non_database_file_instead_of_crashing(tmp_path, capsys):
    bad = tmp_path / "x.db"
    bad.write_bytes(b"garbage" * 100)
    assert cli.main(["verify", "--db", str(bad)]) == 2
    assert "cannot read" in capsys.readouterr().out
