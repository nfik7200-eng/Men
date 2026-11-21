"""A small, bounded, in-process job scheduler.

One daemon thread runs due jobs one at a time, so jobs never overlap and never run in parallel
with each other. Each execution is recorded in ``job_runs`` (health survives a restart); a run
left ``running`` by a crash is closed as an error on start. A failing job backs off exponentially
up to a cap instead of hammering. A job that finds its lock held reports ``skipped:``, which is not
a failure.

The scheduler is off by default (``workers.enabled``). Jobs that contact providers declare it
(``contacts_providers``) and are only registered when separately enabled.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, insert, select, update

from bip.core.logging import get_logger
from bip.db import schema
from bip.db.repository import Repository
from bip.db.types import utcnow
from bip.security.secrets import redact_text

log = get_logger("bip.ops.scheduler")

SKIPPED_RETRY = timedelta(minutes=1)
"""How soon a job that found the ingestion lock held is tried again. Other skips (a full disk) wait
the normal interval."""
FIRST_RUN_DELAY = timedelta(seconds=60)


@dataclass(frozen=True, slots=True)
class Job:
    name: str
    interval: timedelta
    run: Callable[[], str]
    description: str = ""
    contacts_providers: bool = False


@dataclass(slots=True)
class _State:
    next_due: datetime
    failures: int = 0
    running: bool = False


class Scheduler:
    def __init__(
        self,
        repo: Repository,
        jobs: Sequence[Job],
        *,
        clock: Callable[[], datetime] = utcnow,
        poll_seconds: float = 30.0,
        max_backoff: timedelta = timedelta(hours=24),
    ) -> None:
        self._repo = repo
        self._jobs = {j.name: j for j in jobs}
        self._clock = clock
        self._poll = poll_seconds
        self._max_backoff = max_backoff
        self._state: dict[str, _State] = {}
        self._exec_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._initialise()

    # ------------------------------------------------------------------ lifecycle

    def _initialise(self) -> None:
        r = schema.job_runs
        now = self._clock()
        with self._repo.transaction() as conn:
            conn.execute(
                update(r)
                .where(r.c.status == "running")
                .values(status="error", finished_at=now, detail="interrupted by a restart")
            )
            for name, job in self._jobs.items():
                last = conn.execute(
                    select(func.max(r.c.finished_at)).where(
                        r.c.job == name, r.c.status != "running"
                    )
                ).scalar_one()
                due = last + job.interval if last is not None else now + FIRST_RUN_DELAY
                self._state[name] = _State(next_due=max(due, now) if last is None else due)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="bip-scheduler", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stop.wait(self._poll):
            try:
                self.tick()
            except Exception:
                log.exception("scheduler tick failed")

    # ------------------------------------------------------------------ execution

    def tick(self) -> list[str]:
        """Run every job that is due, one at a time. Returns the names that ran."""
        ran: list[str] = []
        for name, job in self._jobs.items():
            if self._clock() >= self._state[name].next_due and self._execute(job):
                ran.append(name)
        return ran

    def run_now(self, name: str) -> dict[str, Any]:
        job = self._jobs.get(name)
        if job is None:
            raise KeyError(name)
        if not self._execute(job):
            return {"job": name, "status": "busy", "detail": "another job is running"}
        return next(s for s in self.status() if s["job"] == name)["last_run"] or {}

    def _execute(self, job: Job) -> bool:
        if not self._exec_lock.acquire(blocking=False):
            return False
        state = self._state[job.name]
        try:
            state.running = True
            started = self._clock()
            run_id = self._open(job.name, started)
            status, detail = "ok", ""
            try:
                detail = job.run()
                if detail.startswith("skipped:"):
                    status = "skipped"
            except Exception as exc:  # a job failing must never stop the scheduler
                log.exception("job %s failed", job.name)
                status, detail = "error", f"{type(exc).__name__}: {redact_text(str(exc))}"
            self._close(run_id, status, detail)
            now = self._clock()
            if status == "error":
                state.failures += 1
                delay = min(job.interval * (2**state.failures), self._max_backoff)
            elif status == "skipped" and "in progress" in detail:
                # A busy ingestion lock is not this job's failure, and waiting a whole interval
                # would let live collection fall behind while a historical run held the lock.
                delay = min(job.interval, SKIPPED_RETRY)
            else:
                state.failures = 0
                delay = job.interval
            state.next_due = now + delay
            return True
        finally:
            state.running = False
            self._exec_lock.release()

    def _open(self, job: str, started: datetime) -> int:
        with self._repo.transaction() as conn:
            pk = conn.execute(
                insert(schema.job_runs).values(job=job, started_at=started, status="running")
            ).inserted_primary_key
            assert pk is not None  # noqa: S101
            return int(pk[0])

    def _close(self, run_id: int, status: str, detail: str) -> None:
        with self._repo.transaction() as conn:
            conn.execute(
                update(schema.job_runs)
                .where(schema.job_runs.c.id == run_id)
                .values(status=status, finished_at=self._clock(), detail=detail[:300])
            )

    # ------------------------------------------------------------------ reporting

    def status(self) -> list[dict[str, Any]]:
        r = schema.job_runs
        out: list[dict[str, Any]] = []
        with self._repo.transaction() as conn:
            for name, job in self._jobs.items():
                row = conn.execute(
                    select(r).where(r.c.job == name).order_by(r.c.id.desc()).limit(1)
                ).first()
                state = self._state[name]
                out.append(
                    {
                        "job": name,
                        "description": job.description,
                        "interval_seconds": int(job.interval.total_seconds()),
                        "contacts_providers": job.contacts_providers,
                        "running": state.running,
                        "consecutive_failures": state.failures,
                        "next_due": state.next_due.isoformat(),
                        "last_run": None
                        if row is None
                        else {
                            "status": row.status,
                            "started_at": row.started_at.isoformat(),
                            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
                            "detail": row.detail,
                        },
                    }
                )
        return out

    def prune_history(self, older_than: datetime) -> int:
        with self._repo.transaction() as conn:
            result = conn.execute(
                delete(schema.job_runs).where(schema.job_runs.c.started_at < older_than)
            )
        return int(result.rowcount or 0)
