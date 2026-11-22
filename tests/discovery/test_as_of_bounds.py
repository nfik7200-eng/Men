"""Every as-of-capable detector ignores rows stored after its evaluation time.

An as-of run (ADR-047, H3) evaluates backfilled history "as it stood" at a past time. Each test
seeds rows just before and just after ``AS_OF`` and calls the detector directly with
``now=AS_OF``: only the earlier rows may count. The ``_tx_rows`` bound (dormant, burst and
deployment detectors) is covered in ``test_pipeline.py``.

Not covered by an output test: the first ``pool_reserve_change`` query (which pools had a recent
update) is a selection bound only. Its rows are re-read under the series bound tested below, so
removing it changes which pools are read, never a result, except through its 500-pool cap.
``holder_concentration`` and ``new_token_activity`` are not as-of capable and are skipped.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, insert

from bip.config import load_settings
from bip.db import schema
from bip.db.engine import create_db_engine
from bip.db.repository import Repository
from bip.discovery.detectors import (
    Context,
    common_funding_source,
    large_transfers,
    new_liquidity_pool,
    pool_reserve_change,
    solana_new_entities,
)
from tests.discovery.seed import E18, ETH, NOW, SOL, Seed, eth_addr, eth_tx

AS_OF = NOW - timedelta(days=60)
BEFORE = AS_OF - timedelta(hours=2)
AFTER = AS_OF + timedelta(hours=1)
TOKEN = eth_addr(0x7000)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    e = create_db_engine(load_settings(data_root=tmp_path / "data", env="test"))
    yield e
    e.dispose()


def ctx(engine: Engine, chain: str = ETH) -> Context:
    cfg = load_settings(data_root=Path("/unused"), env="test").discovery
    return Context(repo=Repository(engine), cfg=cfg, chain=chain, now=AS_OF)


# ------------------------------------------------------------------ large transfers


def big_transfer(seed: Seed, n: int, when: datetime) -> str:
    tx = eth_tx(n)
    seed.tx(tx, eth_addr(0x300 + n), when)
    seed.transfer(tx, eth_addr(0x300 + n), eth_addr(0x400), 5_000_000 * E18, when, token=TOKEN)
    return tx


def test_large_transfers_ignore_transfers_after_as_of(engine: Engine) -> None:
    seed = Seed(engine)
    for i in range(15):
        when = AS_OF - timedelta(days=10, hours=i)
        seed.tx(eth_tx(i + 1), eth_addr(0x100 + i), when)
        seed.transfer(
            eth_tx(i + 1), eth_addr(0x100 + i), eth_addr(0x200), (1000 + i) * E18, when, token=TOKEN
        )
    inside = big_transfer(seed, 900, BEFORE)
    future = big_transfer(seed, 901, AFTER)
    out = large_transfers(ctx(engine))
    txs = {tx for c in out.candidates for tx in c.tx_ids}
    assert inside in txs
    assert future not in txs


# ------------------------------------------------------------------ pools


def pool_event(engine: Engine, n: int, when: datetime) -> str:
    pool = eth_addr(0xA000 + n)
    with engine.begin() as conn:
        conn.execute(
            insert(schema.pool_events).values(
                chain=ETH,
                protocol="v2_like",
                factory=eth_addr(0xFAC),
                pool=pool,
                token0=eth_addr(0xB000 + n),
                token1=eth_addr(0xB100 + n),
                fee=None,
                tx_id=eth_tx(20_000 + n),
                log_index=0,
                block_number=20_000 + n,
                block_hash=f"0x{20_000 + n:064x}",
                block_time=when,
                finality="finalized",
                stored_at=when,
            )
        )
    return pool


def test_new_liquidity_pool_ignores_pools_after_as_of(engine: Engine) -> None:
    inside = pool_event(engine, 1, BEFORE)
    future = pool_event(engine, 2, AFTER)
    subjects = {c.subject_ref for c in new_liquidity_pool(ctx(engine)).candidates}
    assert subjects == {inside}
    assert future not in subjects


# ------------------------------------------------------------------ reserves


def reserve(engine: Engine, pool: str, n: int, when: datetime, r0: int, r1: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(schema.pool_reserves).values(
                chain=ETH,
                pool=pool,
                tx_id=eth_tx(30_000 + n),
                log_index=0,
                block_number=30_000 + n,
                block_hash=f"0x{30_000 + n:064x}",
                block_time=when,
                reserve0=str(r0),
                reserve1=str(r1),
                finality="finalized",
                stored_at=when,
            )
        )


def test_pool_reserve_change_ignores_updates_after_as_of(engine: Engine) -> None:
    calm, moved = eth_addr(0xC001), eth_addr(0xC002)
    # Calm before AS_OF, a tenfold jump only afterwards: no candidate as of AS_OF.
    reserve(engine, calm, 1, AS_OF - timedelta(hours=3), 100 * E18, 100 * E18)
    reserve(engine, calm, 2, BEFORE, 100 * E18, 100 * E18)
    reserve(engine, calm, 3, AFTER, 1000 * E18, 1000 * E18)
    # Positive control: a jump inside the window is still found.
    reserve(engine, moved, 4, AS_OF - timedelta(hours=3), 100 * E18, 100 * E18)
    reserve(engine, moved, 5, BEFORE, 300 * E18, 300 * E18)
    out = pool_reserve_change(ctx(engine))
    assert {c.subject_ref for c in out.candidates} == {moved}
    (cand,) = out.candidates
    assert cand.tx_ids == [eth_tx(30_004), eth_tx(30_005)]


# ------------------------------------------------------------------ Solana discoveries


def discovery(engine: Engine, n: int, when: datetime) -> str:
    ref = f"Mint{n:040d}"
    with engine.begin() as conn:
        conn.execute(
            insert(schema.solana_discoveries).values(
                kind="mint",
                ref=ref,
                slot=40_000 + n,
                blockhash=f"Hash{n:040d}",
                signature=f"Sig{n:080d}",
                block_time=when,
                detail_json=json.dumps({"decimals": 9}),
                finality="finalized",
                stored_at=when,
            )
        )
    return ref


def test_solana_new_entities_ignore_discoveries_after_as_of(engine: Engine) -> None:
    inside = discovery(engine, 1, BEFORE)
    discovery(engine, 2, AFTER)
    out = solana_new_entities(ctx(engine, SOL))
    assert {c.subject_ref for c in out.candidates} == {inside}


# ------------------------------------------------------------------ common funding


def fund(seed: Seed, source: str, n: int, when: datetime) -> str:
    recipient = eth_addr(0xE000 + n)
    seed.tx(eth_tx(50_000 + n), source, when, to=recipient)
    seed.transfer(eth_tx(50_000 + n), source, recipient, 2 * E18, when, decimals=18)
    return recipient


def test_common_funding_counts_only_recipients_funded_by_as_of(engine: Engine) -> None:
    seed = Seed(engine)
    leaky, control = eth_addr(0xF0), eth_addr(0xF1)
    # Two recipients before AS_OF and two after: below the 3-recipient minimum as of AS_OF.
    for i in range(2):
        fund(seed, leaky, i, BEFORE - timedelta(minutes=i))
        fund(seed, leaky, 10 + i, AFTER + timedelta(minutes=i))
    expected = {fund(seed, control, 20 + i, BEFORE - timedelta(minutes=i)) for i in range(3)}
    out = common_funding_source(ctx(engine))
    assert {c.subject_ref for c in out.candidates} == {control}
    (cand,) = out.candidates
    observed = cand.explanation["observed"]
    assert isinstance(observed, dict)
    assert set(observed["newly_seen_recipients"]) == expected  # pyright: ignore[reportUnknownArgumentType]
