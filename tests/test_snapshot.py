"""Loop F, iteration 1: a database backup must contain every COMMITTED row. The real DB runs in WAL mode, so committed
pages can live only in `watchtower.db-wal`; a plain file copy of `watchtower.db` silently loses them (found on the real
DB in mission 004: the copy had 71 pending review candidates, the live DB 23)."""
import shutil
import sqlite3

import pytest

from watchtower.db import SnapshotError, snapshot


def _wal_db(path):
    c = sqlite3.connect(path)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA wal_autocheckpoint=0")  # keep committed pages in the -wal file, as on the real DB
    c.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    c.executemany("INSERT INTO t (v) VALUES (?)", [("x",)] * 50)
    c.commit()
    return c  # keep open so the WAL is not checkpointed on close


def test_plain_copy_of_a_wal_database_loses_committed_rows(tmp_path):
    src = tmp_path / "live.db"
    keep = _wal_db(src)
    shutil.copyfile(src, tmp_path / "naive.db")
    n = sqlite3.connect(tmp_path / "naive.db").execute("SELECT name FROM sqlite_master WHERE name='t'").fetchall()
    assert n == []  # the documented failure mode this tool exists to prevent
    keep.close()


def test_snapshot_contains_committed_wal_rows_and_is_verified(tmp_path):
    src = tmp_path / "live.db"
    keep = _wal_db(src)
    before = (src.read_bytes(), (tmp_path / "live.db-wal").read_bytes())
    report = snapshot(src, tmp_path / "snap.db")
    assert report["integrity"] == "ok" and report["tables"]["t"] == 50
    assert sqlite3.connect(tmp_path / "snap.db").execute("SELECT count(*) FROM t").fetchone() == (50,)
    assert (src.read_bytes(), (tmp_path / "live.db-wal").read_bytes()) == before  # the source is never modified
    keep.close()


def test_snapshot_refuses_to_overwrite_and_needs_an_existing_source(tmp_path):
    src = tmp_path / "live.db"
    _wal_db(src).close()
    (tmp_path / "exists.db").write_bytes(b"keep me")
    with pytest.raises(SnapshotError):
        snapshot(src, tmp_path / "exists.db")
    assert (tmp_path / "exists.db").read_bytes() == b"keep me"
    with pytest.raises(SnapshotError):
        snapshot(tmp_path / "missing.db", tmp_path / "out.db")
    assert not (tmp_path / "missing.db").exists()  # opening read-only must not create an empty source file



def test_cli_backup_does_not_initialise_or_write_the_source(tmp_path, monkeypatch, capsys):
    from watchtower import cli, config
    src = tmp_path / "live.db"
    keep = _wal_db(src)
    before = (src.read_bytes(), (tmp_path / "live.db-wal").read_bytes())
    monkeypatch.setattr(config, "DB_URL", f"sqlite:///{src}")
    assert cli.main(["backup", "--out", str(tmp_path / "b.db")]) == 0
    assert "backup ok" in capsys.readouterr().out
    assert (src.read_bytes(), (tmp_path / "live.db-wal").read_bytes()) == before  # no tables created, no policy rows
    assert cli.main(["backup", "--out", str(tmp_path / "b.db")]) == 1  # never overwrites
    keep.close()
