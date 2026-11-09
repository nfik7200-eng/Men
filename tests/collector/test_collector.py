"""The Ethereum block collector: bounded, finalized-only, resumable, honest about its gaps."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from bip.discovery.service import DiscoveryService
from bip.ops.jobs import build_jobs
from tests.security.helpers import API, PASSWORD, Rig
from tests.stage2.fakes import addr, h32

ETH = 10**18


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    r = Rig(tmp_path, mode="disabled", settings={"collector": {"initial_lookback_blocks": 10}})
    yield r
    r.dispose()


def fin(rig: Rig) -> int:
    return rig.eth.head - 64  # the fake node's finalized block


def populate(rig: Rig) -> None:
    n, node = fin(rig), rig.eth
    node.add_block_tx(n - 5, h32(0x501), addr(0xD1), None, 0, contract=addr(0xC1))
    node.add_block_tx(n - 4, h32(0x502), addr(0xA1), addr(0xB1), 50 * ETH)  # large
    node.add_block_tx(n - 4, h32(0x503), addr(0xA2), addr(0xB2), 1 * ETH)  # below threshold
    node.add_block_tx(n - 3, h32(0x504), addr(0xA3), addr(0xB3), 0)  # plain call


def count(rig: Rig, table: str) -> int:
    with rig.engine.connect() as conn:
        return int(conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())  # noqa: S608


def test_collects_creations_and_large_transfers_and_discloses_the_rest(rig: Rig) -> None:
    populate(rig)
    with TestClient(rig.app) as c:
        r = c.post(f"{API}/discovery/collect/ethereum")
        assert r.status_code == 201
        m = r.json()
        assert m["status"] == "succeeded"
        assert (m["first_block"], m["last_block"], m["finalized_head"]) == (
            fin(rig) - 9,
            fin(rig),
            fin(rig),
        )
        assert m["contract_creations_stored"] == 1
        assert m["large_transfers_stored"] == 1
        assert m["skipped_below_threshold"] == 2
        assert any("liquidity" in x for x in m["not_covered"])
        assert count(rig, "transactions") == 2  # only what the detectors can use
        assert count(rig, "transfers") == 1
        with rig.engine.connect() as conn:
            created = conn.execute(text("SELECT created_contract FROM transactions")).scalars()
            assert addr(0xC1) in set(created)
            assert (
                conn.execute(text("SELECT DISTINCT finality FROM transactions")).scalar_one()
                == "finalized"
            )


def test_a_second_run_resumes_and_an_idle_run_is_a_no_op(rig: Rig) -> None:
    populate(rig)
    with TestClient(rig.app) as c:
        c.post(f"{API}/discovery/collect/ethereum")
        again = c.post(f"{API}/discovery/collect/ethereum").json()
        assert again["blocks_scanned"] == 0
        assert "Up to date" in again["message"]
        rig.eth.calls.clear()
        c.post(f"{API}/discovery/collect/ethereum")
        assert "eth_getBlockByNumber" in rig.eth.calls  # only the finalized-tag lookup
        assert rig.eth.calls.count("eth_getBlockByNumber") == 1
        status = c.get(f"{API}/discovery/collector").json()
        assert status["last_block_scanned"] == fin(rig)
        assert status["last_run"]["status"] == "succeeded"


def test_blocks_per_run_bounds_the_work_and_the_cursor_only_advances_over_scanned_blocks(
    tmp_path: Path,
) -> None:
    rig = Rig(
        tmp_path,
        mode="disabled",
        settings={"collector": {"blocks_per_run": 4, "initial_lookback_blocks": 10}},
    )
    try:
        populate(rig)
        with TestClient(rig.app) as c:
            first = c.post(f"{API}/discovery/collect/ethereum").json()
            assert first["blocks_scanned"] == 4
            assert first["last_block"] == fin(rig) - 6
            assert first["contract_creations_stored"] == 0  # creation is in block fin-5
            second = c.post(f"{API}/discovery/collect/ethereum").json()
            assert second["first_block"] == fin(rig) - 5
            assert second["contract_creations_stored"] == 1
    finally:
        rig.dispose()


def test_receipt_cap_is_disclosed_not_hidden(tmp_path: Path) -> None:
    rig = Rig(
        tmp_path,
        mode="disabled",
        settings={"collector": {"max_receipts_per_run": 1, "initial_lookback_blocks": 10}},
    )
    try:
        populate(rig)
        with TestClient(rig.app) as c:
            m = c.post(f"{API}/discovery/collect/ethereum").json()
        assert m["contract_creations_stored"] == 1
        assert m["status"] == "partial"  # stopped before the next block, which resumes next run
        assert m["last_block"] == fin(rig) - 5
        assert "Receipt cap" in m["message"]
        with TestClient(rig.app) as c:
            later = c.post(f"{API}/discovery/collect/ethereum").json()
        assert later["large_transfers_stored"] == 1
    finally:
        rig.dispose()


def test_candidates_over_the_cap_inside_one_block_are_counted_as_a_gap(tmp_path: Path) -> None:
    rig = Rig(
        tmp_path,
        mode="disabled",
        settings={"collector": {"max_receipts_per_run": 1, "initial_lookback_blocks": 3}},
    )
    try:
        n = fin(rig)
        rig.eth.add_block_tx(n, h32(0x701), addr(0xA1), addr(0xB1), 40 * ETH)
        rig.eth.add_block_tx(n, h32(0x702), addr(0xA2), addr(0xB2), 41 * ETH)
        with TestClient(rig.app) as c:
            m = c.post(f"{API}/discovery/collect/ethereum").json()
        assert (m["large_transfers_stored"], m["skipped_over_receipt_cap"]) == (1, 1)
    finally:
        rig.dispose()


def test_a_failed_transaction_moves_no_value_and_a_missing_receipt_is_counted(rig: Rig) -> None:
    n = fin(rig)
    rig.eth.add_block_tx(n - 2, h32(0x601), addr(0xA1), addr(0xB1), 40 * ETH)
    rig.eth.break_receipt_for = h32(0x601)
    with TestClient(rig.app) as c:
        m = c.post(f"{API}/discovery/collect/ethereum").json()
    assert m["receipt_missing"] == 1
    assert m["large_transfers_stored"] == 0
    assert count(rig, "transfers") == 0


def test_nothing_is_read_when_the_provider_has_no_finalized_tag(rig: Rig) -> None:
    populate(rig)
    rig.eth.no_finalized_tag = True
    with TestClient(rig.app) as c:
        m = c.post(f"{API}/discovery/collect/ethereum").json()
    assert m["status"] == "blocked"
    assert "provisional" in m["message"]
    assert count(rig, "transactions") == 0


def test_the_detectors_run_over_what_the_collector_stored(rig: Rig) -> None:
    populate(rig)
    with TestClient(rig.app) as c:
        c.post(f"{API}/discovery/collect/ethereum")
    with rig.engine.connect() as conn:
        latest = conn.execute(text("SELECT MAX(block_time) FROM transactions")).scalar_one()
    when = datetime.fromisoformat(str(latest)).replace(tzinfo=UTC) + timedelta(hours=1)
    service = DiscoveryService(rig.repo, rig.settings, clock=lambda: when)
    result = service.run("ethereum")
    deployment = result["detectors"]["new_contract_deployment:ethereum"]
    assert deployment["created"] == 1  # a contract nobody asked about, found by the collector
    signals = service.list_signals(chain="ethereum", limit=10, offset=0)["items"]
    assert any(addr(0xC1) in str(s) for s in signals)


def test_it_is_admin_only_and_the_job_is_off_by_default(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.user("ana", "analyst")
    try:
        with TestClient(rig.app) as c:
            r = c.post(f"{API}/auth/login", json={"username": "ana", "password": PASSWORD})
            headers = {"X-CSRF-Token": r.json()["csrf_token"]}
            assert c.post(f"{API}/discovery/collect/ethereum", headers=headers).status_code == 403
            assert c.get(f"{API}/discovery/collector").status_code == 200
        assert "chain_scan" not in {j.name for j in build_jobs(rig.app.state.bip, None)}  # type: ignore[arg-type]
    finally:
        rig.dispose()
