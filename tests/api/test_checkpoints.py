"""BIP-owned checkpoints and coverage ranges (schema v16)."""

from __future__ import annotations

import pytest

from bip.db.checkpoints import (
    CheckpointStatus,
    CheckpointStore,
    CoverageStatus,
    StreamRef,
)
from bip.db.repository import Repository

ETH = StreamRef("ethereum", "collector")
SOL = StreamRef("solana", "collector")


@pytest.fixture
def store(repo: Repository) -> CheckpointStore:
    return CheckpointStore(repo)


def test_stream_keeps_chain_specific_network_and_position_kind() -> None:
    assert (ETH.network, ETH.position_kind) == ("mainnet", "block")
    assert (SOL.network, SOL.position_kind) == ("mainnet-beta", "slot")


def test_live_cursor_moves_forward_only_after_a_committed_batch(store: CheckpointStore) -> None:
    assert store.get(ETH, "live") is None
    cp = store.commit_batch(
        ETH,
        "live",
        from_pos=100,
        to_pos=119,
        decoder_version="3",
        cursor_hash="0xa",
        providers=["p2", "p1", "p1"],
    )
    assert (cp.cursor, cp.cursor_hash, cp.last_batch_from, cp.last_batch_to) == (
        119,
        "0xa",
        100,
        119,
    )
    assert cp.last_providers == ["p1", "p2"]
    assert cp.position_kind == "block"
    assert cp.status == "idle"
    # an idempotent rescan of an older range never moves the cursor backward
    cp = store.commit_batch(
        ETH, "live", from_pos=90, to_pos=95, decoder_version="3", cursor_hash="0xold"
    )
    assert (cp.cursor, cp.cursor_hash) == (119, "0xa")


def test_historical_and_live_progress_are_independent(store: CheckpointStore) -> None:
    store.commit_batch(SOL, "live", from_pos=5000, to_pos=5010, decoder_version="1")
    cp = store.commit_batch(SOL, "historical", from_pos=4000, to_pos=4100, decoder_version="1")
    assert cp.cursor == 4000  # historical moves backward, toward older slots
    assert store.get(SOL, "live").cursor == 5010  # type: ignore[union-attr]
    cp = store.commit_batch(SOL, "historical", from_pos=4050, to_pos=4060, decoder_version="1")
    assert cp.cursor == 4000  # never forward


def test_a_provider_failure_changes_status_but_never_resets_the_cursor(
    store: CheckpointStore,
) -> None:
    store.commit_batch(ETH, "live", from_pos=1, to_pos=50, decoder_version="3")
    cp = store.mark(
        ETH,
        "live",
        CheckpointStatus.STALLED,
        decoder_version="3",
        error_category="provider_unavailable",
        detail="https://rpc.example/key=SECRETKEY123 timed out",
    )
    assert (cp.cursor, cp.status, cp.last_error_category) == (50, "stalled", "provider_unavailable")
    assert "SECRETKEY123" not in cp.recovery["last_error"]
    cp = store.commit_batch(ETH, "live", from_pos=51, to_pos=60, decoder_version="3")
    assert (cp.cursor, cp.status) == (60, "idle")
    assert cp.last_error_category == "provider_unavailable"  # kept as history


def test_mark_on_a_new_stream_creates_it_without_a_cursor(store: CheckpointStore) -> None:
    cp = store.mark(
        SOL,
        "live",
        CheckpointStatus.BLOCKED,
        decoder_version="1",
        error_category="no_eligible_provider",
    )
    assert cp.cursor is None
    assert cp.status == "blocked"


def test_rejects_inverted_ranges(store: CheckpointStore) -> None:
    with pytest.raises(ValueError, match="to_pos must be >= from_pos"):
        store.commit_batch(ETH, "live", from_pos=5, to_pos=4, decoder_version="3")
    with pytest.raises(ValueError, match="to_pos must be >= from_pos"):
        store.record_range(ETH, 5, 4, CoverageStatus.PROCESSED, decoder_version="3")


