"""SPL transfers of allowlisted mints in the Solana collector. SYNTHETIC chain data."""

from __future__ import annotations

import struct
from pathlib import Path

from sqlalchemy import text

from bip.chains.solana import programs as sp
from bip.db.checkpoints import CheckpointStore, CoverageStatus
from tests.collector.test_solana import collect, hash_of
from tests.security.helpers import Rig
from tests.stage2.fakes import block_tx, ix, key, make_block, sig

OWNER, SRC_ATA, DST_ATA = key(0x41), key(0x42), key(0x43)
USDC_LIKE, OTHER_MINT = key(0x51), key(0x52)


def checked_tx(n: int, mint: str, amount: int) -> dict[str, object]:
    keys = [OWNER, SRC_ATA, mint, DST_ATA, sp.TOKEN_PROGRAM]
    data = struct.pack("<BQB", 12, amount, 6)  # TransferChecked, 6 decimals
    tx = block_tx(sig(n), keys, [ix(4, [1, 2, 3, 0], data)])
    meta = tx["meta"]
    assert isinstance(meta, dict)
    bal = [
        {
            "accountIndex": 1,
            "mint": mint,
            "owner": OWNER,
            "uiTokenAmount": {"amount": str(10**9), "decimals": 6},
        }
    ]
    meta["preTokenBalances"] = bal
    meta["postTokenBalances"] = bal
    return tx


def rig_with(tmp_path: Path, **solana: object) -> Rig:
    return Rig(
        tmp_path, mode="disabled", settings={"solana_collector": {"blocks_per_run": 2, **solana}}
    )


def spl_rows(rig: Rig) -> list[tuple[str, str]]:
    with rig.engine.connect() as conn:
        return [
            tuple(r)
            for r in conn.execute(
                text(
                    "SELECT token, amount_raw FROM transfers "
                    "WHERE chain = 'solana' AND standard != 'native'"
                )
            )
        ]  # type: ignore[misc]


def test_allowlisted_spl_transfers_are_stored_and_others_ignored(tmp_path: Path) -> None:
    rig = rig_with(tmp_path, spl_mint_allowlist=[USDC_LIKE])
    try:
        head = rig.sol.head_slot
        rig.sol.blocks[head - 1] = make_block(
            head - 1,
            hash_of(head - 1),
            hash_of(head - 2),
            [checked_tx(1, USDC_LIKE, 2_500_000), checked_tx(2, OTHER_MINT, 7)],
        )
        m = collect(rig)
        assert m["spl_transfers_stored"] == 1
        assert spl_rows(rig) == [(USDC_LIKE, "2500000")]
        collect(rig)  # a rescan stores nothing twice
        assert len(spl_rows(rig)) == 1
    finally:
        rig.dispose()


def test_no_allowlist_means_no_spl_storage(tmp_path: Path) -> None:
    rig = rig_with(tmp_path)
    try:
        head = rig.sol.head_slot
        rig.sol.blocks[head - 1] = make_block(
            head - 1, hash_of(head - 1), hash_of(head - 2), [checked_tx(1, USDC_LIKE, 5)]
        )
        assert collect(rig)["spl_transfers_stored"] == 0
        assert spl_rows(rig) == []
    finally:
        rig.dispose()


def test_spl_cap_is_recorded_as_incomplete(tmp_path: Path) -> None:
    rig = rig_with(tmp_path, spl_mint_allowlist=[USDC_LIKE], max_spl_transfers_per_run=1)
    try:
        head = rig.sol.head_slot
        rig.sol.blocks[head - 1] = make_block(
            head - 1,
            hash_of(head - 1),
            hash_of(head - 2),
            [checked_tx(1, USDC_LIKE, 1), checked_tx(2, USDC_LIKE, 2)],
        )
        m = collect(rig)
        assert (m["spl_transfers_stored"], m["spl_transfers_over_cap"]) == (1, 1)
        gaps = CheckpointStore(rig.repo).ranges("solana", statuses=[CoverageStatus.INCOMPLETE])
        assert [(g["from_pos"], g["reason"]) for g in gaps] == [
            (head - 1, "SPL transfer(s) not stored (per-run cap)")
        ]
    finally:
        rig.dispose()
