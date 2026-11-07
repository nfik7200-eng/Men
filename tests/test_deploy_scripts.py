"""Independent review of the deployment package: each defect is reproduced in a TEMP prefix (nothing is installed on the host,
the real DB is never touched). Needs bash; skipped when it is missing."""
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")


def sh(cmd, env=None, cwd=None, umask=None):
    e = {**os.environ, "WT_ALLOW_NONROOT": "1", "WT_NO_SYSTEMCTL": "1", "WT_NO_SU": "1", "WT_PYTHON": sys.executable, **(env or {})}
    pre = f"umask {umask}; " if umask else ""
    return subprocess.run(["bash", "-c", pre + cmd], env=e, cwd=cwd, capture_output=True, text=True, timeout=180)


def fake_source(tmp, with_pytest_ini=True):
    src = tmp / "src"
    (src / "watchtower").mkdir(parents=True)
    (src / "watchtower" / "__init__.py").write_text("")
    (src / "main.py").write_text("app = None\n")
    (src / "requirements-prod.txt").write_text("")  # nothing to download: the venv + smoke import are what is exercised
    if with_pytest_ini:
        (src / "pytest.ini").write_text("[pytest]\n")
    return src


def test_m1_release_is_readable_by_the_service_user_whatever_the_callers_umask(tmp_path):
    src = fake_source(tmp_path)
    prefix = tmp_path / "opt"
    r = sh(f'. "{DEPLOY}/lib.sh"; DRY=0; mkdir -p "$WT_PREFIX/releases"; make_release "{src}" 20260101T000000Z',
           env={"WT_PREFIX": str(prefix)}, umask="077")
    assert r.returncode == 0, r.stdout + r.stderr
    rel = prefix / "releases" / "20260101T000000Z"
    assert oct(rel.stat().st_mode & 0o755) == oct(0o755) and (rel / "main.py").stat().st_mode & 0o044 == 0o044
    assert (rel / ".venv" / "bin" / "python").exists() and oct((rel / ".venv").stat().st_mode & 0o005) == oct(0o005)


def test_m2_missing_optional_files_do_not_abort_the_release_silently(tmp_path):
    src = fake_source(tmp_path, with_pytest_ini=False)
    prefix = tmp_path / "opt"
    r = sh(f'. "{DEPLOY}/lib.sh"; DRY=0; mkdir -p "$WT_PREFIX/releases"; make_release "{src}" 20260101T000000Z', env={"WT_PREFIX": str(prefix)})
    assert r.returncode == 0, r.stdout + r.stderr
    assert (prefix / "releases" / "20260101T000000Z" / "main.py").exists()


def _managed_prefix(tmp):
    prefix = tmp / "opt"
    good = prefix / "releases" / "20260101T000000Z"
    (good / ".venv" / "bin").mkdir(parents=True)
    (good / "main.py").write_text("")
    py = good / ".venv" / "bin" / "python"
    py.write_text("#!/bin/sh\n")
    py.chmod(0o755)
    other = prefix / "releases" / "20260102T000000Z"
    (other / ".venv" / "bin").mkdir(parents=True)
    (other / "main.py").write_text("")
    (other / ".venv" / "bin" / "python").write_text("#!/bin/sh\n")
    (other / ".venv" / "bin" / "python").chmod(0o755)
    (prefix / ".watchtower-deploy").write_text("x")
    os.symlink("releases/20260102T000000Z", prefix / "current")
    os.symlink("releases/20260101T000000Z", prefix / "previous")
    return prefix


def test_m3_rollback_refuses_an_incomplete_release(tmp_path):
    prefix = _managed_prefix(tmp_path)
    (prefix / "releases" / "20250101T000000Z").mkdir()  # an empty / half-built release dir
    r = sh(f'"{DEPLOY}/rollback.sh" --to 20250101T000000Z --apply', env={"WT_PREFIX": str(prefix)})
    assert r.returncode != 0 and "incomplete" in (r.stdout + r.stderr)
    assert os.readlink(prefix / "current") == "releases/20260102T000000Z"  # untouched
    ok = sh(f'"{DEPLOY}/rollback.sh" --apply', env={"WT_PREFIX": str(prefix)})  # the complete previous release is accepted
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert os.readlink(prefix / "current") == "releases/20260101T000000Z"


def test_m4_restore_waits_for_every_unit_that_may_hold_the_database():
    lib = (DEPLOY / "lib.sh").read_text()
    restore = (DEPLOY / "restore.sh").read_text()
    for unit in ("watchtower-web.service", "watchtower-collect.service", "watchtower-backup.service"):
        assert unit in lib.split("active_db_unit()")[1].split("\n}\n")[0]
    assert "active_db_unit" in restore and "service_active" not in restore


def _wal_db(path):
    c = sqlite3.connect(path)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE t(x)")
    c.commit()
    c.close()


def test_l1_backup_dry_run_creates_nothing(tmp_path):
    live = tmp_path / "state" / "live.db"
    live.parent.mkdir()
    _wal_db(live)
    out = tmp_path / "state" / "backups"
    r = sh(f'"{DEPLOY}/backup.sh" --dry-run --dir "{out}"',
           env={"WATCHTOWER_DB_URL": f"sqlite:///{live}", "WT_APP_DIR": str(ROOT), "WT_PYTHON_BIN": sys.executable, "WT_STATE": str(tmp_path / "state")})
    assert r.returncode == 0, r.stdout + r.stderr
    assert not out.exists()


def test_l3_snapshot_files_are_private(tmp_path):
    from watchtower.db import snapshot
    live = tmp_path / "live.db"
    _wal_db(live)
    snapshot(live, tmp_path / "snap.db")
    assert (tmp_path / "snap.db").stat().st_mode & 0o077 == 0


def _stub_systemctl(tmp):
    bindir = tmp / "bin"
    bindir.mkdir()
    (bindir / "systemctl").write_text("#!/bin/sh\nexit 0\n")  # restart succeeds; health (curl) will fail: nothing listens
    (bindir / "systemctl").chmod(0o755)
    return bindir


def test_rollback_auto_revert_restores_both_symlinks(tmp_path):
    """Advisor review: after a failed health check the revert left current == previous, so the NEXT rollback died with 'already on'."""
    prefix = _managed_prefix(tmp_path)
    bindir = _stub_systemctl(tmp_path)
    r = sh(f'"{DEPLOY}/rollback.sh" --apply',
           env={"WT_PREFIX": str(prefix), "WT_NO_SYSTEMCTL": "0", "WT_HEALTH_TRIES": "1", "WT_PORT": "1", "PATH": f"{bindir}:{os.environ['PATH']}"})
    assert r.returncode == 8, r.stdout + r.stderr
    assert os.readlink(prefix / "current") == "releases/20260102T000000Z"   # switched back
    assert os.readlink(prefix / "previous") == "releases/20260101T000000Z"  # and `previous` is again the release we can roll back to


def test_b5_a_hardlink_to_the_live_database_is_refused_by_the_snapshot_guard(tmp_path, monkeypatch):
    from watchtower import cli, config
    live = tmp_path / "live.db"
    _wal_db(live)
    link = tmp_path / "alias.db"
    os.link(live, link)  # same inode, different path: a resolved-path comparison would let it through
    monkeypatch.setattr(config, "DB_URL", f"sqlite:///{live}")
    assert cli._snapshot_copy(str(link), "coverage") is None
