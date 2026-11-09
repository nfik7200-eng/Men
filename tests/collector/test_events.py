"""ERC-20 transfer counting, pool reserves and the detectors built on them.

All chain data here is SYNTHETIC: a fake node serves invented blocks, tokens and pools.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from bip.chains.ethereum.pools import SYNC_TOPIC
from bip.discovery.service import DiscoveryService
from bip.ingest.collector import CURSOR_KEY
from tests.collector.test_pools import keccak256
from tests.security.helpers import API, Rig
from tests.stage2.fakes import TRANSFER, addr, h32

FACTORY, POOL, TOKEN, OTHER, NEW = (
    addr(0xFAC0),
    addr(0xB001),
    addr(0xA001),
    addr(0xA002),
    addr(0xC9),
)


def test_sync_topic_matches_the_event_signature() -> None:
    assert keccak256(b"Sync(uint112,uint112)") == SYNC_TOPIC


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    r = Rig(tmp_path, mode="disabled", settings={"collector": {"initial_lookback_blocks": 10}})
    yield r
    r.dispose()


def fin(rig: Rig) -> int:
    return rig.eth.head - 64


def populate(rig: Rig) -> None:
    n, node = fin(rig), rig.eth
    node.add_pool(n - 8, h32(0x901), FACTORY, TOKEN, OTHER, POOL)
    serial = iter(range(0xA00, 0xB00))
    # 30 transfers into three addresses, 12 small ones into others: concentrated net inflow
    for i in range(3):
        for _ in range(10):
            node.add_token_transfer(
                n - 6, h32(next(serial)), TOKEN, addr(0xF00D), addr(0x1000 + i), 100
            )
    for i in range(12):
        node.add_token_transfer(n - 5, h32(next(serial)), TOKEN, addr(0xF00D), addr(0x2000 + i), 10)
    # a token nobody asked about, four transfers
    for i in range(4):
        node.add_token_transfer(n - 4, h32(next(serial)), NEW, addr(0x3000), addr(0x3100 + i), 5)
    # an NFT transfer is counted separately, never as a token
    node.logs.append(
        node._event(  # pyright: ignore[reportPrivateUsage]
            n - 4,
            h32(0xA01),
            addr(0x7777),
            [
                TRANSFER,
                "0x" + "0" * 24 + "ab" * 20,
                "0x" + "0" * 24 + "cd" * 20,
                "0x" + "0" * 63 + "1",
            ],
            "0x",
        )
    )
    # the pool's reserves: ordinary, ordinary, then a 98 % move
    node.add_sync(n - 7, h32(0xB01), POOL, 1000, 2000)
    node.add_sync(n - 3, h32(0xB02), POOL, 1010, 1980)
    node.add_sync(n - 1, h32(0xB03), POOL, 2000, 1000)


def count(rig: Rig, sql: str, **params: str) -> int:
    with rig.engine.connect() as conn:
        return int(conn.execute(text(sql), params).scalar_one())


def collect(rig: Rig) -> dict[str, Any]:
    with TestClient(rig.app) as c:
        r = c.post(f"{API}/discovery/collect/ethereum")
    assert r.status_code == 201
    return r.json()


def test_tokens_are_counted_pooled_ones_stored_and_nfts_kept_apart(rig: Rig) -> None:
    populate(rig)
    m = collect(rig)
    assert m["status"] == "succeeded"
    assert m["tokens_seen"] == 2
    assert m["new_tokens"] == 2
    assert m["nft_transfers_skipped"] == 1
    assert m["token_transfers_stored"] == 42  # only the token with a stored pool
    assert count(rig, "SELECT transfers_seen FROM token_stats WHERE token = :t", t=NEW) == 4
    assert count(rig, "SELECT COUNT(*) FROM transfers WHERE token = :t", t=NEW) == 0
    # the first transaction of each new token is stored, so signals can cite it
    assert count(rig, "SELECT COUNT(*) FROM transactions WHERE tx_id = :t", t=h32(0xA00 + 42)) == 1
    assert m["reserve_updates_stored"] == 3
    assert m["rpc_calls"] <= 150


def test_a_rescan_does_not_count_a_block_twice(rig: Rig) -> None:
    populate(rig)
    first = collect(rig)
    rig.repo.set_meta(CURSOR_KEY, str(fin(rig) - 10))  # pretend the cursor update was lost
    again = collect(rig)
    assert again["blocks_scanned"] > 0
    assert again["token_transfers_stored"] == 0
    assert again["reserve_updates_stored"] == 0
    assert count(rig, "SELECT transfers_seen FROM token_stats WHERE token = :t", t=NEW) == 4
    assert count(rig, "SELECT COUNT(*) FROM pool_reserves") == 3
    assert (first["new_tokens"], again["new_tokens"]) == (2, 0)


def test_detectors_raise_evidence_backed_signals_from_the_collected_data(rig: Rig) -> None:
    populate(rig)
    collect(rig)
    with rig.engine.connect() as conn:
        latest = conn.execute(text("SELECT MAX(block_time) FROM pool_reserves")).scalar_one()
    when = datetime.fromisoformat(str(latest)).replace(tzinfo=UTC) + timedelta(hours=1)
    service = DiscoveryService(rig.repo, rig.settings, clock=lambda: when)
    result = service.run("ethereum")["detectors"]
    for detector in ("new_token_activity", "pool_reserve_change", "holder_concentration"):
        assert result[f"{detector}:ethereum"]["created"] >= 1, detector
    items = service.list_signals(chain="ethereum", limit=50, offset=0)["items"]
    by = {s["detector"]["id"]: s for s in items}
    reserve = by["pool_reserve_change"]
    assert reserve["subject"] == {"kind": "pool", "ref": POOL}
    assert reserve["confidence"] <= 0.5
    holders = by["holder_concentration"]
    assert holders["subject"]["ref"] == TOKEN
    assert "not balances" in holders["explanation"]["caveat"]
    assert holders["confidence"] <= 0.4
    assert by["new_token_activity"]["confidence"] <= 0.35


@pytest.mark.parametrize("group", ["ingest", "collector"])
@pytest.mark.parametrize(
    "override", [{"max_rpc_calls_per_run": 151}, {"run_deadline_s": 91}], ids=["calls", "seconds"]
)
def test_provider_use_per_invocation_cannot_exceed_the_authorised_limits(
    group: str, override: dict[str, float]
) -> None:
    """At most 150 provider requests and 90 seconds per invocation, whatever the configuration."""
    from bip.config import load_settings
    from bip.core.exceptions import ConfigurationError

    with pytest.raises(ConfigurationError, match="less than or equal"):
        load_settings(data_root=Path("/unused"), env="test", **{group: override})
