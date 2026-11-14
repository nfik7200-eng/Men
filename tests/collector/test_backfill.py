"""Bounded historical backfill, gap recovery and pause/resume (Stage 2). SYNTHETIC chain data:
every block, slot and provider here is a local fake; nothing contacts a real provider."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from bip.db.checkpoints import CheckpointStore, CoverageStatus, StreamRef
from tests.collector.test_collector import fin
from tests.collector.test_solana import hash_of
from tests.security.helpers import API, PASSWORD, Rig
from tests.stage2.fakes import addr, h32, make_block

ETH = StreamRef("ethereum", "collector")
SOL = StreamRef("solana", "collector")
WEI = 10**18


def eth_rig(tmp_path: Path, **backfill: object) -> Rig:
    return Rig(
        tmp_path,
        mode="disabled",
        settings={
            "collector": {"initial_lookback_blocks": 5},
            "backfill": {"blocks_per_run": 5, "slots_per_run": 4, **backfill},
        },
    )


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    r = eth_rig(tmp_path)
    yield r
    r.dispose()


def post(rig: Rig, path: str) -> dict[str, Any]:
    with TestClient(rig.app) as c:
        r = c.post(f"{API}{path}")
    assert r.status_code in (200, 201), r.text
    return r.json()


def live_eth(rig: Rig) -> dict[str, Any]:
    with TestClient(rig.app) as c:
        return c.post(f"{API}/discovery/collect/ethereum").json()


def ranges(rig: Rig, chain: str) -> list[tuple[int, int, str]]:
    rows = CheckpointStore(rig.repo).ranges(chain, limit=500)
    return sorted((r["from_pos"], r["to_pos"], r["status"]) for r in rows)


def block_reads(rig: Rig) -> list[int]:
    return [c for c in rig.eth.calls if c == "eth_getBlockByNumber"]  # type: ignore[misc]


# ------------------------------------------------------------------------------------ Ethereum


def test_backfill_reads_backward_from_live_coverage_and_never_touches_live(rig: Rig) -> None:
    n = fin(rig)
    live_eth(rig)  # live covers n-4 .. n
    live_before = CheckpointStore(rig.repo).get(ETH, "live")
    rig.eth.add_block_tx(n - 7, h32(0x701), addr(0xA1), addr(0xB1), 80 * WEI)  # large, old
    m = post(rig, "/ingestion/backfill/ethereum")
    assert m["status"] == "succeeded"
    assert m["window"] == [n - 9, n - 5]
    hist = CheckpointStore(rig.repo).get(ETH, "historical")
    assert hist is not None
    assert (hist.cursor, hist.position_kind) == (n - 9, "block")
    assert hist.boundary == n - 4 - 180 * 7200  # set once from the target period
    assert "estimate" in hist.recovery["boundary_basis"]
    assert ranges(rig, "ethereum") == [(n - 9, n, "processed")]  # merged, contiguous
    with rig.engine.connect() as conn:
        assert (
            conn.execute(text("SELECT COUNT(*) FROM transfers WHERE standard='native'")).scalar()
            == 1
        )
    assert CheckpointStore(rig.repo).get(ETH, "live") == live_before


def test_backfill_resumes_where_it_stopped_without_rereading(rig: Rig) -> None:
    n = fin(rig)
    live_eth(rig)
    post(rig, "/ingestion/backfill/ethereum")
    reads_before = len(block_reads(rig))
    m = post(rig, "/ingestion/backfill/ethereum")
    assert m["window"] == [n - 14, n - 10]
    assert len(block_reads(rig)) - reads_before == 5  # exactly the new window
    assert CheckpointStore(rig.repo).get(ETH, "historical").cursor == n - 14  # type: ignore[union-attr]


def test_backfill_stops_at_the_history_floor_instead_of_shortening_the_period(
    rig: Rig,
) -> None:
    n = fin(rig)
    live_eth(rig)
    rig.eth.history_floor = n - 7  # blocks below are not served by the only provider
    m = post(rig, "/ingestion/backfill/ethereum")
    assert m["status"] == "blocked"
    hist = CheckpointStore(rig.repo).get(ETH, "historical")
    assert hist is not None
    assert (hist.cursor, hist.status, hist.last_error_category) == (
        n - 7,
        "blocked",
        "history_unavailable",
    )
    assert (n - 8, n - 8, "provider_unavailable") in ranges(rig, "ethereum")
    assert "boundary" in m["message"]


def test_a_block_that_keeps_failing_is_given_up_as_failed_and_the_cursor_passes(
    tmp_path: Path,
) -> None:
    rig = eth_rig(tmp_path, max_attempts=2)
    try:
        n = fin(rig)
        live_eth(rig)
        rig.eth.corrupt_blocks.add(n - 6)
        first = post(rig, "/ingestion/backfill/ethereum")
        assert first["status"] == "partial"
        assert CheckpointStore(rig.repo).get(ETH, "historical").cursor == n - 5  # type: ignore[union-attr]
        second = post(rig, "/ingestion/backfill/ethereum")  # second failure: give it up
        assert second["positions_given_up"] == 1
        assert (n - 6, n - 6, "failed") in ranges(rig, "ethereum")
        hist = CheckpointStore(rig.repo).get(ETH, "historical")
        assert hist is not None
        assert hist.cursor == n - 10
        assert hist.recovery["attempts"] == {}
        # recovery re-reads the failed block once it parses; checkpoints do not move
        rig.eth.corrupt_blocks.clear()
        r = post(rig, "/ingestion/recovery/ethereum")
        assert r["positions_recovered"] == 1
        open_gaps = CheckpointStore(rig.repo).ranges(
            "ethereum", statuses=[CoverageStatus.FAILED], include_resolved=False
        )
        assert open_gaps == []
        assert CheckpointStore(rig.repo).get(ETH, "historical") == hist
    finally:
        rig.dispose()


def test_a_provider_outage_stalls_without_moving_the_cursor(rig: Rig) -> None:
    n = fin(rig)
    live_eth(rig)
    post(rig, "/ingestion/backfill/ethereum")
    rig.transport.nodes.clear()  # every provider is now unreachable
    m = post(rig, "/ingestion/backfill/ethereum")
    assert m["status"] == "partial"
    hist = CheckpointStore(rig.repo).get(ETH, "historical")
    assert hist is not None
    assert hist.cursor == n - 9
    assert (hist.status, hist.last_error_category) == ("stalled", "provider_unavailable")


def test_live_has_priority_when_it_is_behind(rig: Rig) -> None:
    live_eth(rig)
    CheckpointStore(rig.repo).finish_run(ETH, "live", head_seen=fin(rig) + 1000)
    calls = len(rig.eth.calls)
    m = post(rig, "/ingestion/backfill/ethereum")
    assert m["status"] == "deferred"
    assert "priority" in m["message"]
    assert len(rig.eth.calls) == calls  # nothing was read


def test_pause_and_resume_are_per_direction(rig: Rig) -> None:
    live_eth(rig)
    paused = post(rig, "/ingestion/streams/ethereum/historical/pause")
    assert paused["status"] == "paused"
    assert post(rig, "/ingestion/backfill/ethereum")["status"] == "paused"
    assert post(rig, "/ingestion/recovery/ethereum")["status"] == "paused"
    assert live_eth(rig)["status"] != "paused"  # live keeps working
    post(rig, "/ingestion/streams/ethereum/live/pause")
    assert live_eth(rig)["status"] == "paused"
    post(rig, "/ingestion/streams/ethereum/historical/resume")
    assert post(rig, "/ingestion/backfill/ethereum")["status"] == "succeeded"  # live paused: ok


def test_backfill_reaches_its_boundary_and_completes(tmp_path: Path) -> None:
    rig = eth_rig(tmp_path, ethereum_target_days=1, ethereum_blocks_per_day_estimate=8)
    try:
        n = fin(rig)
        live_eth(rig)
        post(rig, "/ingestion/backfill/ethereum")
        m = post(rig, "/ingestion/backfill/ethereum")
        assert m["status"] == "complete"
        hist = CheckpointStore(rig.repo).get(ETH, "historical")
        assert hist is not None
        assert (hist.cursor, hist.boundary, hist.status) == (n - 12, n - 12, "complete")
        assert post(rig, "/ingestion/backfill/ethereum")["status"] == "complete"
    finally:
        rig.dispose()


def test_allowlisted_token_transfers_are_backfilled_with_an_address_filter(
    tmp_path: Path,
) -> None:
    token = addr(0x7E)
    rig = Rig(
        tmp_path,
        mode="disabled",
        settings={
            "collector": {
                "initial_lookback_blocks": 5,
                "token_allowlist": [token],
                "token_scan_enabled": False,  # the backfill then uses an address filter
            },
            "backfill": {"blocks_per_run": 5},
        },
    )
    try:
        n = fin(rig)
        live_eth(rig)
        rig.eth.add_token_transfer(n - 8, h32(0x801), token, addr(0xC1), addr(0xC2), 1234)
        rig.eth.add_token_transfer(n - 8, h32(0x802), addr(0x7F), addr(0xC1), addr(0xC2), 9)
        m = post(rig, "/ingestion/backfill/ethereum")
        assert m["allowlisted_transfers_stored"] == 1
        assert any(has_address for _a, _b, has_address in rig.eth.log_windows)
        with rig.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT token, amount_raw FROM transfers WHERE standard = 'erc20'")
            ).all()
        assert [tuple(r) for r in rows] == [(token, "1234")]
    finally:
        rig.dispose()


def test_an_interrupted_batch_is_reread_without_duplicates(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    n = fin(rig)
    live_eth(rig)
    rig.eth.add_block_tx(n - 6, h32(0x901), addr(0xA1), addr(0xB1), 90 * WEI)
    real = CheckpointStore.commit_batch
    calls = {"n": 0}

    def crash_once(self: CheckpointStore, *a: Any, **k: Any) -> Any:
        if k.get("conn") is not None and a[1] == "historical" and calls["n"] == 0:
            calls["n"] += 1
            raise RuntimeError("simulated crash before the commit")
        return real(self, *a, **k)

    monkeypatch.setattr(CheckpointStore, "commit_batch", crash_once)
    first = post(rig, "/ingestion/backfill/ethereum")
    assert first["status"] == "partial"
    hist = CheckpointStore(rig.repo).get(ETH, "historical")
    assert hist is not None
    assert hist.cursor is None  # nothing was claimed
    post(rig, "/ingestion/backfill/ethereum")
    with rig.engine.connect() as conn:
        assert (
            conn.execute(text("SELECT COUNT(*) FROM transfers WHERE standard='native'")).scalar()
            == 1
        )
    assert CheckpointStore(rig.repo).get(ETH, "historical").cursor == n - 9  # type: ignore[union-attr]


def test_the_run_budget_bounds_backfill(tmp_path: Path) -> None:
    rig = eth_rig(tmp_path, max_rpc_calls_per_run=3)
    try:
        n = fin(rig)
        live_eth(rig)
        m = post(rig, "/ingestion/backfill/ethereum")
        assert m["status"] == "partial"
        assert m["rpc_calls"] <= 3
        hist = CheckpointStore(rig.repo).get(ETH, "historical")
        # the cursor covers only finished blocks (never past an unread one)
        # nothing was claimed: the window is read again next run, never skipped
        assert hist is not None
        assert hist.cursor is None
        assert ranges(rig, "ethereum") == [(n - 4, n, "processed")]
    finally:
        rig.dispose()


def test_backfill_endpoints_are_admin_only(tmp_path: Path) -> None:
    rig = Rig(tmp_path, mode="required")
    rig.user("ana", role="analyst")
    try:
        with TestClient(rig.app) as c:
            r = c.post(f"{API}/auth/login", json={"username": "ana", "password": PASSWORD})
            headers = {"X-CSRF-Token": r.json()["csrf_token"]}
            for path in (
                "/ingestion/backfill/ethereum",
                "/ingestion/recovery/solana",
                "/ingestion/streams/solana/live/pause",
            ):
                assert c.post(f"{API}{path}", headers=headers).status_code == 403, path
    finally:
        rig.dispose()


def test_backfill_jobs_are_off_by_default(rig: Rig) -> None:
    from bip.ops.jobs import build_jobs

    with TestClient(rig.app):
        ctx = rig.app.state.bip
        names = {j.name for j in build_jobs(ctx, ctx.backups)}
    assert not names & {"ethereum_backfill", "solana_backfill", "gap_recovery"}


# -------------------------------------------------------------------------------------- Solana


def sol_rig(tmp_path: Path, **backfill: object) -> Rig:
    return Rig(
        tmp_path,
        mode="disabled",
        settings={
            "solana_collector": {"blocks_per_run": 3},
            "backfill": {"slots_per_run": 4, **backfill},
        },
    )


def chain_blocks(rig: Rig, slots: range) -> None:
    for slot in slots:
        rig.sol.blocks[slot] = make_block(slot, hash_of(slot), hash_of(slot - 1), [])


def live_sol(rig: Rig) -> dict[str, Any]:
    with TestClient(rig.app) as c:
        return c.post(f"{API}/discovery/collect/solana").json()


def test_solana_backfill_separates_skipped_slots_and_keeps_live_independent(
    tmp_path: Path,
) -> None:
    rig = sol_rig(tmp_path)
    try:
        head = rig.sol.head_slot
        chain_blocks(rig, range(head - 12, head + 1))
        del rig.sol.blocks[head - 5]  # a skipped slot (-32007)
        live_sol(rig)  # live covers head-2 .. head
        post(rig, "/ingestion/streams/solana/live/pause")
        m = post(rig, "/ingestion/backfill/solana")
        assert m["window"] == [head - 6, head - 3]
        assert m["continuity_mismatches"] == 0
        cov = ranges(rig, "solana")
        assert (head - 5, head - 5, "confirmed_empty") in cov
        hist = CheckpointStore(rig.repo).get(SOL, "historical")
        assert hist is not None
        assert (hist.cursor, hist.position_kind) == (head - 6, "slot")
        assert hist.boundary == head - 2 - 90 * 216_000
        assert CheckpointStore(rig.repo).get(SOL, "live").status == "paused"  # type: ignore[union-attr]
    finally:
        rig.dispose()


def test_solana_pruned_history_blocks_the_backfill(tmp_path: Path) -> None:
    rig = sol_rig(tmp_path)
    try:
        head = rig.sol.head_slot
        chain_blocks(rig, range(head - 12, head + 1))
        live_sol(rig)
        rig.sol.history_floor_slot = head - 4
        rig.sol.history_floor_code = -32001  # "cleaned up": the node pruned it
        m = post(rig, "/ingestion/backfill/solana")
        assert m["status"] == "blocked"
        hist = CheckpointStore(rig.repo).get(SOL, "historical")
        assert hist is not None
        assert (hist.cursor, hist.last_error_category) == (head - 4, "history_unavailable")
        assert (head - 5, head - 5, "provider_unavailable") in ranges(rig, "solana")
    finally:
        rig.dispose()


def test_solana_skipped_or_missing_answer_is_recorded_and_passed_not_a_floor(
    tmp_path: Path,
) -> None:
    """-32009 says "skipped, or missing in long-term storage". Old skipped slots answer like
    that, so one such slot must not block the backfill; it is recorded as unavailable (with the
    ambiguity stated) and passed. A window of nothing but such answers is treated as the floor."""
    rig = sol_rig(tmp_path)
    try:
        head = rig.sol.head_slot
        chain_blocks(rig, range(head - 12, head + 1))
        live_sol(rig)
        rig.sol.history_floor_slot = head - 3  # slots head-6 .. head-4 answer -32009
        m = post(rig, "/ingestion/backfill/solana")
        assert m["status"] == "succeeded"
        assert m["positions_ambiguous"] == 3
        hist = CheckpointStore(rig.repo).get(SOL, "historical")
        assert hist is not None
        assert hist.cursor == head - 6
        rows = CheckpointStore(rig.repo).ranges(
            "solana", statuses=[CoverageStatus.PROVIDER_UNAVAILABLE]
        )
        assert [(r["from_pos"], r["to_pos"]) for r in rows] == [(head - 6, head - 4)]
        assert "does not distinguish" in str(rows[0]["reason"])
        nothing = post(rig, "/ingestion/backfill/solana")  # head-10 .. head-7: all -32009
        assert nothing["status"] == "blocked"
        assert CheckpointStore(rig.repo).get(SOL, "historical").cursor == head - 6  # type: ignore[union-attr]
    finally:
        rig.dispose()


def test_solana_recovery_reads_a_lag_skip_gap(tmp_path: Path) -> None:
    rig = sol_rig(tmp_path, recovery_positions_per_run=3)
    try:
        head = rig.sol.head_slot
        chain_blocks(rig, range(head - 12, head + 1))
        live_sol(rig)
        CheckpointStore(rig.repo).record_range(
            SOL,
            head - 11,
            head - 9,
            CoverageStatus.SKIPPED,
            decoder_version="1",
            reason="lag exceeded max_lag_slots",
        )
        m = post(rig, "/ingestion/recovery/solana")
        assert m["positions_recovered"] == 3
        gaps = CheckpointStore(rig.repo).ranges(
            "solana", statuses=[CoverageStatus.SKIPPED], include_resolved=False
        )
        assert gaps == []  # fully re-read: resolved, kept as history
        assert post(rig, "/ingestion/recovery/solana")["positions_targeted"] == 0
    finally:
        rig.dispose()


def test_missing_history_is_fetched_from_another_configured_provider(tmp_path: Path) -> None:
    """The routed provider is pruned below a floor; a second configured provider has the
    blocks. The backfill must use it instead of stopping at the first provider's floor."""
    from bip.config import load_settings
    from bip.db.engine import create_db_engine
    from bip.db.repository import Repository
    from bip.ingest.backfill import Backfill
    from bip.providers.registry import default_registry
    from bip.providers.state import ProviderStateStore
    from bip.router.router import ProviderRouter
    from tests.stage2.fakes import ETH_HOST, FakeEthNode, FakeTransport

    settings = load_settings(data_root=tmp_path / "d", env="test", backfill={"blocks_per_run": 4})
    engine = create_db_engine(settings)
    repo = Repository(engine)
    pruned, archive = FakeEthNode(), FakeEthNode()
    finalized = pruned.head - 64
    pruned.history_floor = finalized - 1  # only the newest two blocks are served
    router = ProviderRouter(
        default_registry(),
        settings,
        FakeTransport({ETH_HOST: pruned.handle, "mainnet.infura.io": archive.handle}),
        ProviderStateStore(repo),
        environ={"BIP_PROVIDER_INFURA_ETH_URL": "https://mainnet.infura.io/v3/" + "f" * 32},
        sleep=lambda _s: None,
        rng=lambda: 0.5,
    )
    try:
        CheckpointStore(repo).commit_batch(  # live collection covers finalized+1 .. +2
            ETH, "live", from_pos=finalized + 1, to_pos=finalized + 2, decoder_version="3"
        )
        CheckpointStore(repo).record_range(
            ETH, finalized + 1, finalized + 2, CoverageStatus.PROCESSED, decoder_version="3"
        )
        m = Backfill(router, repo, settings).run("ethereum")
        assert m["status"] == "succeeded", m["message"]
        assert m["window"] == [finalized - 3, finalized]
        assert "infura-eth" in m["providers"]
        hist = CheckpointStore(repo).get(ETH, "historical")
        assert hist is not None
        assert hist.cursor == finalized - 3
        assert [r for r in ranges_of(repo) if r[2] != "processed"] == []
    finally:
        engine.dispose()


