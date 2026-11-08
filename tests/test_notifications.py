"""In-app notifications: schema only by explicit migration, idempotent generation, read state, owner isolation,
rollback safety, and the live-database guard."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from shopping.web.app import create_app
from tests._ml_helpers import mk_listing, mk_source
from watchtower import cli, config, monitor, notifications as notes
from watchtower.models import Listing


def _listing(db, price=1000, availability="in_stock"):
    with db.session() as s:
        src = mk_source(s, "shopn")
        l = mk_listing(s, src, "n1", "Sensor board", price=price)
        l.availability = availability
        s.commit()
        return l.id


def _migrated(db):
    notes.migrate(db.engine, apply=True)


def test_database_init_never_creates_the_notifications_table(db):
    assert not inspect(db.engine).has_table(notes.TABLE)  # app start (init) must not change an existing database


def test_migrate_is_a_dry_run_unless_applied(db):
    text = notes.migrate(db.engine, apply=False)
    assert "CREATE TABLE notifications" in text and "DRY RUN" in text
    assert not inspect(db.engine).has_table(notes.TABLE)


def test_migrate_apply_creates_once_and_is_idempotent(db):
    assert notes.migrate(db.engine, apply=True) == "created table notifications"
    assert notes.migrate(db.engine, apply=True) == "already present: nothing to do"
    assert inspect(db.engine).has_table(notes.TABLE)


def test_target_alert_is_recorded_once_and_a_new_low_price_adds_one(db):
    _migrated(db)
    lid = _listing(db, price=1000)
    with db.session() as s:
        monitor.add_watch(s, lid, "alice", target_price_minor=900)
        s.commit()
    with db.session() as s:
        assert notes.generate(s, "alice") == 0          # price 1000 is above target 900: no alert
    with db.session() as s:
        s.get(Listing, lid).price_minor = 850
        s.commit()
    with db.session() as s:
        assert notes.generate(s, "alice") == 1
        assert notes.generate(s, "alice") == 0          # same event again: nothing new
        assert notes.unread_count(s, "alice") == 1
        assert notes.list_for(s, "alice")[0].kind == "target_reached"


def _obs(db, listing_id, availability, at):
    from watchtower.models import Observation
    with db.session() as s:
        s.add(Observation(listing_id=listing_id, observed_at=at, price_minor=1000, currency="USD", availability=availability))
        s.commit()


def test_each_real_restock_alerts_once_and_repeat_crawls_add_nothing(db):
    from datetime import timedelta
    from watchtower.models import utcnow
    _migrated(db)
    lid = _listing(db, availability="out_of_stock")
    t0 = utcnow()
    _obs(db, lid, "out_of_stock", t0 - timedelta(hours=2))          # before the watch: history only
    with db.session() as s:
        monitor.add_watch(s, lid, "bob", target_price_minor=None, notify_restock=True)
        s.commit()
    _obs(db, lid, "in_stock", t0 + timedelta(minutes=1))            # restock #1
    _obs(db, lid, "in_stock", t0 + timedelta(minutes=2))            # still in stock: not a new restock
    _obs(db, lid, "out_of_stock", t0 + timedelta(minutes=3))        # sold out again
    _obs(db, lid, "in_stock", t0 + timedelta(minutes=4))            # restock #2
    with db.session() as s:
        assert notes.generate(s, "bob") == 2
        assert notes.generate(s, "bob") == 0                        # repeat crawl: nothing new
        assert notes.unread_count(s, "bob") == 2
        assert {n.kind for n in notes.list_for(s, "bob")} == {"back_in_stock"}


def test_a_restock_before_the_watch_existed_is_not_alerted(db):
    from datetime import timedelta
    from watchtower.models import utcnow
    _migrated(db)
    lid = _listing(db, availability="out_of_stock")
    t0 = utcnow()
    _obs(db, lid, "out_of_stock", t0 - timedelta(hours=5))
    _obs(db, lid, "in_stock", t0 - timedelta(hours=4))              # restocked before anyone watched it
    with db.session() as s:
        monitor.add_watch(s, lid, "gina", target_price_minor=None, notify_restock=True)
        s.commit()
    with db.session() as s:
        assert notes.generate(s, "gina") == 0


def test_read_state_and_owner_isolation(db):
    _migrated(db)
    lid = _listing(db, price=800)
    with db.session() as s:
        monitor.add_watch(s, lid, "carol", target_price_minor=900)
        s.commit()
    with db.session() as s:
        notes.generate(s, "carol")
        note_id = notes.list_for(s, "carol")[0].id
        assert notes.mark_read(s, "mallory", note_id) is False      # another person's alert: refused like a missing one
        assert notes.unread_count(s, "carol") == 1
        assert notes.mark_read(s, "carol", note_id) is True
        assert notes.unread_count(s, "carol") == 0


def test_rollback_refuses_to_discard_history_without_the_flag(db):
    _migrated(db)
    lid = _listing(db, price=800)
    with db.session() as s:
        monitor.add_watch(s, lid, "dave", target_price_minor=900)
        s.commit()
    with db.session() as s:
        notes.generate(s, "dave")
    with pytest.raises(RuntimeError, match="refusing to drop"):
        notes.rollback(db.engine, apply=True)
    assert inspect(db.engine).has_table(notes.TABLE)                 # nothing was dropped
    assert "DRY RUN" in notes.rollback(db.engine, apply=False)
    assert notes.rollback(db.engine, apply=True, discard_rows=True).startswith("dropped table notifications")
    assert not inspect(db.engine).has_table(notes.TABLE)


def test_web_routes_answer_503_until_an_operator_migrates(db):
    client = TestClient(create_app(db), base_url="http://testserver")
    assert client.get("/").status_code == 200                        # owner cookie
    r = client.get("/api/v1/notifications")
    assert r.status_code == 503 and "notify-migrate" in r.json()["error"]
    page = client.get("/notifications")
    assert page.status_code == 200 and "not set up on this database" in page.text
    _migrated(db)
    assert client.get("/api/v1/notifications").json() == {"unread": 0, "items": []}


def test_web_mark_read_is_owner_scoped(db):
    _migrated(db)
    lid = _listing(db, price=800)
    owner_client = TestClient(create_app(db), base_url="http://testserver")
    assert owner_client.get("/").status_code == 200
    with db.session() as s:
        cookie = owner_client.cookies.get("wt_owner")
        monitor.add_watch(s, lid, cookie, target_price_minor=900)
        s.commit()
    assert owner_client.get("/api/v1/notifications").json()["unread"] == 1
    nid = owner_client.get("/api/v1/notifications").json()["items"][0]["id"]
    other = TestClient(create_app(db), base_url="http://testserver")
    assert other.get("/").status_code == 200
    assert other.post(f"/api/v1/notifications/{nid}/read", headers={"Origin": "http://testserver"}).status_code == 404
    assert owner_client.post(f"/api/v1/notifications/{nid}/read", headers={"Origin": "http://testserver"}).status_code == 200


def test_cli_refuses_the_configured_live_database(tmp_path, monkeypatch):
    live = tmp_path / "live.db"
    live.write_bytes(b"")
    monkeypatch.setattr(config, "DB_URL", f"sqlite:///{live}")
    with pytest.raises(SystemExit, match="live database"):
        cli.main(["notify-migrate", "--db", str(live), "--apply"])


def test_a_watch_paused_by_a_currency_change_raises_no_alert(db):
    _migrated(db)
    lid = _listing(db, price=800)                       # USD listing
    with db.session() as s:
        monitor.add_watch(s, lid, "erin", target_price_minor=900)
        s.commit()
    with db.session() as s:
        l = s.get(Listing, lid)
        l.currency, l.price_minor = "JPY", 500          # now yen: the monitor pauses comparisons
        s.commit()
    with db.session() as s:
        assert notes.generate(s, "erin") == 0
        assert notes.unread_count(s, "erin") == 0


def test_a_restock_seen_again_on_later_crawls_adds_one_row(db):
    from datetime import timedelta
    from watchtower.models import utcnow
    _migrated(db)
    lid = _listing(db, availability="out_of_stock")
    t0 = utcnow()
    _obs(db, lid, "out_of_stock", t0 - timedelta(hours=1))
    with db.session() as s:
        monitor.add_watch(s, lid, "frank", target_price_minor=None, notify_restock=True)
        s.commit()
    _obs(db, lid, "in_stock", t0 + timedelta(minutes=1))            # the restock
    for minutes in (2, 3, 4):                                       # later crawls still see it in stock
        _obs(db, lid, "in_stock", t0 + timedelta(minutes=minutes))
        with db.session() as s:
            notes.generate(s, "frank")
    with db.session() as s:
        assert notes.unread_count(s, "frank") == 1


def test_the_cli_guard_refuses_a_hard_link_to_the_live_database(tmp_path, monkeypatch):
    import os
    live = tmp_path / "live.db"
    live.write_bytes(b"")
    alias = tmp_path / "copy-but-really-live.db"
    os.link(live, alias)
    monkeypatch.setattr(config, "DB_URL", f"sqlite:///{live}")
    with pytest.raises(SystemExit, match="live database"):
        cli.main(["notify-migrate", "--db", str(alias), "--apply"])


def test_alerts_page_renders_unread_alerts_with_mark_read_controls(db):
    _migrated(db)
    lid = _listing(db, price=800)
    client = TestClient(create_app(db), base_url="http://testserver")
    assert client.get("/").status_code == 200
    with db.session() as s:
        monitor.add_watch(s, lid, client.cookies.get("wt_owner"), target_price_minor=900)
        s.commit()
    page = client.get("/notifications")
    assert page.status_code == 200
    assert "Sensor board: at or below your target" in page.text
    assert "1</b> unread" in page.text or ">1</b>" in page.text
    assert "Mark read" in page.text and "Mark all read" in page.text


def test_no_alert_for_a_listing_no_longer_found_at_the_source(db):
    _migrated(db)
    lid = _listing(db, price=800)
    with db.session() as s:
        monitor.add_watch(s, lid, "hank", target_price_minor=900)
        s.commit()
    with db.session() as s:
        s.get(Listing, lid).active = False          # removed after a complete crawl
        s.commit()
    with db.session() as s:
        assert notes.generate(s, "hank") == 0       # the last price seen is not a current price
        assert notes.unread_count(s, "hank") == 0
