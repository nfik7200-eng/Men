"""Pool-creation events: decoded, stored with their transaction, deduplicated, bounded."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from bip.chains.ethereum.pools import PAIR_CREATED_TOPIC, POOL_CREATED_TOPIC
from bip.discovery.service import DiscoveryService
from bip.entities import views
from tests.security.helpers import API, Rig
from tests.stage2.fakes import addr, h32

# ----------------------------------------------------------------- keccak-256 (test-only)

_RC = [
    0x1, 0x8082, 0x800000000000808A, 0x8000000080008000, 0x808B, 0x80000001,
    0x8000000080008081, 0x8000000000008009, 0x8A, 0x88, 0x80008009, 0x8000000A,
    0x8000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x80000001, 0x8000000080008008,
]  # fmt: skip
_ROT = [1, 3, 6, 10, 15, 21, 28, 36, 45, 55, 2, 14, 27, 41, 56, 8, 25, 43, 62, 18, 39, 61, 20, 44]
_PI = [10, 7, 11, 17, 18, 3, 5, 16, 8, 21, 24, 4, 15, 23, 19, 13, 12, 2, 20, 14, 22, 9, 6, 1]
_M = (1 << 64) - 1


def _rol(x: int, n: int) -> int:
    return ((x << n) | (x >> (64 - n))) & _M


def keccak256(data: bytes) -> str:
    rate = 136
    padded = bytearray(data) + b"\x01"
    padded += b"\x00" * (-len(padded) % rate)
    padded[-1] |= 0x80
    s = [0] * 25
    for off in range(0, len(padded), rate):
        for i in range(rate // 8):
            s[i] ^= int.from_bytes(padded[off + 8 * i : off + 8 * i + 8], "little")
        for rc in _RC:
            c = [s[x] ^ s[x + 5] ^ s[x + 10] ^ s[x + 15] ^ s[x + 20] for x in range(5)]
            d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
            s = [s[i] ^ d[i % 5] for i in range(25)]
            t = s[1]
            for i in range(24):
                j = _PI[i]
                s[j], t = _rol(t, _ROT[i]), s[j]
            for y in range(0, 25, 5):
                row = s[y : y + 5]
                for x in range(5):
                    s[y + x] = row[x] ^ (~row[(x + 1) % 5] & row[(x + 2) % 5])
            s[0] ^= rc
    return "0x" + b"".join(v.to_bytes(8, "little") for v in s[:4]).hex()


def test_keccak_matches_a_known_vector_and_the_topic_constants() -> None:
    assert keccak256(b"Transfer(address,address,uint256)").startswith("0xddf252ad1be2c89b")
    assert keccak256(b"PairCreated(address,address,address,uint256)") == PAIR_CREATED_TOPIC
    assert keccak256(b"PoolCreated(address,address,uint24,int24,address)") == POOL_CREATED_TOPIC


# ---------------------------------------------------------------------- collection

ETH = 10**18
FACTORY = addr(0xFAC0)


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    r = Rig(tmp_path, mode="disabled", settings={"collector": {"initial_lookback_blocks": 10}})
    yield r
    r.dispose()


def fin(rig: Rig) -> int:
    return rig.eth.head - 64


def count(rig: Rig, table: str) -> int:
    with rig.engine.connect() as conn:
        return int(conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())  # noqa: S608


def test_pools_are_decoded_stored_with_their_transaction_and_deduplicated(rig: Rig) -> None:
    n = fin(rig)
    rig.eth.add_pool(n - 3, h32(0x801), FACTORY, addr(0xA0), addr(0xA1), addr(0xB0))
    rig.eth.add_pool(n - 2, h32(0x802), FACTORY, addr(0xA0), addr(0xA2), addr(0xB1), v3_fee=3000)
    with TestClient(rig.app) as c:
        m = c.post(f"{API}/discovery/collect/ethereum").json()
        assert (m["pool_events_found"], m["pool_events_stored"]) == (2, 2)
        again = c.post(f"{API}/discovery/collect/ethereum").json()
        assert again["pool_events_stored"] == 0
    assert count(rig, "pool_events") == 2
    with rig.engine.connect() as conn:
        rows = conn.execute(
            text("SELECT protocol, pool, token0, token1, fee FROM pool_events ORDER BY id")
        ).all()
        assert rows[0] == ("v2_like", addr(0xB0), addr(0xA0), addr(0xA1), None)
        assert rows[1] == ("v3_like", addr(0xB1), addr(0xA0), addr(0xA2), 3000)
        # the creating transaction is stored, so the signal can cite it as evidence
        assert (
            conn.execute(
                text("SELECT COUNT(*) FROM transactions WHERE tx_id = :t"), {"t": h32(0x801)}
            ).scalar_one()
            == 1
        )


def test_a_log_from_another_block_hash_is_rejected(rig: Rig) -> None:
    rig.eth.add_pool(fin(rig) - 2, h32(0x811), FACTORY, addr(0xA0), addr(0xA1), addr(0xB0))
    rig.eth.logs[-1]["blockHash"] = h32(0xBAD)
    with TestClient(rig.app) as c:
        m = c.post(f"{API}/discovery/collect/ethereum").json()
    assert m["pool_events_stored"] == 0
    assert m["rejected"] >= 1
    assert count(rig, "pool_events") == 0


def test_pools_beyond_the_cap_are_a_disclosed_gap(tmp_path: Path) -> None:
    rig = Rig(
        tmp_path,
        mode="disabled",
        settings={"collector": {"max_pools_per_run": 1, "initial_lookback_blocks": 10}},
    )
    try:
        n = fin(rig)
        rig.eth.add_pool(n - 3, h32(0x821), FACTORY, addr(0xA0), addr(0xA1), addr(0xB0))
        rig.eth.add_pool(n - 2, h32(0x822), FACTORY, addr(0xA2), addr(0xA3), addr(0xB1))
        with TestClient(rig.app) as c:
            m = c.post(f"{API}/discovery/collect/ethereum").json()
        assert (m["pool_events_stored"], m["pool_events_over_cap"]) == (1, 1)
    finally:
        rig.dispose()


def test_a_pool_that_reuses_a_token_is_scored_lower_than_one_with_new_tokens(rig: Rig) -> None:
    n = fin(rig)
    rig.eth.add_pool(n - 3, h32(0x831), FACTORY, addr(0xA0), addr(0xA1), addr(0xB0))
    rig.eth.add_pool(n - 2, h32(0x832), FACTORY, addr(0xA0), addr(0xA1), addr(0xB1))
    with TestClient(rig.app) as c:
        c.post(f"{API}/discovery/collect/ethereum")
    with rig.engine.connect() as conn:
        latest = conn.execute(text("SELECT MAX(block_time) FROM pool_events")).scalar_one()
    when = datetime.fromisoformat(str(latest)).replace(tzinfo=UTC) + timedelta(hours=1)
    service = DiscoveryService(rig.repo, rig.settings, clock=lambda: when)
    result = service.run("ethereum")
    assert result["detectors"]["new_liquidity_pool:ethereum"]["created"] == 2
    items = service.list_signals(chain="ethereum", limit=10, offset=0)["items"]
    by_pool = {s["subject"]["ref"]: s for s in items if s["detector"]["id"] == "new_liquidity_pool"}
    assert by_pool[addr(0xB0)]["score"] > by_pool[addr(0xB1)]["score"]
    assert by_pool[addr(0xB0)]["subject"]["kind"] == "pool"
    assert by_pool[addr(0xB0)]["confidence"] <= 0.4
    hit = views.search(rig.repo, addr(0xB1)[:12], None, 10)["items"]
    assert {"kind": "pool", "chain": "ethereum", "ref": addr(0xB1)} in hit
