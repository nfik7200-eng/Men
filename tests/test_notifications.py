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


def test_back_in_stock_alert_fires_once_for_a_restock(db):
    _migrated(db)
    lid = _listing(db, availability="out_of_stock")
    with db.session() as s:
        monitor.add_watch(s, lid, "bob", target_price_minor=None, notify_restock=True)
        s.commit()
    with db.session() as s:
        s.get(Listing, lid).availability = "in_stock"
        s.commit()
    with db.session() as s:
        assert notes.generate(s, "bob") == 1
        assert notes.generate(s, "bob") == 0
        assert notes.list_for(s, "bob")[0].kind == "back_in_stock"


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
