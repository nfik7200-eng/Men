"""The collectors write BIP-owned checkpoints and coverage ranges (v16). SYNTHETIC chain data."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bip.db.checkpoints import CheckpointStore, CoverageStatus, StreamRef
from bip.ingest.solana_collector import CURSOR_KEY as SOL_CURSOR
from bip.providers.state import ProviderStateStore
from tests.collector.test_collector import fin
from tests.collector.test_collector import populate as eth_populate
from tests.collector.test_solana import collect, hash_of
from tests.collector.test_solana import populate as sol_populate
from tests.security.helpers import API, Rig
from tests.stage2.fakes import addr, h32, make_block

WEI = 10**18

ETH = StreamRef("ethereum", "collector")
SOL = StreamRef("solana", "collector")


@pytest.fixture
def eth_rig(tmp_path: Path) -> Iterator[Rig]:
    r = Rig(tmp_path, mode="disabled", settings={"collector": {"initial_lookback_blocks": 10}})
    yield r
    r.dispose()


@pytest.fixture
def sol_rig(tmp_path: Path) -> Iterator[Rig]:
    r = Rig(tmp_path, mode="disabled", settings={"solana_collector": {"blocks_per_run": 5}})
    yield r
    r.dispose()


def statuses(rig: Rig, chain: str) -> list[tuple[int, int, str]]:
    rows = CheckpointStore(rig.repo).ranges(chain, limit=500)
    return sorted((r["from_pos"], r["to_pos"], r["status"]) for r in rows)


def test_ethereum_run_records_processed_blocks_and_moves_the_live_checkpoint(
    eth_rig: Rig,
) -> None:
    eth_populate(eth_rig)
    with TestClient(eth_rig.app) as c:
        m = c.post(f"{API}/discovery/collect/ethereum").json()
    n = fin(eth_rig)
    cp = CheckpointStore(eth_rig.repo).get(ETH, "live")
    assert cp is not None
    assert (cp.cursor, cp.position_kind, cp.status) == (m["last_block"], "block", "idle")
    assert cp.cursor == n
    assert cp.cursor_hash is not None
    assert cp.decoder_version == "3"
    assert cp.last_providers == m["providers"]
    # one merged processed range per provider, covering exactly the scanned blocks
    assert statuses(eth_rig, "ethereum") == [(m["first_block"], m["last_block"], "processed")]
    assert CheckpointStore(eth_rig.repo).get(ETH, "historical") is None  # independent, unset


def test_ethereum_blocked_run_keeps_no_cursor_and_says_why(eth_rig: Rig) -> None:
    eth_populate(eth_rig)
    eth_rig.eth.no_finalized_tag = True
    with TestClient(eth_rig.app) as c:
        c.post(f"{API}/discovery/collect/ethereum")
    cp = CheckpointStore(eth_rig.repo).get(ETH, "live")
    assert cp is not None
    assert (cp.cursor, cp.status, cp.last_error_category) == (None, "blocked", "no_finalized_tag")
    assert statuses(eth_rig, "ethereum") == []  # nothing claimed as covered


def test_solana_run_separates_processed_from_confirmed_empty(sol_rig: Rig) -> None:
    sol_populate(sol_rig)
    collect(sol_rig)
    head = sol_rig.sol.head_slot
    assert statuses(sol_rig, "solana") == [
        (head - 4, head - 1, "processed"),
        (head, head, "confirmed_empty"),  # the node said the head slot was skipped
    ]
    cp = CheckpointStore(sol_rig.repo).get(SOL, "live")
    assert cp is not None
    assert (cp.cursor, cp.position_kind, cp.network) == (head, "slot", "mainnet-beta")
    assert cp.cursor_hash is None  # the head slot was skipped: no block, so no hash


def test_solana_outage_stalls_the_checkpoint_without_moving_it(tmp_path: Path) -> None:
    rig = Rig(
        tmp_path,
        mode="disabled",
        settings={"solana_collector": {"blocks_per_run": 3, "max_slot_attempts": 1}},
    )
    try:
        head = rig.sol.head_slot
        rig.sol.fail_slots.add(head - 1)
        rig.sol.blocks[head - 2] = make_block(head - 2, hash_of(head - 2), hash_of(head - 3), [])
        collect(rig)
        cp = CheckpointStore(rig.repo).get(SOL, "live")
        assert cp is not None
        assert cp.cursor == head - 2 == int(rig.repo.get_meta(SOL_CURSOR) or 0)
        assert (cp.status, cp.last_error_category) == ("stalled", "provider_unavailable")
        # the failing slot is neither processed nor recorded as a gap: it is "not yet processed"
        assert statuses(rig, "solana") == [(head - 2, head - 2, "processed")]
    finally:
        rig.dispose()


def test_solana_lag_jump_is_recorded_as_skipped_not_empty(sol_rig: Rig) -> None:
    sol_populate(sol_rig)
    collect(sol_rig)
    sol_rig.repo.set_meta(SOL_CURSOR, str(sol_rig.sol.head_slot - 50_000))
    collect(sol_rig)
    skipped = CheckpointStore(sol_rig.repo).ranges("solana", statuses=[CoverageStatus.SKIPPED])
    assert len(skipped) == 1
    assert skipped[0]["from_pos"] == sol_rig.sol.head_slot - 50_000 + 1
    assert skipped[0]["reason"] == "lag exceeded max_lag_slots"


def test_ethereum_given_up_block_is_failed_and_the_cursor_hash_is_not_borrowed(
    tmp_path: Path,
) -> None:
    rig = Rig(
        tmp_path,
        mode="disabled",
        settings={
            "collector": {
                "initial_lookback_blocks": 3,
                "blocks_per_run": 2,
                "max_block_attempts": 1,
            }
        },
    )
    try:
        n = fin(rig)
        rig.eth.corrupt_blocks.add(n - 1)  # the last block of the 2-block window never parses
        with TestClient(rig.app) as c:
            m = c.post(f"{API}/discovery/collect/ethereum").json()
        assert m["blocks_given_up"] == 1
        assert statuses(rig, "ethereum") == [(n - 2, n - 2, "processed"), (n - 1, n - 1, "failed")]
        cp = CheckpointStore(rig.repo).get(ETH, "live")
        assert cp is not None
        assert (cp.cursor, cp.cursor_hash) == (n - 1, None)  # past the gap, no borrowed hash
    finally:
        rig.dispose()


def test_ethereum_receipt_cap_is_incomplete_alongside_processed(tmp_path: Path) -> None:
    rig = Rig(
        tmp_path,
        mode="disabled",
        settings={"collector": {"initial_lookback_blocks": 1, "max_receipts_per_run": 1}},
    )
    try:
        n, node = fin(rig), rig.eth
        node.add_block_tx(n, h32(0x601), addr(0xA1), addr(0xB1), 50 * WEI)
        node.add_block_tx(n, h32(0x602), addr(0xA2), addr(0xB2), 60 * WEI)
        with TestClient(rig.app) as c:
            c.post(f"{API}/discovery/collect/ethereum")
        rows = CheckpointStore(rig.repo).ranges("ethereum")
        assert sorted((r["from_pos"], r["status"]) for r in rows) == [
            (n, "incomplete"),
            (n, "processed"),
        ]
        incomplete = next(r for r in rows if r["status"] == "incomplete")
        assert "receipt cap" in incomplete["reason"]
    finally:
        rig.dispose()


def test_an_open_circuit_blocks_the_collector_and_keeps_the_cursor(eth_rig: Rig) -> None:
    eth_populate(eth_rig)
    with TestClient(eth_rig.app) as c:
        c.post(f"{API}/discovery/collect/ethereum")
        before = CheckpointStore(eth_rig.repo).get(ETH, "live")
        assert before is not None
        state = ProviderStateStore(eth_rig.repo)
        for _ in range(5):
            state.record_call("publicnode-eth", ok=False, latency_ms=None, outcome="timeout")
        m = c.post(f"{API}/discovery/collect/ethereum").json()
    assert m["status"] == "blocked"
    after = CheckpointStore(eth_rig.repo).get(ETH, "live")
    assert after is not None
    assert after.cursor == before.cursor
    assert (after.status, after.last_error_category) == ("blocked", "no_eligible_provider")


def test_solana_lag_just_beyond_the_limit_but_within_one_run_is_read_not_skipped(
    tmp_path: Path,
) -> None:
    """max_lag_slots < lag <= blocks_per_run: the slots are within reach, so nothing is skipped
    and the run does not fail."""
    rig = Rig(
        tmp_path,
        mode="disabled",
        settings={"solana_collector": {"blocks_per_run": 50, "max_lag_slots": 10}},
    )
    try:
        head = rig.sol.head_slot
        rig.repo.set_meta(SOL_CURSOR, str(head - 20))
        m = collect(rig)
        assert m["status"] != "failed"
        assert m["gap_recorded"] is None
        assert CheckpointStore(rig.repo).ranges("solana", statuses=[CoverageStatus.SKIPPED]) == []
    finally:
        rig.dispose()


def test_solana_head_seen_gives_lag_without_a_provider(sol_rig: Rig) -> None:
    sol_populate(sol_rig)
    collect(sol_rig)
    cp = CheckpointStore(sol_rig.repo).get(SOL, "live")
    assert cp is not None
    assert (cp.head_seen, cp.lag, cp.status) == (sol_rig.sol.head_slot, 0, "idle")
