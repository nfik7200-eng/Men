"""Metrics, backups and the scheduler, without the HTTP layer."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, insert, text

from bip.config import load_settings
from bip.config.settings import Settings
from bip.db import schema
from bip.db.engine import create_db_engine
from bip.db.repository import Repository
from bip.ops.backup import BackupError, BackupService
from bip.ops.metrics import MAX_ROUTES, ApiMetrics, normalise
from bip.ops.scheduler import Job, Scheduler

T0 = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw: float) -> None:
        self.now += timedelta(**kw)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return load_settings(data_root=tmp_path / "data", env="test")


@pytest.fixture
def engine(settings: Settings) -> Iterator[Engine]:
    e = create_db_engine(settings)
    yield e
    e.dispose()


# ------------------------------------------------------------------ metrics


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/api/v1/wallets/ethereum/0x" + "ab" * 20, "/api/v1/wallets/ethereum/{id}"),
        ("/api/v1/signals/42", "/api/v1/signals/{id}"),
        ("/api/v1/investigations/7/findings", "/api/v1/investigations/{id}/findings"),
        (
            "/api/v1/wallets/solana/" + "4vJ9JU1bJJE96FWSJKvHsmmFADCg4gpZQff4P3bkLKi",
            "/api/v1/wallets/solana/{id}",
        ),
        ("/api/v1/health", "/api/v1/health"),
    ],
)
def test_paths_are_normalised(raw: str, expected: str) -> None:
    assert normalise(raw) == expected


def test_metrics_counts_buckets_and_stays_bounded() -> None:
    m = ApiMetrics()
    m.record("GET", "/api/v1/signals/1", 200, 12)
    m.record("GET", "/api/v1/signals/2", 200, 300)
    m.record("POST", "/api/v1/auth/login", 401, 80)
    m.record("GET", "/api/v1/x", 429, 1)
    m.record("GET", "/api/v1/y", 500, 6000)
    snap = m.snapshot()
    assert snap["requests"] == 5
    assert snap["by_status_class"] == {"2xx": 2, "3xx": 0, "4xx": 2, "5xx": 1}
    assert (snap["throttled_429"], snap["unauthorised_401_403"]) == (1, 1)
    assert snap["latency_buckets"]["<50ms"] == 2
    assert snap["latency_buckets"][">=5000ms"] == 1
    routes = {r["route"]: r for r in snap["slowest_routes"]}
    assert routes["GET /api/v1/signals/{id}"]["count"] == 2  # ids collapsed into one route
    assert routes["GET /api/v1/y"]["server_errors"] == 1
    for i in range(MAX_ROUTES + 50):
        m.record("GET", f"/api/v1/route{i}x", 200, 1)
    assert m.snapshot(top=1000)["routes_not_tracked"] > 0
    assert len(m.snapshot(top=1000)["slowest_routes"]) <= MAX_ROUTES


# ------------------------------------------------------------------ backups


def seed(engine: Engine) -> None:
    Repository(engine).create_watchlist("w", None)


def test_backup_is_a_verified_private_copy_with_the_data(
    settings: Settings, engine: Engine
) -> None:
    seed(engine)
    svc = BackupService(settings, clock=Clock())
    info = svc.create()
    path = svc.directory / info.name
    assert info.name == "bip-20261108T120000Z.sqlite3"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert oct(svc.directory.stat().st_mode & 0o777) == "0o700"
    assert svc.verify(info.name) is True
    with closing(sqlite3.connect(path)) as copy:
        assert copy.execute("SELECT name FROM watchlists").fetchall() == [("w",)]
    assert not list(svc.directory.glob("*.partial"))


def test_backup_names_do_not_collide(settings: Settings, engine: Engine) -> None:
    svc = BackupService(settings, clock=Clock())
    names = {svc.create().name for _ in range(3)}
    assert len(names) == 3


def test_backups_are_listed_newest_first_and_pruned(settings: Settings, engine: Engine) -> None:
    clock = Clock()
    svc = BackupService(settings, clock=clock)
    for _ in range(4):
        svc.create()
        clock.advance(hours=1)
    listed = svc.list()
    assert [b.created_at for b in listed] == sorted((b.created_at for b in listed), reverse=True)
    removed = svc.prune(keep=2)
    assert len(removed) == 2
    assert [b.name for b in svc.list()] == [b.name for b in listed[:2]]
    assert svc.prune(keep=0) != [] or len(svc.list()) == 1  # never deletes the last one
    assert len(svc.list()) == 1


def test_backup_is_refused_without_room(settings: Settings, engine: Engine) -> None:
    svc = BackupService(settings, free_bytes=lambda _p: 1024)
    with pytest.raises(BackupError, match="not enough free disk space"):
        svc.create()
    assert svc.list() == []


def test_a_corrupt_backup_fails_verification(settings: Settings, engine: Engine) -> None:
    svc = BackupService(settings, clock=Clock())
    info = svc.create()
    path = svc.directory / info.name
    data = bytearray(path.read_bytes())
    data[100:300] = b"\x00" * 200
    path.write_bytes(bytes(data))
    assert svc.verify(info.name) is False


def test_backup_name_validation(settings: Settings, engine: Engine) -> None:
    svc = BackupService(settings)
    for bad in ("../../etc/passwd", "bip.sqlite3", "bip-1.sqlite3", ""):
        with pytest.raises(BackupError, match="not a backup file name"):
            svc.verify(bad)
    with pytest.raises(BackupError, match="no such backup"):
        svc.verify("bip-20200101T000000Z.sqlite3")


def test_backup_without_a_database(settings: Settings) -> None:
    with pytest.raises(BackupError, match="no database"):
        BackupService(settings).create()


# ------------------------------------------------------------------ scheduler


def _note(sink: list[Any], item: Any, result: str) -> str:
    sink.append(item)
    return result


def make(engine: Engine, clock: Clock, *jobs: Job, **kw: object) -> Scheduler:
    return Scheduler(Repository(engine), list(jobs), clock=clock, **kw)  # type: ignore[arg-type]


def test_due_jobs_run_in_order_and_are_recorded(engine: Engine) -> None:
    clock = Clock()
    calls: list[str] = []
    a = Job("a", timedelta(minutes=10), lambda: _note(calls, "a", "did a"))
    b = Job("b", timedelta(minutes=30), lambda: _note(calls, "b", "did b"))
    sched = make(engine, clock, a, b)
    assert sched.tick() == []  # nothing is due during the first-run delay
    clock.advance(seconds=61)
    assert sched.tick() == ["a", "b"]
    assert calls == ["a", "b"]
    clock.advance(minutes=11)
    assert sched.tick() == ["a"]  # b's interval is longer
    status = {s["job"]: s for s in sched.status()}
    assert status["a"]["last_run"]["status"] == "ok"
    assert status["a"]["last_run"]["detail"] == "did a"
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM job_runs")).scalar_one() == 3


def test_failures_back_off_exponentially_up_to_a_cap(engine: Engine) -> None:
    clock = Clock()

    def boom() -> str:
        raise RuntimeError("secret=hunter2hunter2 exploded")

    sched = make(
        engine, clock, Job("flaky", timedelta(minutes=10), boom), max_backoff=timedelta(hours=1)
    )
    clock.advance(seconds=61)
    delays: list[float] = []
    for _ in range(5):
        sched.tick()
        due = datetime.fromisoformat(sched.status()[0]["next_due"])
        delays.append((due - clock.now).total_seconds() / 60)
        clock.now = due
    assert delays == [20, 40, 60, 60, 60]  # 10 * 2**n, capped at 1 hour
    last = sched.status()[0]
    assert last["consecutive_failures"] == 5
    assert last["last_run"]["status"] == "error"
    assert "hunter2hunter2" not in last["last_run"]["detail"]  # secrets are redacted


def test_success_resets_the_failure_count_and_skips_are_not_failures(engine: Engine) -> None:
    clock = Clock()
    outcomes = iter(["boom", "skipped: busy", "fine"])

    def run() -> str:
        value = next(outcomes)
        if value == "boom":
            raise RuntimeError("x")
        return value

    sched = make(engine, clock, Job("j", timedelta(minutes=5), run))
    clock.advance(seconds=61)
    sched.tick()
    assert sched.status()[0]["consecutive_failures"] == 1
    for expected in ("skipped", "ok"):
        clock.now = datetime.fromisoformat(sched.status()[0]["next_due"])
        sched.tick()
        assert sched.status()[0]["last_run"]["status"] == expected
    assert sched.status()[0]["consecutive_failures"] == 0


def test_one_failing_job_does_not_stop_the_others(engine: Engine) -> None:
    clock = Clock()
    ran: list[int] = []

    def boom() -> str:
        raise RuntimeError("x")

    sched = make(
        engine,
        clock,
        Job("bad", timedelta(minutes=5), boom),
        Job("good", timedelta(minutes=5), lambda: _note(ran, 1, "ok")),
    )
    clock.advance(seconds=61)
    assert sched.tick() == ["bad", "good"]
    assert ran == [1]


def test_a_run_left_running_by_a_crash_is_closed_on_restart(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(schema.job_runs).values(
                job="j", started_at=T0 - timedelta(hours=1), status="running"
            )
        )
    sched = make(engine, Clock(), Job("j", timedelta(minutes=5), lambda: "ok"))
    last = sched.status()[0]["last_run"]
    assert last["status"] == "error"
    assert last["detail"] == "interrupted by a restart"


def test_next_run_follows_the_recorded_history(engine: Engine) -> None:
    clock = Clock()
    first = make(engine, clock, Job("j", timedelta(hours=6), lambda: "ok"))
    clock.advance(seconds=61)
    first.tick()
    clock.advance(hours=1)
    second = make(engine, clock, Job("j", timedelta(hours=6), lambda: "ok"))  # "restart"
    due = datetime.fromisoformat(second.status()[0]["next_due"])
    assert due == T0 + timedelta(seconds=61) + timedelta(hours=6)


def test_run_now_and_unknown_job(engine: Engine) -> None:
    sched = make(engine, Clock(), Job("j", timedelta(days=1), lambda: "manual"))
    assert sched.run_now("j")["detail"] == "manual"
    with pytest.raises(KeyError):
        sched.run_now("nope")


def test_jobs_never_overlap(engine: Engine) -> None:
    started, release = threading.Event(), threading.Event()

    def slow() -> str:
        started.set()
        release.wait(5)
        return "done"

    sched = make(
        engine,
        Clock(),
        Job("slow", timedelta(days=1), slow),
        Job("other", timedelta(days=1), lambda: "x"),
    )
    t = threading.Thread(target=sched.run_now, args=("slow",))
    t.start()
    assert started.wait(5)
    assert sched.run_now("other")["status"] == "busy"
    release.set()
    t.join(5)


def test_the_thread_runs_jobs_and_stops_cleanly(engine: Engine) -> None:
    ran = threading.Event()
    clock = Clock()
    sched = make(
        engine, clock, Job("j", timedelta(seconds=1), lambda: ran.set() or "ok"), poll_seconds=0.05
    )
    clock.advance(seconds=61)
    sched.start()
    assert ran.wait(5), "the scheduler thread never ran the job"
    assert sched.running
    sched.stop()
    assert not sched.running
    assert not [t for t in threading.enumerate() if t.name == "bip-scheduler"]
    _ = (os, time)


def test_same_second_backups_sort_by_creation_and_pruning_keeps_the_newest(
    settings: Settings,
    engine: Engine,
) -> None:
    """Regression: 'bip-X.sqlite3' sorted after 'bip-X-2.sqlite3', so prune kept the oldest."""
    svc = BackupService(settings, clock=Clock())
    created = [svc.create().name for _ in range(3)]
    assert [b.name for b in svc.list()] == list(reversed(created))
    svc.prune(keep=1)
    assert [b.name for b in svc.list()] == [created[-1]]