def test_adjacent_ranges_with_the_same_meaning_merge(store: CheckpointStore) -> None:
    for n in range(100, 110):
        store.record_range(
            ETH, n, n, CoverageStatus.PROCESSED, decoder_version="3", provider_id="p1"
        )
    store.record_range(
        ETH, 110, 110, CoverageStatus.PROCESSED, decoder_version="3", provider_id="p2"
    )  # another provider is another row
    store.record_range(
        ETH,
        105,
        105,
        CoverageStatus.INCOMPLETE,
        decoder_version="3",
        provider_id="p1",
        reason="receipt cap",
    )
    rows = store.ranges("ethereum")
    assert [(r["from_pos"], r["to_pos"], r["status"], r["provider_id"]) for r in rows] == [
        (110, 110, "processed", "p2"),
        (105, 105, "incomplete", "p1"),
        (100, 109, "processed", "p1"),
    ]
    # recording the exact same range again only refreshes it
    store.record_range(
        ETH,
        105,
        105,
        CoverageStatus.INCOMPLETE,
        decoder_version="3",
        provider_id="p1",
        reason="receipt cap",
        attempts=2,
    )
    gaps = store.ranges("ethereum", statuses=[CoverageStatus.INCOMPLETE])
    assert len(gaps) == 1
    assert gaps[0]["attempts"] == 2


def test_a_new_decoder_version_starts_a_new_range(store: CheckpointStore) -> None:
    store.record_range(SOL, 1, 5, CoverageStatus.PROCESSED, decoder_version="1")
    store.record_range(SOL, 6, 9, CoverageStatus.PROCESSED, decoder_version="2")
    assert len(store.ranges("solana")) == 2


def test_summary_never_reports_unobserved_ranges_as_empty(store: CheckpointStore) -> None:
    store.record_range(
        SOL, 100, 109, CoverageStatus.PROCESSED, decoder_version="1", provider_id="solana-public"
    )
    store.record_range(
        SOL,
        110,
        110,
        CoverageStatus.CONFIRMED_EMPTY,
        decoder_version="1",
        provider_id="solana-public",
    )
    store.record_range(
        SOL, 200, 210, CoverageStatus.SKIPPED, decoder_version="1", reason="lag exceeded"
    )
    s = store.summary("solana", "collector")
    assert s["by_status"]["processed"]["positions"] == 10
    assert s["by_status"]["confirmed_empty"]["positions"] == 1
    assert s["by_status"]["skipped"]["positions"] == 11
    assert (s["lowest_observed"], s["highest_observed"]) == (100, 210)
    assert s["providers"] == ["solana-public"]
    # 111..199 has no row at all: it is "not observed", and nothing counts it as empty
    assert sum(v["positions"] for v in s["by_status"].values()) == 22


def test_reasons_are_redacted(store: CheckpointStore) -> None:
    store.record_range(
        ETH,
        1,
        1,
        CoverageStatus.FAILED,
        decoder_version="3",
        reason="https://mainnet.example/v2/abcdef0123456789abcdef failed",
    )
    assert "abcdef0123456789abcdef" not in str(store.ranges("ethereum")[0]["reason"])


def test_a_rescan_inside_a_merged_range_adds_nothing(store: CheckpointStore) -> None:
    for n in range(100, 110):
        store.record_range(ETH, n, n, CoverageStatus.PROCESSED, decoder_version="3")
    store.record_range(ETH, 105, 105, CoverageStatus.PROCESSED, decoder_version="3")
    store.record_range(ETH, 98, 101, CoverageStatus.PROCESSED, decoder_version="3")  # overlaps
    rows = store.ranges("ethereum")
    assert [(r["from_pos"], r["to_pos"]) for r in rows] == [(98, 109)]
    assert store.summary("ethereum", "collector")["by_status"]["processed"]["positions"] == 12


def test_filling_a_hole_joins_both_sides(store: CheckpointStore) -> None:
    store.record_range(SOL, 1, 5, CoverageStatus.PROCESSED, decoder_version="1")
    store.record_range(SOL, 9, 12, CoverageStatus.PROCESSED, decoder_version="1")
    assert len(store.ranges("solana")) == 2  # the hole 6..8 is never bridged on its own
    store.record_range(SOL, 6, 8, CoverageStatus.PROCESSED, decoder_version="1")
    assert [(r["from_pos"], r["to_pos"]) for r in store.ranges("solana")] == [(1, 12)]