def ranges_of(repo: Any) -> list[tuple[int, int, str]]:
    rows = CheckpointStore(repo).ranges("ethereum", limit=500)
    return sorted((r["from_pos"], r["to_pos"], r["status"]) for r in rows)


def test_a_broken_parent_link_in_a_backfill_window_is_reported(rig: Rig) -> None:
    n = fin(rig)
    live_eth(rig)
    rig.eth.blocks[n - 6] = {**rig.eth._block(n - 6), "hash": h32(0xDEAD)}  # pyright: ignore[reportPrivateUsage]
    m = post(rig, "/ingestion/backfill/ethereum")
    assert m["continuity_mismatches"] == 1  # n-5's parentHash no longer names n-6


def test_a_database_failure_at_commit_claims_nothing_and_leaves_no_running_status(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bip.db.errors import DatabaseError

    n = fin(rig)
    live_eth(rig)
    real = CheckpointStore.commit_batch

    def fail(self: CheckpointStore, *a: Any, **k: Any) -> Any:
        if a[1] == "historical":
            raise DatabaseError("simulated write failure")
        return real(self, *a, **k)

    monkeypatch.setattr(CheckpointStore, "commit_batch", fail)
    with TestClient(rig.app, raise_server_exceptions=False) as c:
        r = c.post(f"{API}/ingestion/backfill/ethereum")
    assert r.status_code >= 500
    hist = CheckpointStore(rig.repo).get(ETH, "historical")
    assert hist is not None
    assert hist.cursor is None
    assert hist.status != "running"
    monkeypatch.setattr(CheckpointStore, "commit_batch", real)
    assert post(rig, "/ingestion/backfill/ethereum")["window"] == [n - 9, n - 5]  # re-read


def test_a_stale_running_live_status_does_not_block_history_forever(rig: Rig) -> None:
    from datetime import timedelta

    from sqlalchemy import update

    from bip.db import schema
    from bip.db.types import utcnow

    live_eth(rig)
    CheckpointStore(rig.repo).start_run(ETH, "live", decoder_version="3")
    assert post(rig, "/ingestion/backfill/ethereum")["status"] == "deferred"  # live is running
    t = schema.ingestion_checkpoints
    with rig.repo.transaction() as conn:  # a crashed live run, ten minutes ago
        conn.execute(
            update(t)
            .where(t.c.direction == "live")
            .values(updated_at=utcnow() - timedelta(minutes=10))
        )
    assert post(rig, "/ingestion/backfill/ethereum")["status"] == "succeeded"


def test_backfill_waits_for_live_collection_to_start(rig: Rig) -> None:
    m = post(rig, "/ingestion/backfill/ethereum")
    assert m["status"] == "deferred"
    assert "has not started" in m["message"]
    assert rig.eth.calls == []


def test_the_disk_guard_refuses_backfill_before_any_provider_call(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bip.core.exceptions import StorageLimitError

    live_eth(rig)
    calls = len(rig.eth.calls)
    with TestClient(rig.app) as c:

        def full(_n: int) -> None:
            raise StorageLimitError("SYNTHETIC: free space below the floor")

        monkeypatch.setattr(rig.app.state.bip.disk_guard, "require", full)
        r = c.post(f"{API}/ingestion/backfill/ethereum")
    assert r.status_code == 507
    assert len(rig.eth.calls) == calls


def test_a_block_that_does_not_fit_the_receipt_cap_starts_the_next_run(tmp_path: Path) -> None:
    rig = Rig(
        tmp_path,
        mode="disabled",
        settings={
            "collector": {"initial_lookback_blocks": 5, "max_receipts_per_run": 2},
            "backfill": {"blocks_per_run": 5},
        },
    )
    try:
        n = fin(rig)
        live_eth(rig)
        rig.eth.add_block_tx(n - 5, h32(0xA01), addr(0xA1), addr(0xB1), 50 * WEI)
        for i in range(2):
            rig.eth.add_block_tx(n - 6, h32(0xA10 + i), addr(0xA2), addr(0xB2), 60 * WEI)
        m = post(rig, "/ingestion/backfill/ethereum")
        assert m["status"] == "partial"
        hist = CheckpointStore(rig.repo).get(ETH, "historical")
        assert hist is not None
        assert hist.cursor == n - 5  # n-6 was not half-stored behind n-5
        assert not [r for r in ranges(rig, "ethereum") if r[2] == "incomplete"]
        post(rig, "/ingestion/backfill/ethereum")  # n-6 is first now and fits
        with rig.engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT COUNT(*) FROM transfers WHERE standard='native'")
                ).scalar()
                == 3
            )
    finally:
        rig.dispose()


