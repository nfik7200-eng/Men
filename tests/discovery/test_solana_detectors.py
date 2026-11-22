"""Solana discovery context: repeat program payers, unseen pool mints, mint-then-pool launches.

Detector-level tests over ``solana_discoveries`` rows shaped like the Solana collector's output
(``bip.chains.solana.programs``): programs carry ``payer``, pools carry ``mint_a``/``mint_b``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import Engine, insert

from bip.config import load_settings
from bip.db import schema
from bip.db.engine import create_db_engine
from bip.db.repository import Repository
from bip.discovery.detectors import Context, RawCandidate, solana_new_entities
from tests.discovery.seed import NOW, SOL

PAYER = "Payer1111111111111111111111111111111111111"
OTHER_PAYER = "Payer2222222222222222222222222222222222222"
MINT_X = "MintX111111111111111111111111111111111111"
MINT_Y = "MintY111111111111111111111111111111111111"
WSOL = "So11111111111111111111111111111111111111112"


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    e = create_db_engine(load_settings(data_root=tmp_path / "data", env="test"))
    yield e
    e.dispose()


class Disc:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.n = 0

    def add(self, kind: str, ref: str, when: datetime, **detail: object) -> int:
        self.n += 1
        slot = 300_000_000 + int((when - NOW).total_seconds() / 0.4) + self.n
        with self.engine.begin() as conn:
            conn.execute(
                insert(schema.solana_discoveries).values(
                    kind=kind,
                    ref=ref,
                    slot=slot,
                    blockhash=f"Hash{self.n:040d}",
                    signature=f"Sig{self.n:080d}",
                    block_time=when,
                    detail_json=json.dumps(detail),
                    finality="finalized",
                    stored_at=when,
                )
            )
        return slot

    def program(self, ref: str, payer: str, when: datetime) -> int:
        return self.add("program", ref, when, payer=payer, program_data=f"Data{ref[:30]}")

    def pool(self, ref: str, a: str, b: str, when: datetime) -> int:
        return self.add("pool", ref, when, dex="Raydium AMM v4", mint_a=a, mint_b=b)

    def mint(self, ref: str, when: datetime) -> int:
        return self.add("mint", ref, when, decimals=6, instruction="InitializeMint2")


def run(engine: Engine, now: datetime = NOW) -> dict[str, RawCandidate]:
    cfg = load_settings(data_root=Path("/unused"), env="test").discovery
    out = solana_new_entities(Context(repo=Repository(engine), cfg=cfg, chain=SOL, now=now))
    return {c.subject_ref: c for c in out.candidates}


def observed(c: RawCandidate) -> dict[str, object]:
    obs = c.explanation["observed"]
    assert isinstance(obs, dict)
    return obs  # pyright: ignore[reportUnknownVariableType]


# ------------------------------------------------------------------ programs


def test_a_repeat_payer_is_counted_and_raises_the_magnitude(engine: Engine) -> None:
    d = Disc(engine)
    d.program("ProgA", PAYER, NOW - timedelta(hours=3))
    d.program("ProgB", PAYER, NOW - timedelta(hours=2))
    d.program("ProgC", OTHER_PAYER, NOW - timedelta(hours=1))
    found = run(engine)
    a, c = found["ProgA"], found["ProgC"]
    assert observed(a)["deployer_payer"] == PAYER
    assert observed(a)["programs_by_payer_in_window"] == 2
    assert "repeat" in a.title
    assert observed(c)["programs_by_payer_in_window"] == 1
    assert "repeat" not in c.title
    assert a.magnitude > c.magnitude
    assert "payer" in str(a.explanation["caveat"])


def test_the_payer_count_uses_the_baseline_window_like_ethereum(engine: Engine) -> None:
    d = Disc(engine)
    d.program("ProgAncient", PAYER, NOW - timedelta(days=40))  # outside the 30-day baseline
    d.program("ProgMid", PAYER, NOW - timedelta(days=10))  # history, not a new candidate
    d.program("ProgNew", PAYER, NOW - timedelta(hours=1))
    found = run(engine)
    assert set(found) == {"ProgNew"}
    assert observed(found["ProgNew"])["programs_by_payer_in_window"] == 2


# ------------------------------------------------------------------ pools


def test_a_pool_with_a_mint_never_pooled_before_is_flagged(engine: Engine) -> None:
    d = Disc(engine)
    d.pool("PoolOld", MINT_X, WSOL, NOW - timedelta(days=5))  # outside the window, still history
    d.pool("PoolNew", MINT_X, MINT_Y, NOW - timedelta(hours=2))
    d.pool("PoolSeen", MINT_X, WSOL, NOW - timedelta(hours=1))
    found = run(engine)
    new, seen = found["PoolNew"], found["PoolSeen"]
    assert observed(new)["unseen_mints"] == [MINT_Y]
    assert "not seen in earlier stored pools" in new.title
    assert observed(seen)["unseen_mints"] == []
    assert new.magnitude > seen.magnitude


def test_a_mint_initialised_before_its_pool_is_linked(engine: Engine) -> None:
    d = Disc(engine)
    d.mint(MINT_Y, NOW - timedelta(hours=5))
    slot = d.pool("PoolNew", MINT_Y, WSOL, NOW - timedelta(hours=2))
    found = run(engine)
    pool = found["PoolNew"]
    links = observed(pool)["mints_initialised_in_stored_data"]
    assert isinstance(links, list)
    (link,) = cast(list[dict[str, Any]], links)
    assert link["mint"] == MINT_Y
    assert link["slot"] < slot
    assert link["hours_before_pool"] == pytest.approx(3.0)
    assert "mint initialised" in pool.title
    assert "does not show" in str(pool.explanation["caveat"])


def test_a_mint_initialised_after_the_pool_or_after_as_of_is_not_linked(engine: Engine) -> None:
    d = Disc(engine)
    as_of = NOW - timedelta(hours=1)
    d.pool("PoolNew", MINT_Y, WSOL, NOW - timedelta(hours=3))
    d.mint(MINT_Y, NOW - timedelta(minutes=30))  # after the pool, and after as_of
    found = run(engine, now=as_of)
    assert observed(found["PoolNew"])["mints_initialised_in_stored_data"] == []
    assert "mint initialised" not in found["PoolNew"].title


def test_a_mint_initialised_after_its_pool_is_not_linked(engine: Engine) -> None:
    d = Disc(engine)
    d.pool("PoolNew", MINT_Y, WSOL, NOW - timedelta(hours=3))
    d.mint(MINT_Y, NOW - timedelta(hours=2))  # before now, but after the pool
    pool = run(engine)["PoolNew"]
    assert observed(pool)["mints_initialised_in_stored_data"] == []
    assert pool.tx_ids == [pool.tx_ids[0]]


def test_pools_after_as_of_never_make_a_mint_look_seen(engine: Engine) -> None:
    d = Disc(engine)
    as_of = NOW - timedelta(hours=1)
    d.pool("PoolNew", MINT_Y, WSOL, NOW - timedelta(hours=2))
    d.pool("PoolLater", MINT_Y, WSOL, NOW - timedelta(minutes=30))
    found = run(engine, now=as_of)
    assert set(found) == {"PoolNew"}
    assert observed(found["PoolNew"])["unseen_mints"] == [MINT_Y, WSOL]


# ------------------------------------------------------------------ mints unchanged


def test_mint_candidates_keep_their_fixed_score(engine: Engine) -> None:
    d = Disc(engine)
    d.mint(MINT_X, NOW - timedelta(hours=1))
    mint = run(engine)[MINT_X]
    assert mint.magnitude == pytest.approx(0.3)
    assert mint.subject_kind == "token"
    assert mint.detector_version == "2"