def test_a_later_successful_read_resolves_a_failed_gap_but_not_an_incomplete_one(
    store: CheckpointStore,
) -> None:
    store.record_range(ETH, 7, 7, CoverageStatus.FAILED, decoder_version="3", reason="given up")
    store.record_range(ETH, 8, 8, CoverageStatus.INCOMPLETE, decoder_version="3", reason="cap")
    store.record_range(ETH, 20, 30, CoverageStatus.SKIPPED, decoder_version="3", reason="lag")
    store.record_range(ETH, 6, 9, CoverageStatus.PROCESSED, decoder_version="3")
    store.record_range(ETH, 20, 25, CoverageStatus.PROCESSED, decoder_version="3")
    open_gaps = store.ranges(
        "ethereum",
        statuses=[CoverageStatus.FAILED, CoverageStatus.INCOMPLETE, CoverageStatus.SKIPPED],
        include_resolved=False,
    )
    # failed 7 is resolved; incomplete 8 qualifies the read; skipped 20..30 is only half re-read
    assert sorted((r["from_pos"], r["status"]) for r in open_gaps) == [
        (8, "incomplete"),
        (20, "skipped"),
    ]
    s = store.summary("ethereum", "collector")
    assert "failed" not in s["by_status"]
    assert s["resolved_gap_ranges"] == 1


def test_cursor_hash_always_belongs_to_the_cursor(store: CheckpointStore) -> None:
    store.commit_batch(ETH, "live", from_pos=1, to_pos=10, decoder_version="3", cursor_hash="0xten")
    cp = store.commit_batch(ETH, "live", from_pos=11, to_pos=12, decoder_version="3")
    assert (cp.cursor, cp.cursor_hash) == (12, None)  # block 12 had no known hash


def test_skip_to_records_skipped_positions_without_claiming_a_committed_batch(
    store: CheckpointStore,
) -> None:
    store.commit_batch(
        SOL, "live", from_pos=90, to_pos=100, decoder_version="1", cursor_hash="H100"
    )
    cp = store.skip_to(SOL, 5000, decoder_version="1", reason="lag exceeded max_lag_slots")
    assert (cp.cursor, cp.cursor_hash) == (5000, None)
    assert (cp.last_batch_from, cp.last_batch_to) == (90, 100)
    skipped = store.ranges("solana", statuses=[CoverageStatus.SKIPPED])
    assert [(r["from_pos"], r["to_pos"]) for r in skipped] == [(101, 5000)]
    assert store.skip_to(SOL, 10, decoder_version="1", reason="x").cursor == 5000  # never back


def test_historical_checkpoint_completes_at_its_boundary(store: CheckpointStore) -> None:
    store.set_boundary(SOL, 1000, decoder_version="1")
    cp = store.commit_batch(SOL, "historical", from_pos=1200, to_pos=1300, decoder_version="1")
    assert (cp.cursor, cp.status) == (1200, "idle")
    cp = store.commit_batch(SOL, "historical", from_pos=1000, to_pos=1199, decoder_version="1")
    assert (cp.cursor, cp.status, cp.boundary) == (1000, "complete", 1000)


def test_run_lifecycle_never_leaves_running_and_keeps_failures(store: CheckpointStore) -> None:
    store.start_run(ETH, "live", decoder_version="3")
    assert store.get(ETH, "live").status == "running"  # type: ignore[union-attr]
    store.finish_run(ETH, "live", head_seen=500)
    cp = store.get(ETH, "live")
    assert cp is not None
    assert (cp.status, cp.head_seen, cp.lag) == ("idle", 500, None)  # no cursor yet: no lag
    store.start_run(ETH, "live", decoder_version="3")
    store.mark(
        ETH, "live", CheckpointStatus.STALLED, decoder_version="3", error_category="read_failed"
    )
    store.finish_run(ETH, "live", head_seen=510)
    assert store.get(ETH, "live").status == "stalled"  # type: ignore[union-attr]


def test_a_batch_inside_a_run_keeps_the_run_visible(store: CheckpointStore) -> None:
    store.start_run(SOL, "live", decoder_version="1")
    cp = store.commit_batch(SOL, "live", from_pos=1, to_pos=1, decoder_version="1")
    assert cp.status == "running"
    store.finish_run(SOL, "live")
    assert store.get(SOL, "live").status == "idle"  # type: ignore[union-attr]
