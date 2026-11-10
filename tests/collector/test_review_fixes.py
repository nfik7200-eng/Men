"""Regression tests for the independent review of the collectors (all chain data SYNTHETIC)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from bip.core.exceptions import ConfigurationError
from bip.ingest.collector import ATTEMPTS_KEY, CURSOR_KEY, GAPS_KEY
from tests.security.helpers import API, Rig
from tests.stage2.fakes import addr, h32

ETH = 10**18
FACTORY, OTHER_FACTORY = addr(0xFAC0), addr(0xFAC1)
POOL, TOKEN, OTHER, NEW = addr(0xB001), addr(0xA001), addr(0xA002), addr(0xC9)


def make(tmp_path: Path, **collector: Any) -> Rig:
    return Rig(
        tmp_path,
        mode="disabled",
        settings={
            "collector": {
                "initial_lookback_blocks": 6,
                "blocks_per_run": 6,
                "extra_factories": [FACTORY],
                **collector,
            }
        },
    )


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    r = make(tmp_path)
    yield r
    r.dispose()


def fin(rig: Rig) -> int:
    return rig.eth.head - 64


def collect(rig: Rig) -> dict[str, Any]:
    with TestClient(rig.app) as c:
        r = c.post(f"{API}/discovery/collect/ethereum")
    assert r.status_code == 201
    return r.json()


def count(rig: Rig, sql: str, **params: str) -> int:
    with rig.engine.connect() as conn:
        return int(conn.execute(text(sql), params).scalar_one())


def gaps(rig: Rig) -> list[dict[str, Any]]:
    return json.loads(rig.repo.get_meta(GAPS_KEY) or "[]")


def test_token_counts_are_written_once_even_if_the_feed_is_interrupted(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bip.router.router import BudgetExhaustedError

    n = fin(rig)
    for i in range(4):
        rig.eth.add_token_transfer(n - 2, h32(0xA00 + i), NEW, addr(0x3000), addr(0x3100 + i), 5)
    with TestClient(rig.app) as c:
        ctx: Any = rig.app.state.bip
        feeds: Any = ctx.collector._feeds  # pyright: ignore[reportPrivateUsage]
        real = feeds._store_evidence_tx  # pyright: ignore[reportPrivateUsage]

        def exhausted(*args: Any, **kwargs: Any) -> bool:
            raise BudgetExhaustedError("synthetic: the call budget ran out mid-update")

        monkeypatch.setattr(feeds, "_store_evidence_tx", exhausted)
        failed = c.post(f"{API}/discovery/collect/ethereum").json()
        assert failed["status"] == "partial"
        assert count(rig, "SELECT COUNT(*) FROM token_stats") == 0  # nothing half-applied
        monkeypatch.setattr(feeds, "_store_evidence_tx", real)
        done = c.post(f"{API}/discovery/collect/ethereum").json()
    assert done["new_tokens"] == 1
    assert count(rig, "SELECT transfers_seen FROM token_stats WHERE token = :t", t=NEW) == 4
    assert rig.repo.get_meta(CURSOR_KEY) == str(n)


def test_a_provider_error_fetching_evidence_does_not_abort_the_feed(rig: Rig) -> None:
    n = fin(rig)
    rig.eth.add_token_transfer(n - 2, h32(0xA10), NEW, addr(0x3000), addr(0x3100), 5)
    rig.eth.fail_tx.add(h32(0xA10))
    m = collect(rig)
    assert m["status"] == "succeeded"
    assert m["evidence_fetch_failed"] >= 1
    assert m["new_tokens_without_evidence_tx"] == 1
    assert count(rig, "SELECT transfers_seen FROM token_stats WHERE token = :t", t=NEW) == 1


def test_a_block_that_always_fails_is_given_up_and_earlier_blocks_still_get_their_feeds(
    rig: Rig,
) -> None:
    n = fin(rig)
    rig.eth.add_pool(n - 4, h32(0x901), FACTORY, TOKEN, OTHER, POOL)
    rig.eth.corrupt_blocks.add(n - 3)
    first = collect(rig)
    assert first["status"] == "partial"
    assert first["pool_events_stored"] == 1  # blocks before the bad one were not wasted
    assert rig.repo.get_meta(CURSOR_KEY) == str(n - 4)
    collect(rig)
    third = collect(rig)  # third failed read: give the block up and move on
    assert third["blocks_given_up"] == 1
    assert any(g["from_block"] == n - 3 and "given up" in g["reason"] for g in gaps(rig))
    assert rig.repo.get_meta(CURSOR_KEY) == str(n)
    assert json.loads(rig.repo.get_meta(ATTEMPTS_KEY) or "{}") == {}


def test_a_block_is_never_half_stored_behind_other_blocks(tmp_path: Path) -> None:
    rig = make(tmp_path, max_receipts_per_run=2)
    try:
        n = fin(rig)
        rig.eth.add_block_tx(n - 3, h32(0x501), addr(0xA1), addr(0xB1), 50 * ETH)
        for i in range(3):
            rig.eth.add_block_tx(n - 2, h32(0x510 + i), addr(0xA2), addr(0xB2), 60 * ETH)
        first = collect(rig)
        assert first["large_transfers_stored"] == 1  # block n-2 needs 3 receipts, only 1 left
        assert first["status"] == "partial"
        assert rig.repo.get_meta(CURSOR_KEY) == str(n - 3)
        second = collect(rig)  # first block of the run: takes what fits, records the rest
        assert second["large_transfers_stored"] == 2
        assert [g["from_block"] for g in gaps(rig)] == [n - 2]
        assert "1 qualifying transaction(s) not stored" in gaps(rig)[0]["reason"]
    finally:
        rig.dispose()


def test_a_reverted_creation_is_not_a_deployment(rig: Rig) -> None:
    n = fin(rig)
    rig.eth.add_block_tx(n - 2, h32(0x601), addr(0xD1), None, 0, contract=addr(0xC1))
    rig.eth.failed_txs.add(h32(0x601))
    m = collect(rig)
    assert m["contract_creations_stored"] == 0
    assert count(rig, "SELECT COUNT(*) FROM transactions WHERE created_contract IS NOT NULL") == 0


def test_a_pool_is_still_recorded_when_its_evidence_transaction_is_missing(rig: Rig) -> None:
    n = fin(rig)
    rig.eth.add_pool(n - 3, h32(0x911), FACTORY, TOKEN, OTHER, POOL)
    rig.eth.fail_tx.add(h32(0x911))
    m = collect(rig)
    assert m["pool_events_stored"] == 1
    assert m["pool_evidence_missing"] == 1
    assert rig.repo.get_meta(CURSOR_KEY) == str(n)


def test_pools_from_an_unrecognised_factory_never_feed_transfers_or_reserves(rig: Rig) -> None:
    n = fin(rig)
    rig.eth.add_pool(n - 4, h32(0x921), OTHER_FACTORY, TOKEN, OTHER, POOL)  # not trusted
    rig.eth.add_token_transfer(n - 3, h32(0x922), TOKEN, addr(0x3000), addr(0x3100), 7)
    rig.eth.add_sync(n - 2, h32(0x923), POOL, 1000, 2000)
    m = collect(rig)
    assert m["pool_events_stored"] == 1  # recorded as an observation
    assert m["token_transfers_stored"] == 0
    assert m["reserve_pools_watched"] == 0
    assert count(rig, "SELECT COUNT(*) FROM pool_reserves") == 0


def test_trusted_factories_are_stored_first_when_the_pool_cap_is_hit(tmp_path: Path) -> None:
    rig = make(tmp_path, max_pools_per_run=1)
    try:
        n = fin(rig)
        rig.eth.add_pool(
            n - 5, h32(0x931), OTHER_FACTORY, TOKEN, OTHER, addr(0xB002)
        )  # fake, older
        rig.eth.add_pool(n - 4, h32(0x932), FACTORY, TOKEN, OTHER, addr(0xB003))
        m = collect(rig)
        assert (m["pool_events_stored"], m["pool_events_over_cap"]) == (1, 1)
        assert count(rig, "SELECT COUNT(*) FROM pool_events WHERE factory = :f", f=FACTORY) == 1
        assert any("over the per-run cap" in g["reason"] for g in gaps(rig))
    finally:
        rig.dispose()


def test_one_block_with_too_many_logs_is_skipped_and_recorded_not_a_permanent_stall(
    rig: Rig,
) -> None:
    n = fin(rig)
    rig.eth.huge_log_blocks.add(n - 3)
    m = collect(rig)
    assert m["status"] == "succeeded"
    assert m["feed_blocks_skipped"] >= 1
    assert rig.repo.get_meta(CURSOR_KEY) == str(n)  # the cursor moved past it
    assert any(g["from_block"] == n - 3 and "response limit" in g["reason"] for g in gaps(rig))


def test_tokens_beyond_the_tracking_cap_are_a_recorded_gap(tmp_path: Path) -> None:
    rig = make(tmp_path, max_tracked_tokens=1000)
    try:
        n = fin(rig)
        with rig.engine.begin() as conn:  # fill the table to its cap with SYNTHETIC rows
            for i in range(1000):
                conn.execute(
                    text(
                        "INSERT INTO token_stats (chain, token, first_seen_block, first_seen_at, "
                        "first_tx_id, transfers_seen, last_seen_block) VALUES "
                        "('ethereum', :t, 1, '2026-01-01', 'x', 1, 1)"
                    ),
                    {"t": addr(0x10000 + i)},
                )
        rig.eth.add_token_transfer(n - 2, h32(0xA01), NEW, addr(0x3000), addr(0x3100), 5)
        m = collect(rig)
        assert m["tokens_not_tracked"] == 1
        assert any("not tracked" in g["reason"] for g in gaps(rig))
    finally:
        rig.dispose()


def test_manifest_messages_are_redacted_of_secrets(rig: Rig) -> None:
    from bip.security.secrets import register_secret

    register_secret("synthetic-provider-key-123")
    rig.eth.unsupported.add("eth_getBlockByNumber")  # provider error text reaches the message
    m = collect(rig)
    assert "synthetic-provider-key-123" not in json.dumps(m)


def test_an_unaffordable_call_budget_is_refused_when_settings_load() -> None:
    from bip.config import load_settings

    with pytest.raises(ConfigurationError, match="exceeds"):
        load_settings(
            data_root=Path("/unused"), env="test", collector={"max_receipts_per_run": 150}
        )
    with pytest.raises(ConfigurationError, match="0x-prefixed"):
        load_settings(
            data_root=Path("/unused"), env="test", collector={"extra_factories": ["nope"]}
        )
    ok = load_settings(
        data_root=Path("/unused"), env="test", collector={"extra_factories": "0x" + "A" * 40}
    )
    assert ok.collector.extra_factories == ("0x" + "a" * 40,)


def test_a_pool_late_in_the_alphabet_is_still_evaluated_when_the_table_is_large(
    tmp_path: Path,
) -> None:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import insert

    from bip.db import schema
    from bip.discovery.service import DiscoveryService

    rig = Rig(tmp_path, mode="disabled", settings={"discovery": {"max_rows_scanned": 100}})
    try:
        now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)

        def row(pool: str, n: int, when: datetime, r0: int, r1: int) -> dict[str, Any]:
            return {
                "chain": "ethereum", "pool": pool, "tx_id": h32(0x100000 + n), "log_index": 0,
                "block_number": n, "block_hash": h32(0x200000 + n), "block_time": when,
                "reserve0": str(r0), "reserve1": str(r1), "finality": "finalized", "stored_at": now,
            }  # fmt: skip

        old_pool, late_pool = addr(0xA0), addr(0xF0)  # sorts first / last
        rows = [
            row(old_pool, i, now - timedelta(days=10, minutes=i), 1000, 1000) for i in range(150)
        ]
        rows.append(row(late_pool, 1000, now - timedelta(hours=3), 1000, 1000))
        rows.append(row(late_pool, 1001, now - timedelta(hours=1), 3000, 1000))  # +200 %
        with rig.engine.begin() as conn:
            conn.execute(insert(schema.pool_reserves), rows)
        service = DiscoveryService(rig.repo, rig.settings, clock=lambda: now)
        out = service.run("ethereum")["detectors"]["pool_reserve_change:ethereum"]
        assert out["created"] == 1
        subjects = [
            s["subject"]["ref"]
            for s in service.list_signals(chain="ethereum", limit=5, offset=0)["items"]
        ]
        assert late_pool in subjects
    finally:
        rig.dispose()
