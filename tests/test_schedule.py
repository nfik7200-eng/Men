"""Mission 005: the scheduler exists, does nothing by default, and never bypasses the policy gate. It installs nothing:
an operator (cron/systemd/anything) may call `schedule run-due` once they have approved it."""
import json
from datetime import datetime, timedelta

import pytest

from conftest import FakeSite
from test_real_sources import NOW, U, _site, live_db  # noqa: F401
from watchtower import cli, config, schedule
from watchtower.models import CollectionRun, Listing, Source


def _cfg(tmp_path, **kw):
    cfg = {"enabled": False, "sources": {"adafruit": {"min_interval_hours": 24, "max_pages": 5},
                                         "sparkfun": {"min_interval_hours": 48, "max_pages": 3}}}
    cfg.update(kw)
    p = tmp_path / "schedule.json"
    p.write_text(json.dumps(cfg))
    return p


def test_the_committed_config_is_disabled(monkeypatch):
    cfg = schedule.load(config.ROOT / "data" / "schedule.json")
    assert cfg["enabled"] is False
    assert set(cfg["sources"]) <= {"adafruit", "sparkfun"}  # only approved live sources may be scheduled
    assert all(v["min_interval_hours"] >= 12 for v in cfg["sources"].values())


def test_nothing_runs_unless_both_the_file_and_the_environment_opt_in(live_db, tmp_path, monkeypatch):
    monkeypatch.delenv("WATCHTOWER_SCHEDULER_ENABLED", raising=False)
    calls = []
    run = lambda db, key, **kw: calls.append(key)
    off = schedule.run_due(live_db, _cfg(tmp_path), now=NOW, runner=run)
    assert calls == [] and off.status == "disabled" and "disabled" in off.message
    on_file = schedule.run_due(live_db, _cfg(tmp_path, enabled=True), now=NOW, runner=run)
    assert calls == [] and on_file.status == "disabled" and "WATCHTOWER_SCHEDULER_ENABLED" in on_file.message
    monkeypatch.setenv("WATCHTOWER_SCHEDULER_ENABLED", "1")
    only_env = schedule.run_due(live_db, _cfg(tmp_path, enabled=False), now=NOW, runner=run)
    assert calls == [] and only_env.status == "disabled"


def test_due_sources_run_oldest_first_once_per_invocation_and_respect_intervals(live_db, tmp_path, monkeypatch):
    monkeypatch.setenv("WATCHTOWER_SCHEDULER_ENABLED", "1")
    monkeypatch.setattr(config, "CONTACT", "crawler@watchtower-research.net")
    with live_db.session() as s:
        s.add(CollectionRun(source_id=s.query(Source).filter_by(key="adafruit").one().id, started_at=NOW - timedelta(hours=30), status="ok"))
        s.add(CollectionRun(source_id=s.query(Source).filter_by(key="sparkfun").one().id, started_at=NOW - timedelta(hours=10), status="ok"))
    calls = []
    res = schedule.run_due(live_db, _cfg(tmp_path, enabled=True), now=NOW, runner=lambda db, key, **kw: calls.append((key, kw)))
    assert [k for k, _ in calls] == ["adafruit"]  # sparkfun ran 10 h ago, its minimum interval is 48 h
    assert calls[0][1]["max_pages"] == 5  # the per-source budget comes from the config
    assert res.ran == ["adafruit"] and res.skipped == {"sparkfun": "ran 10.0 h ago; minimum interval 48 h"}


def test_a_source_without_contact_or_approval_is_skipped_with_its_reason_not_forced(live_db, tmp_path, monkeypatch):
    monkeypatch.setenv("WATCHTOWER_SCHEDULER_ENABLED", "1")
    monkeypatch.setattr(config, "CONTACT", "set-WATCHTOWER_CONTACT")
    res = schedule.run_due(live_db, _cfg(tmp_path, enabled=True), now=NOW)  # real runner -> the policy gate refuses
    assert res.ran == [] and set(res.skipped) == {"adafruit", "sparkfun"}
    assert all("contact" in v.lower() or "not approved" in v.lower() for v in res.skipped.values()), res.skipped
    with live_db.session() as s:
        assert s.query(Listing).count() == 0 and s.query(CollectionRun).count() == 0  # nothing was started


def test_only_known_approved_sources_can_be_scheduled(live_db, tmp_path, monkeypatch):
    monkeypatch.setenv("WATCHTOWER_SCHEDULER_ENABLED", "1")
    cfg = _cfg(tmp_path, enabled=True, sources={"ebay_browse": {"min_interval_hours": 24, "max_pages": 5},
                                                "books_toscrape": {"min_interval_hours": 24, "max_pages": 5}})
    with pytest.raises(schedule.ScheduleError):
        schedule.load(cfg)  # eBay is scaffolding and demo catalogues are not real retail data


def test_intervals_below_the_floor_and_budget_above_the_ceiling_are_rejected(tmp_path):
    with pytest.raises(schedule.ScheduleError):
        schedule.load(_cfg(tmp_path, sources={"adafruit": {"min_interval_hours": 1, "max_pages": 5}}))
    with pytest.raises(schedule.ScheduleError):
        schedule.load(_cfg(tmp_path, sources={"adafruit": {"min_interval_hours": 24, "max_pages": 10**6}}))


def test_cli_schedule_status_and_run_due_when_disabled(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("WATCHTOWER_SCHEDULER_ENABLED", raising=False)
    monkeypatch.setattr(config, "DB_URL", f"sqlite:///{tmp_path / 'w.db'}")
    assert cli.main(["schedule", "run-due", "--config", str(_cfg(tmp_path))]) == 0
    assert "disabled" in capsys.readouterr().out.lower()


def test_a_source_whose_last_run_was_blocked_is_not_retried_by_the_scheduler(live_db, tmp_path, monkeypatch):
    """CLAUDE.md: on 401/403/451 STOP. An unattended job must not re-hit a site that refused us; an operator clears it."""
    monkeypatch.setenv("WATCHTOWER_SCHEDULER_ENABLED", "1")
    with live_db.session() as s:
        s.add(CollectionRun(source_id=s.query(Source).filter_by(key="adafruit").one().id, started_at=NOW - timedelta(days=3), status="blocked"))
    calls = []
    res = schedule.run_due(live_db, _cfg(tmp_path, enabled=True), now=NOW, runner=lambda db, key, **kw: calls.append(key))
    assert "adafruit" not in calls and "blocked" in res.skipped["adafruit"] and "operator" in res.skipped["adafruit"]
    assert calls == ["sparkfun"]


def test_one_crashing_source_does_not_abort_the_others(live_db, tmp_path, monkeypatch):
    monkeypatch.setenv("WATCHTOWER_SCHEDULER_ENABLED", "1")
    def runner(db, key, **kw):
        if key == "adafruit":
            raise RuntimeError("boom")
    res = schedule.run_due(live_db, _cfg(tmp_path, enabled=True), now=NOW, runner=runner)
    assert res.ran == ["sparkfun"] and "RuntimeError" in res.skipped["adafruit"]