def test_recovery_does_not_keep_retrying_ambiguous_skipped_or_missing_slots(
    tmp_path: Path,
) -> None:
    from bip.ingest.backfill import AMBIGUOUS_REASON

    rig = sol_rig(tmp_path, recovery_positions_per_run=2)
    try:
        head = rig.sol.head_slot
        chain_blocks(rig, range(head - 12, head + 1))
        live_sol(rig)
        store = CheckpointStore(rig.repo)
        for k in range(60):  # many newer ambiguous rows, one per slot, above a real failure
            store.record_range(
                SOL,
                head - 200 + 2 * k,
                head - 200 + 2 * k,
                CoverageStatus.PROVIDER_UNAVAILABLE,
                decoder_version="1",
                reason=AMBIGUOUS_REASON,
            )
        store.record_range(
            SOL,
            head - 400,
            head - 400,
            CoverageStatus.FAILED,
            decoder_version="1",
            reason="given up after 3 failed reads",
        )
        m = post(rig, "/ingestion/recovery/solana")
        assert m["positions_targeted"] == 1  # only the real failure is retried
        assert m["positions_recovered"] == 1
    finally:
        rig.dispose()


def test_solana_backfill_keeps_finished_slots_when_the_call_budget_runs_out(
    tmp_path: Path,
) -> None:
    """The Solana reader stops before the budget is spent (as the Ethereum one does), so the
    slots it finished are committed instead of discarded with the window."""
    rig = sol_rig(tmp_path, max_rpc_calls_per_run=3)  # the head plus two of four slots
    try:
        head = rig.sol.head_slot
        chain_blocks(rig, range(head - 12, head + 1))
        live_sol(rig)  # live covers head-2 .. head
        post(rig, "/ingestion/streams/solana/live/pause")
        m = post(rig, "/ingestion/backfill/solana")
        assert m["rpc_calls"] <= 3
        assert m["status"] == "partial"
        assert m["positions_committed"] == m["positions_read"] >= 1, m  # nothing discarded
        done = {"processed", "confirmed_empty"}
        assert any(r[2] in done and r[0] >= m["cursor"] for r in ranges(rig, "solana"))
        hist = CheckpointStore(rig.repo).get(SOL, "historical")
        assert hist is not None
        assert hist.cursor is not None  # progress was claimed
    finally:
        rig.dispose()
