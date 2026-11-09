"""The live-database guard. It must hold for every SQLite spelling Database accepts, for links to the live file, and
it must fail closed when the configured SQLite file cannot be located. Server databases have no file to compare."""
import os

import pytest

from watchtower import cli, config


@pytest.fixture
def live(tmp_path, monkeypatch):
    f = tmp_path / "live.db"
    f.write_bytes(b"")
    other = tmp_path / "copy.db"
    other.write_bytes(b"")
    return f, other


@pytest.mark.parametrize("template", [
    "sqlite:///{p}", "sqlite+pysqlite:///{p}", "sqlite:///{p}?timeout=30", "sqlite+pysqlite:///{p}?timeout=30"])
def test_every_sqlite_spelling_protects_the_live_file(live, monkeypatch, template):
    f, other = live
    monkeypatch.setattr(config, "DB_URL", template.format(p=f))
    assert cli._is_live_db(f) is True
    assert cli._is_live_db(other) is False


def test_a_hard_link_and_a_symlink_to_the_live_file_are_the_live_file(live, monkeypatch, tmp_path):
    f, _ = live
    monkeypatch.setattr(config, "DB_URL", f"sqlite:///{f}")
    hard = tmp_path / "hard.db"
    os.link(f, hard)
    sym = tmp_path / "sym.db"
    sym.symlink_to(f)
    assert cli._is_live_db(hard) is True
    assert cli._is_live_db(sym) is True


def test_a_relative_path_resolves_to_the_live_file(live, monkeypatch, tmp_path):
    f, _ = live
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "DB_URL", "sqlite:///./live.db")
    assert cli._is_live_db("live.db") is True


def test_a_missing_live_file_is_still_protected_by_its_path(tmp_path, monkeypatch):
    ghost = tmp_path / "not-yet.db"
    monkeypatch.setattr(config, "DB_URL", f"sqlite:///{ghost}")
    assert cli._is_live_db(ghost) is True
    assert cli._is_live_db(tmp_path / "copy.db") is False


@pytest.mark.parametrize("url", ["sqlite:///:memory:", "sqlite://", "sqlite:///file:x.db?uri=true"])
def test_an_unlocatable_sqlite_target_fails_closed(url, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_URL", url)
    assert cli._is_live_db(tmp_path / "anything.db") is True


def test_a_server_database_has_no_file_to_protect(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_URL", "postgresql://user@localhost/watchtower")
    assert cli._is_live_db(tmp_path / "anything.db") is False


def test_the_cli_refuses_to_migrate_the_live_file_under_any_spelling(live, monkeypatch):
    f, _ = live
    monkeypatch.setattr(config, "DB_URL", f"sqlite+pysqlite:///{f}?timeout=30")
    with pytest.raises(SystemExit, match="configured live database"):
        cli.main(["notify-migrate", "--db", str(f), "--apply"])
    assert f.read_bytes() == b""  # untouched
