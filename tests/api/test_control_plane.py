"""Read-only control-plane endpoints: checkpoints, coverage and budgets (v16)."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from bip.db.checkpoints import CheckpointStatus, CheckpointStore, CoverageStatus, StreamRef
from bip.db.repository import Repository

API = "/api/v1"
SOL = StreamRef("solana", "collector")


def test_checkpoints_are_empty_until_something_is_committed(client: TestClient) -> None:
    r = client.get(f"{API}/ingestion/checkpoints")
    assert r.status_code == 200
    assert r.json() == []


def test_checkpoints_report_lag_as_of_the_last_run_without_contacting_a_provider(
    client: TestClient, repo: Repository
) -> None:
    store = CheckpointStore(repo)
    store.commit_batch(
        SOL,
        "live",
        from_pos=990,
        to_pos=1000,
        decoder_version="1",
        cursor_hash="H",
        providers=["solana-public"],
    )
    store.mark(
        SOL,
        "live",
        CheckpointStatus.STALLED,
        decoder_version="1",
        error_category="provider_unavailable",
    )
    store.finish_run(SOL, "live", head_seen=1040)  # what the last run saw; no provider call
    body = client.get(f"{API}/ingestion/checkpoints", params={"chain": "solana"}).json()
    assert len(body) == 1
    cp = body[0]
    assert (cp["cursor"], cp["position_kind"], cp["network"]) == (1000, "slot", "mainnet-beta")
    assert (cp["status"], cp["last_error_category"]) == ("stalled", "provider_unavailable")
    assert (cp["head_as_of_last_run"], cp["lag_as_of_last_run"]) == (1040, 40)
    assert cp["last_providers"] == ["solana-public"]


def test_unknown_chain_is_rejected(client: TestClient) -> None:
    bad = client.get(f"{API}/ingestion/checkpoints", params={"chain": "dogecoin"})
    assert bad.status_code == 422
    assert client.get(f"{API}/ingestion/coverage/dogecoin").status_code == 422


def test_coverage_lists_gaps_separately_and_explains_unobserved_ranges(
    client: TestClient, repo: Repository
) -> None:
    store = CheckpointStore(repo)
    store.record_range(
        SOL, 100, 109, CoverageStatus.PROCESSED, decoder_version="1", provider_id="solana-public"
    )
    store.record_range(SOL, 110, 110, CoverageStatus.CONFIRMED_EMPTY, decoder_version="1")
    store.record_range(
        SOL,
        111,
        111,
        CoverageStatus.FAILED,
        decoder_version="1",
        reason="given up after 3 failed reads",
        attempts=3,
    )
    store.commit_batch(SOL, "live", from_pos=100, to_pos=111, decoder_version="1")
    body = client.get(f"{API}/ingestion/coverage/solana").json()
    assert body["position_kind"] == "slot"
    assert [(g["from_pos"], g["status"]) for g in body["gaps"]] == [(111, "failed")]
    assert {r["status"] for r in body["ranges"]} == {"processed", "confirmed_empty", "failed"}
    assert body["summary"]["by_status"]["processed"]["positions"] == 10
    assert body["not_yet_processed"]["after"] == 111
    assert "never treat as empty" in body["meaning"]["not observed"]


def test_budgets_show_configured_caps_and_no_secrets(client: TestClient) -> None:
    body = client.get(f"{API}/system/budgets").json()
    assert body["per_run"]["ethereum_collector"]["max_rpc_calls"] == 150
    assert body["per_run"]["solana_collector"]["deadline_s"] == 90.0
    assert body["circuit_breaker"] == {"enabled": True, "failure_threshold": 5, "open_s": 120.0}
    assert body["scheduled_collection"]["scheduler_enabled"] is False
    assert set(body) == {
        "per_run",
        "retries",
        "circuit_breaker",
        "storage",
        "scheduled_collection",
        "provider_daily",
        "quota_tracking",
    }
    assert body["provider_daily"]["budgets"] == {}
    assert "http" not in json.dumps(body).lower()


def test_the_new_endpoints_are_read_only(client: TestClient) -> None:
    for path in ("/ingestion/checkpoints", "/ingestion/coverage/solana", "/system/budgets"):
        assert client.post(f"{API}{path}").status_code == 405


def test_resolved_gaps_are_history_not_gaps(client: TestClient, repo: Repository) -> None:
    store = CheckpointStore(repo)
    store.record_range(SOL, 50, 50, CoverageStatus.FAILED, decoder_version="1", reason="x")
    store.record_range(SOL, 49, 51, CoverageStatus.PROCESSED, decoder_version="1")
    body = client.get(f"{API}/ingestion/coverage/solana").json()
    assert body["gaps"] == []
    failed = [r for r in body["ranges"] if r["status"] == "failed"]
    assert len(failed) == 1
    assert failed[0]["resolved_at"] is not None
    assert body["summary"]["resolved_gap_ranges"] == 1


def test_required_mode_protects_the_new_endpoints(tmp_path: Path) -> None:
    from tests.security.helpers import PASSWORD, Rig

    rig = Rig(tmp_path, mode="required")
    rig.user("vera", role="viewer")
    try:
        with TestClient(rig.app) as c:
            for path in (
                "/ingestion/checkpoints",
                "/ingestion/coverage/solana",
                "/system/budgets",
                "/settings",
            ):
                assert c.get(f"{API}{path}").status_code == 401, path
            r = c.post(f"{API}/auth/login", json={"username": "vera", "password": PASSWORD})
            assert r.status_code == 200
            assert c.get(f"{API}/ingestion/checkpoints").status_code == 200
            assert c.get(f"{API}/settings").json()["authentication"] == "required"
    finally:
        rig.dispose()
