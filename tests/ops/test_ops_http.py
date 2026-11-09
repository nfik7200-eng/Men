"""Operational endpoints and jobs over HTTP, with real stored data and fake providers."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, text

from bip.db import schema
from bip.security.auth import AuthService
from tests.api.conftest import GIB, roomy_guard
from tests.security.helpers import API, PASSWORD, Rig

LONG_AGO = datetime(2025, 1, 1, tzinfo=UTC)


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    r = Rig(tmp_path, mode="disabled")
    yield r
    r.dispose()


def workers(**extra: Any) -> dict[str, Any]:
    return {"workers": {"enabled": True, "poll_seconds": 3600, **extra}}


# ------------------------------------------------------------------ health


def test_detailed_health_reports_state_without_leaking_paths(rig: Rig, tmp_path: Path) -> None:
    with TestClient(rig.app) as c:
        r = c.get(f"{API}/system/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["warnings"] == []
    assert body["database"]["schema_version"] == schema.SCHEMA_VERSION
    assert body["database"]["journal_mode"] == "wal"
    assert body["disk"]["writes"] == "allowed"
    assert body["workers"] == {"enabled": False, "running": False, "jobs": []}
    assert body["backups"]["count"] == 0
    assert body["api"]["requests"] >= 0
    assert body["process"]["threads"] >= 1
    assert str(tmp_path) not in r.text
    assert "sqlite3" not in r.text


def test_low_disk_degrades_and_a_refusing_disk_is_critical(rig: Rig, tmp_path: Path) -> None:
    root = rig.settings.resolved_data_root
    with TestClient(rig.app) as c:
        ctx = rig.app.state.bip
        ctx.disk_guard = roomy_guard(root, free=10 * GIB)  # 10 % free: allowed but low
        low = c.get(f"{API}/system/health").json()
        assert low["status"] == "degraded"
        assert [w["code"] for w in low["warnings"]] == ["disk_low"]
        ctx.disk_guard = roomy_guard(root, free=GIB // 2)  # below the free-space floor
        full = c.get(f"{API}/system/health").json()
        assert full["status"] == "critical"
        assert "storage_refusing_writes" in [w["code"] for w in full["warnings"]]
        assert full["disk"]["writes"] == "refused"


def test_usage_report_counts_the_real_ingestion_runs(rig: Rig) -> None:
    with TestClient(rig.app) as c:
        job = c.post(f"{API}/ingestion/wallet/ethereum/{rig.eth.wallet}?limit=5").json()
        usage = c.get(f"{API}/system/usage", params={"days": 7}).json()
        assert c.get(f"{API}/system/usage", params={"days": 0}).status_code == 422
        assert c.get(f"{API}/system/usage", params={"days": 91}).status_code == 422
    assert usage["total_runs"] == 1
    assert usage["total_rpc_calls"] == job["rpc_calls"] > 0
    day = usage["days"][0]
    assert day["by_chain"] == {"ethereum": job["rpc_calls"]}
    assert sum(day["runs_by_provider"].values()) == len(job["providers"])
    assert "not metered" in usage["note"]
    with TestClient(rig.app) as c:
        health = c.get(f"{API}/system/health").json()
    assert health["ingestion"]["recent_runs"][0]["rpc_calls"] == job["rpc_calls"]


def test_api_metrics_count_requests_and_collapse_ids(rig: Rig) -> None:
    with TestClient(rig.app) as c:
        for n in (1, 2, 3):
            c.get(f"{API}/signals/{n}")  # three 404s on one route
        snap = c.get(f"{API}/system/health").json()["api"]
    routes = {r["route"]: r for r in snap["slowest_routes"]}
    assert routes[f"GET {API}/signals/{{id}}"]["count"] == 3
    assert snap["by_status_class"]["4xx"] >= 3


# ------------------------------------------------------------------ backups


def test_backup_endpoints_enforce_roles_and_retention(tmp_path: Path) -> None:
    r = Rig(tmp_path, settings=workers(backup_keep=2, poll_seconds=3600))
    r.user("root", "admin")
    r.user("ana", "analyst")
    try:
        with TestClient(r.app) as c:
            ana = login(c, "ana")
            assert c.get(f"{API}/system/backups").status_code == 200
            assert c.post(f"{API}/system/backups", headers=ana).status_code == 403
            c.post(f"{API}/auth/logout", headers=ana)
            admin = login(c, "root")
            names: list[str] = []
            for _ in range(3):
                made = c.post(f"{API}/system/backups", headers=admin)
                assert made.status_code == 201, made.text
                names.append(made.json()["name"])
            listing = c.get(f"{API}/system/backups").json()
            assert len(listing["items"]) == 2  # keep=2
            assert listing["items"][0]["name"] == names[-1]
            ok = c.post(f"{API}/system/backups/{names[-1]}/verify", headers=admin)
            assert ok.json() == {"name": names[-1], "integrity_ok": True}
            missing = c.post(
                f"{API}/system/backups/bip-20200101T000000Z.sqlite3/verify", headers=admin
            )
            assert missing.status_code == 404
            traversal = c.post(f"{API}/system/backups/..%2F..%2Fetc%2Fpasswd/verify", headers=admin)
            assert traversal.status_code in (404, 422)
    finally:
        r.dispose()


def login(client: TestClient, user: str) -> dict[str, str]:
    r = client.post(f"{API}/auth/login", json={"username": user, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": r.json()["csrf_token"]}


# ------------------------------------------------------------------ scheduler and jobs


def test_jobs_endpoints_when_workers_are_off(rig: Rig) -> None:
    with TestClient(rig.app) as c:
        assert c.get(f"{API}/system/jobs").json() == {
            "enabled": False,
            "running": False,
            "jobs": [],
        }
        r = c.post(f"{API}/system/jobs/discovery/run")
        assert (r.status_code, r.json()["error"]["code"]) == (409, "workers_disabled")


def test_default_job_set_never_contacts_a_provider(tmp_path: Path) -> None:
    r = Rig(tmp_path, mode="disabled", settings=workers())
    try:
        with TestClient(r.app) as c:
            jobs = c.get(f"{API}/system/jobs").json()
            assert jobs["running"] is True
            by_name = {j["job"]: j for j in jobs["jobs"]}
            assert set(by_name) == {"discovery", "relationships", "maintenance"}
            assert not any(j["contacts_providers"] for j in by_name.values())
            run = c.post(f"{API}/system/jobs/discovery/run").json()
            assert run["status"] == "ok"
            assert c.post(f"{API}/system/jobs/nope/run").status_code == 404
            assert r.eth.calls == []
            assert r.sol.calls == []
    finally:
        r.dispose()


def test_optional_jobs_are_registered_only_when_enabled(tmp_path: Path) -> None:
    r = Rig(
        tmp_path,
        mode="disabled",
        settings=workers(backup_enabled=True, watchlist_refresh_enabled=True),
    )
    try:
        with TestClient(r.app) as c:
            jobs = {j["job"]: j for j in c.get(f"{API}/system/jobs").json()["jobs"]}
        assert set(jobs) == {
            "discovery",
            "relationships",
            "maintenance",
            "backup",
            "watchlist_refresh",
        }
        assert jobs["watchlist_refresh"]["contacts_providers"] is True
        assert "CONTACTS PROVIDERS" in jobs["watchlist_refresh"]["description"]
        assert jobs["backup"]["contacts_providers"] is False
    finally:
        r.dispose()


def test_scheduler_thread_follows_the_app_lifecycle(tmp_path: Path) -> None:
    r = Rig(tmp_path, mode="disabled", settings=workers())
    try:
        with TestClient(r.app):
            scheduler = r.app.state.bip.scheduler
            assert scheduler is not None
            assert scheduler.running
        assert not scheduler.running
    finally:
        r.dispose()


def test_maintenance_applies_retention_without_touching_live_data(tmp_path: Path) -> None:
    r = Rig(tmp_path, mode="disabled", settings=workers())
    try:
        old = AuthService(r.repo, r.settings.auth, clock=lambda: LONG_AGO)
        old.audit("someone", "old.event", None, "ok")  # beyond the 365-day retention
        user = old.create_user("temp", PASSWORD, "viewer")
        old.start_session(user, None)  # expired long ago
        r.auth.audit("someone", "recent.event", None, "ok")
        with r.engine.begin() as conn:
            for status, when in (("dismissed", LONG_AGO), ("open", LONG_AGO)):
                conn.execute(
                    insert(schema.signals).values(
                        signal_key=f"k-{status}",
                        detector_id="d",
                        detector_version="1",
                        family="f",
                        chain="ethereum",
                        subject_kind="wallet",
                        subject_ref="0x1",
                        title="t",
                        severity="low",
                        confidence=0.1,
                        score=1.0,
                        status=status,
                        explanation_json="{}",
                        evidence_json="[]",
                        coverage_json="{}",
                        observed_at=when,
                        first_seen_at=when,
                        last_seen_at=when,
                        occurrences=1,
                    )
                )
            conn.execute(
                insert(schema.job_runs).values(job="old", started_at=LONG_AGO, status="ok")
            )
        with TestClient(r.app) as c:
            result = c.post(f"{API}/system/jobs/maintenance/run").json()
        assert result["status"] == "ok", result
        with r.engine.connect() as conn:
            actions = set(conn.execute(text("SELECT action FROM audit_log")).scalars())
            signals = set(conn.execute(text("SELECT status FROM signals")).scalars())
            sessions = conn.execute(text("SELECT COUNT(*) FROM sessions")).scalar_one()
            history = set(conn.execute(text("SELECT job FROM job_runs")).scalars())
        assert "old.event" not in actions
        assert "recent.event" in actions
        assert signals == {"open"}  # the closed old signal went; the open one is never pruned
        assert sessions == 0
        assert "old" not in history
        assert "wal checkpointed" in result["detail"]
    finally:
        r.dispose()


# ------------------------------------------------------------------ the provider-contacting job


def watch(r: Rig) -> None:
    wl = r.repo.create_watchlist("w", None)
    r.repo.add_watchlist_entry(wl.id, "ethereum", r.eth.wallet, None)


def test_watchlist_refresh_uses_the_normal_bounded_ingestion_path(tmp_path: Path) -> None:
    r = Rig(tmp_path, mode="disabled", settings=workers(watchlist_refresh_enabled=True))
    watch(r)
    try:
        with TestClient(r.app) as c:
            first = c.post(f"{API}/system/jobs/watchlist_refresh/run").json()
            assert first["status"] == "ok"
            assert "1 wallet(s)" in first["detail"]
            runs = c.get(f"{API}/ingestion/jobs").json()
            assert len(runs) == 1
            assert runs[0]["address"] == r.eth.wallet
            assert r.eth.calls  # it really went through the provider path
            used = c.get(f"{API}/system/usage").json()
            assert used["total_runs"] == 1
    finally:
        r.dispose()


def test_watchlist_refresh_skips_when_ingestion_is_busy_or_storage_is_full(tmp_path: Path) -> None:
    r = Rig(tmp_path, mode="disabled", settings=workers(watchlist_refresh_enabled=True))
    watch(r)
    try:
        with TestClient(r.app) as c:
            ctx = r.app.state.bip
            assert ctx.ingest_lock.acquire(blocking=False)
            try:
                busy = c.post(f"{API}/system/jobs/watchlist_refresh/run").json()
            finally:
                ctx.ingest_lock.release()
            assert busy["status"] == "skipped"
            assert "in progress" in busy["detail"]
            # the API route itself refuses writes on a full disk, so go through the scheduler
            ctx.disk_guard = roomy_guard(r.settings.resolved_data_root, free=GIB // 2)
            full = ctx.scheduler.run_now("watchlist_refresh")
            assert full["status"] == "skipped"
            assert "storage guard" in full["detail"]
            assert r.eth.calls == []
    finally:
        r.dispose()


def test_refresh_picks_the_least_recently_refreshed_wallet_first(tmp_path: Path) -> None:
    r = Rig(
        tmp_path,
        mode="disabled",
        settings=workers(watchlist_refresh_enabled=True, watchlist_refresh_wallets_per_tick=1),
    )
    wl = r.repo.create_watchlist("w", None)
    other = "0x" + "00" * 19 + "bb"
    r.repo.add_watchlist_entry(wl.id, "ethereum", r.eth.wallet, None)
    r.repo.add_watchlist_entry(wl.id, "ethereum", other, None)
    try:
        with TestClient(r.app) as c:
            for _ in range(2):
                c.post(f"{API}/system/jobs/watchlist_refresh/run")
            addresses = {j["address"] for j in c.get(f"{API}/ingestion/jobs").json()}
        assert addresses == {r.eth.wallet, other}  # the second tick took the other wallet
    finally:
        r.dispose()


def test_job_history_is_stored_in_the_database(rig: Rig) -> None:
    r2 = Rig(
        Path(str(rig.settings.resolved_data_root.parent) + "-b"),
        mode="disabled",
        settings=workers(),
    )
    try:
        with TestClient(r2.app) as c:
            c.post(f"{API}/system/jobs/discovery/run")
        with r2.engine.connect() as conn:
            rows = conn.execute(text("SELECT job, status, detail FROM job_runs")).all()
        assert rows
        assert (rows[0][0], rows[0][1]) == ("discovery", "ok")
        assert json.dumps(rows[0][2]).count("alerts") == 1
    finally:
        r2.dispose()


def test_all_checked_providers_unavailable_is_a_warning_but_untested_is_not(rig: Rig) -> None:
    with TestClient(rig.app) as c:
        assert "no_healthy_provider" not in {
            w["code"] for w in c.get(f"{API}/system/health").json()["warnings"]
        }
        rig.eth.chain_id = "0x2105"  # the provider answers for the wrong network: unavailable
        c.post(f"{API}/providers/publicnode-eth/health-check")
        codes = {w["code"] for w in c.get(f"{API}/system/health").json()["warnings"]}
    assert "no_healthy_provider" in codes


def test_relationships_job_reads_only_the_database(tmp_path: Path) -> None:
    r = Rig(tmp_path, mode="disabled", settings=workers())
    try:
        with TestClient(r.app) as c:
            run = c.post(f"{API}/system/jobs/relationships/run").json()
            assert run["status"] == "ok"
            assert "scan(s) hit the row cap" in run["detail"]
            assert r.eth.calls == []
            assert r.sol.calls == []
    finally:
        r.dispose()
