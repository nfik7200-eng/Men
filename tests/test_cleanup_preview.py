"""Production-cleanup preview: runs `rematch` on a TEMPORARY copy of a snapshot and reports verify before/after, so the
owner can approve the real cleanup with evidence. The source file is never modified."""
import hashlib
import sqlite3
from datetime import datetime

from test_review_queue import _raw, A_TITLE, B_TITLE, T0  # noqa: F401
from watchtower import cli
from watchtower.cleanup import preview_cleanup
from watchtower.collector import ensure_sources
from watchtower.db import Database
from watchtower.ingest import ingest_listing
from watchtower.matching import rematch_all
from watchtower.models import Listing, MatchCandidate, Source


def _legacy_db(tmp_path):
    """A database in the legacy state: a mirrored review pair and a 'self' label on a shared product."""
    path = tmp_path / "legacy.db"
    db = Database(f"sqlite:///{path}")
    db.init()
    ensure_sources(db)
    with db.session() as s:
        a = Source(key="shopa", name="shopa", base_url="https://a.example", currency="USD", kind="live")
        b = Source(key="shopb", name="shopb", base_url="https://b.example", currency="USD", kind="live")
        s.add_all([a, b])
        s.flush()
        x, *_ = ingest_listing(s, a, None, _raw("a1", A_TITLE), T0)
        y, *_ = ingest_listing(s, b, None, _raw("b1", B_TITLE), T0)
        z1, *_ = ingest_listing(s, a, None, _raw("a2", "Raspberry Pi Zero 2 W"), T0)
        z2, *_ = ingest_listing(s, b, None, _raw("b2", "Raspberry Pi Zero 2 W"), T0)
        s.flush()
        rematch_all(s)
        s.flush()
        (c,) = s.query(MatchCandidate).filter_by(status="pending").all()
        l, other = (x, y) if c.listing_id == y.id else (y, x)
        s.add(MatchCandidate(listing_id=l.id, product_id=other.product_id, score=0.6, reasons=["legacy mirror"]))
        z2.match_status = "self"
    db.engine.dispose()
    return path


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_preview_reports_before_after_and_never_touches_the_source(tmp_path):
    path = _legacy_db(tmp_path)
    before = _sha(path)
    r = preview_cleanup(path)
    assert _sha(path) == before  # byte-identical
    failing_before = {c["name"] for c in r["before"]["checks"] if not c["ok"]}
    failing_after = {c["name"] for c in r["after"]["checks"] if not c["ok"]}
    assert {"mirrored_pending_candidates", "shared_product_status_is_not_self"} <= failing_before
    assert failing_after == set() and r["after"]["ok"]
    assert r["metrics_before"]["pending_review_candidates"] > r["metrics_after"]["pending_review_candidates"]
    assert r["rematch"]["self_to_auto"] == 1 and r["rematch"]["candidates_dropped"] >= 1
    assert r["metrics_after"]["multi_merchant_products"] == r["metrics_before"]["multi_merchant_products"]  # no product merged or split
    assert r["listings_changed"] >= 1 and "merged" not in str(r["warnings"])


def test_preview_flags_any_change_to_product_grouping_or_observations(tmp_path):
    path = _legacy_db(tmp_path)
    r = preview_cleanup(path)
    assert r["observations_before"] == r["observations_after"] and r["products_regrouped"] == 0
    assert r["safe_to_apply"] is True


def test_cli_cleanup_preview_exit_codes(tmp_path, capsys, monkeypatch):
    from watchtower import config
    path = _legacy_db(tmp_path)
    monkeypatch.setattr(config, "DB_URL", f"sqlite:///{tmp_path / 'other.db'}")
    assert cli.main(["cleanup-preview", "--db", str(path)]) == 0
    out = capsys.readouterr().out
    assert "BEFORE" in out and "AFTER" in out and "nothing was modified" in out
    monkeypatch.setattr(config, "DB_URL", f"sqlite:///{path}")
    assert cli.main(["cleanup-preview", "--db", str(path)]) == 2  # refuses the configured live DB
