"""The Solana finalized-slot collector. Every block, mint, program and pool here is SYNTHETIC."""

from __future__ import annotations

import json
import struct
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from bip.chains.solana import programs as sp
from bip.discovery.service import DiscoveryService
from bip.ingest.solana_collector import CURSOR_KEY, GAPS_KEY
from tests.security.helpers import API, PASSWORD, Rig
from tests.stage2.fakes import block_tx, ix, key, make_block, sig

SYSTEM, TOKEN = sp.SYSTEM_PROGRAM, sp.TOKEN_PROGRAM
PAYER, DEST = key(0x61), key(0x62)


def hash_of(slot: int) -> str:
    return key(slot % 200 + 20)


def _transfer(lamports: int) -> bytes:
    return struct.pack("<IQ", 2, lamports)


def mint_tx(n: int, mint: str, *, tag: int = 0, err: object = None) -> dict[str, object]:
    keys = [PAYER, mint, TOKEN]
    data = bytes([tag, 6]) + b"\x01" * 32 + b"\x00"
    return block_tx(sig(n), keys, [ix(2, [1], data)], err=err)


def program_tx(n: int, program: str) -> dict[str, object]:
    keys = [PAYER, key(0x71), program, key(0x72), sp.BPF_LOADER_UPGRADEABLE]
    data = struct.pack("<IQ", 2, 1_000_000)
    return block_tx(sig(n), keys, [ix(4, [0, 1, 2, 3], data)], version=0)


def raydium_tx(n: int, pool: str, mint_a: str, mint_b: str) -> dict[str, object]:
    keys = [PAYER] + [key(0x80 + i) for i in range(18)] + [sp.RAYDIUM_AMM_V4]
    keys[5], keys[9], keys[10] = pool, mint_a, mint_b  # accounts 4, 8, 9 are keys[5], [9], [10]
    accounts = list(range(1, 19))
    return block_tx(sig(n), keys, [ix(19, accounts, bytes([1, 255]) + b"\x00" * 24)], version=1)


def orca_tx(n: int, pool: str, mint_a: str, mint_b: str) -> dict[str, object]:
    keys = [PAYER] + [key(0x90 + i) for i in range(11)] + [sp.ORCA_WHIRLPOOL]
    keys[2], keys[3], keys[5] = mint_a, mint_b, pool  # accounts 1, 2, 4 are keys[2], [3], [5]
    data = sp.ORCA_INITIALIZE_POOL + bytes([254]) + struct.pack("<H", 64) + b"\x00" * 16
    return block_tx(sig(n), keys, [ix(12, list(range(1, 12)), data)])


def sol_transfer_tx(n: int, sol: int) -> dict[str, object]:
    return block_tx(sig(n), [PAYER, DEST, SYSTEM], [ix(2, [0, 1], _transfer(sol * 10**9))])


MINT, MINT2, PROGRAM, POOL_R, POOL_O = key(0xA1), key(0xA2), key(0xA3), key(0xA4), key(0xA5)


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    r = Rig(tmp_path, mode="disabled", settings={"solana_collector": {"blocks_per_run": 5}})
    yield r
    r.dispose()


def populate(rig: Rig) -> None:
    head = rig.sol.head_slot
    txs = {
        head - 4: [
            mint_tx(1, MINT),
            mint_tx(2, key(0xB1), err={"InstructionError": [0, "Custom"]}),
        ],
        head - 3: [mint_tx(3, MINT2, tag=20), program_tx(4, PROGRAM), sol_transfer_tx(5, 1)],
        head - 2: [raydium_tx(6, POOL_R, MINT, MINT2), sol_transfer_tx(7, 600)],
        head - 1: [orca_tx(8, POOL_O, MINT2, key(0xB2))],
    }
    previous = hash_of(head - 5)
    for slot in range(head - 4, head):
        rig.sol.blocks[slot] = make_block(slot, hash_of(slot), previous, txs[slot])
        previous = hash_of(slot)


def count(rig: Rig, sql: str, **params: object) -> int:
    with rig.engine.connect() as conn:
        return int(conn.execute(text(sql), params).scalar_one())


def collect(rig: Rig) -> dict[str, Any]:
    with TestClient(rig.app) as c:
        r = c.post(f"{API}/discovery/collect/solana")
    assert r.status_code == 201
    return r.json()


def test_discoveries_are_found_failed_transactions_ignored_and_gaps_counted(rig: Rig) -> None:
    populate(rig)
    m = collect(rig)
    assert m["status"] == "succeeded"
    assert (m["slots_scanned"], m["slots_skipped"]) == (4, 1)  # the head slot is "skipped"
    assert (m["mints_found"], m["programs_found"], m["pools_found"]) == (2, 1, 2)
    assert m["failed_transactions"] == 1
    assert m["large_transfers_stored"] == 1  # 600 SOL stored, 1 SOL not
    assert m["rpc_calls"] <= 150
    assert m["continuity_mismatches"] == 0
    with rig.engine.connect() as conn:
        rows = conn.execute(
            text("SELECT kind, ref, detail_json FROM solana_discoveries ORDER BY slot, id")
        ).all()
    assert [(r[0], r[1]) for r in rows] == [
        ("mint", MINT),
        ("mint", MINT2),
        ("program", PROGRAM),
        ("pool", POOL_R),
        ("pool", POOL_O),
    ]
    assert json.loads(rows[3][2]) == {"dex": "Raydium AMM v4", "mint_a": MINT, "mint_b": MINT2}
    assert json.loads(rows[4][2])["dex"] == "Orca Whirlpools"
    assert count(rig, "SELECT COUNT(*) FROM transfers WHERE standard = 'native'") == 1
    # transaction versions 0 and 1 were both accepted
    assert count(rig, "SELECT COUNT(*) FROM transactions WHERE chain = 'solana'") == 6


def test_resume_does_not_rescan_and_a_far_behind_cursor_records_a_gap(rig: Rig) -> None:
    populate(rig)
    first = collect(rig)
    assert first["last_slot"] == rig.sol.head_slot - 1
    again = collect(rig)
    assert again["slots_scanned"] == 0  # only the skipped head slot remains
    assert count(rig, "SELECT COUNT(*) FROM solana_discoveries") == 5
    rig.repo.set_meta(CURSOR_KEY, str(rig.sol.head_slot - 50_000))
    jumped = collect(rig)
    assert jumped["gap_recorded"]["reason"] == "lag exceeded max_lag_slots"
    gaps = json.loads(rig.repo.get_meta(GAPS_KEY) or "[]")
    assert gaps[-1]["from_slot"] == rig.sol.head_slot - 50_000 + 1


def test_a_broken_blockhash_chain_is_reported(rig: Rig) -> None:
    populate(rig)
    head = rig.sol.head_slot
    rig.sol.blocks[head - 2]["previousBlockhash"] = key(0xEE)  # does not match slot head-3
    m = collect(rig)
    assert m["continuity_mismatches"] == 1


def test_a_block_not_available_is_retried_then_given_up_and_recorded(tmp_path: Path) -> None:
    """-32004 is not "skipped": the cursor must not move past it until it is given up."""
    rig = Rig(
        tmp_path,
        mode="disabled",
        settings={"solana_collector": {"blocks_per_run": 3, "max_slot_attempts": 2}},
    )
    try:
        head = rig.sol.head_slot
        rig.sol.unavailable_slots.add(head - 1)
        for slot in (head - 2, head):
            rig.sol.blocks[slot] = make_block(slot, hash_of(slot), hash_of(slot - 1), [])
        first = collect(rig)
        assert first["status"] == "partial"
        assert rig.repo.get_meta(CURSOR_KEY) == str(head - 2)  # not advanced over head - 1
        second = collect(rig)
        assert second["slots_given_up"] == 1
        assert json.loads(rig.repo.get_meta(GAPS_KEY) or "[]")[-1]["from_slot"] == head - 1
    finally:
        rig.dispose()


def test_an_outage_never_makes_the_collector_give_a_slot_up(tmp_path: Path) -> None:
    rig = Rig(
        tmp_path,
        mode="disabled",
        settings={"solana_collector": {"blocks_per_run": 3, "max_slot_attempts": 1}},
    )
    try:
        head = rig.sol.head_slot
        rig.sol.fail_slots.add(head - 1)  # -32603: transient, the same on every attempt
        rig.sol.blocks[head - 2] = make_block(head - 2, hash_of(head - 2), hash_of(head - 3), [])
        for _ in range(3):
            m = collect(rig)
            assert m["slots_given_up"] == 0
        assert json.loads(rig.repo.get_meta(GAPS_KEY) or "[]") == []
        assert rig.repo.get_meta(CURSOR_KEY) == str(head - 2)
    finally:
        rig.dispose()


def test_continuity_is_checked_across_runs_and_a_lag_jump_records_one_gap(rig: Rig) -> None:
    head = rig.sol.head_slot
    rig.sol.head_slot = head - 1  # run 1 reads head-5 .. head-1; only head-2 has a block
    rig.sol.blocks[head - 2] = make_block(head - 2, hash_of(head - 2), hash_of(head - 3), [])
    first = collect(rig)
    assert first["continuity_mismatches"] == 0
    # run 2: the next block names head-2 as its parent but carries a different previous blockhash
    rig.sol.head_slot = head
    wrong = make_block(head, hash_of(head), key(0xEE), [])
    wrong["parentSlot"] = head - 2
    rig.sol.blocks[head] = wrong
    second = collect(rig)
    assert second["continuity_mismatches"] == 1  # the previous run's blockhash was remembered
    rig.repo.set_meta(CURSOR_KEY, str(head - 100_000))
    for _ in range(2):
        collect(rig)
    jumps = [g for g in json.loads(rig.repo.get_meta(GAPS_KEY) or "[]") if "lag" in g["reason"]]
    assert len(jumps) == 1  # the second run did not record the same jump again


def test_discoveries_are_kept_when_the_transaction_cap_is_hit(tmp_path: Path) -> None:
    rig = Rig(
        tmp_path,
        mode="disabled",
        settings={"solana_collector": {"blocks_per_run": 5, "max_stored_transactions_per_run": 1}},
    )
    try:
        populate(rig)  # five discoveries and a large transfer in four blocks
        m = collect(rig)
        assert m["stored_transactions_over_cap"] >= 1
        assert count(rig, "SELECT COUNT(*) FROM solana_discoveries") == 5  # tiny rows: never capped
        gaps = json.loads(rig.repo.get_meta(GAPS_KEY) or "[]")
        assert any("large SOL transfer" in g["reason"] for g in gaps)
    finally:
        rig.dispose()


def test_a_transaction_without_meta_is_not_assumed_to_have_succeeded(rig: Rig) -> None:
    head = rig.sol.head_slot
    tx = mint_tx(1, MINT)
    del tx["meta"]
    rig.sol.blocks[head - 1] = make_block(head - 1, hash_of(head - 1), hash_of(head - 2), [tx])
    m = collect(rig)
    assert m["unknown_status_transactions"] == 1
    assert count(rig, "SELECT COUNT(*) FROM solana_discoveries") == 0
    assert sp.decode_discoveries(tx) == []


def test_huge_instruction_data_and_unrelated_programs_are_not_decoded() -> None:
    long_data = "1" * 5000  # would be a slow base58 decode if it were attempted
    keys = [PAYER, sp.TOKEN_PROGRAM, key(0xCC)]
    tx = block_tx(sig(9), keys, [{"programIdIndex": 1, "accounts": [0], "data": long_data}])
    assert sp.instructions(tx) == []  # over the length cap
    other = block_tx(
        sig(10), keys, [{"programIdIndex": 2, "accounts": [0], "data": "3Bxs4h24hBtQy9rw"}]
    )
    assert sp.instructions(other) == []  # unrelated program: its data is never decoded


def test_detector_raises_evidence_backed_signals_with_honest_limits(rig: Rig) -> None:
    populate(rig)
    collect(rig)
    with rig.engine.connect() as conn:
        latest = conn.execute(text("SELECT MAX(block_time) FROM solana_discoveries")).scalar_one()
    when = datetime.fromisoformat(str(latest)).replace(tzinfo=UTC) + timedelta(hours=1)
    service = DiscoveryService(rig.repo, rig.settings, clock=lambda: when)
    result = service.run("solana")["detectors"]
    assert result["solana_new_entities:solana"]["created"] == 5
    items = service.list_signals(chain="solana", limit=20, offset=0)["items"]
    kinds = {s["subject"]["kind"] for s in items if s["detector"]["id"] == "solana_new_entities"}
    assert kinds == {"token", "contract", "pool"}
    for s in items:
        assert s["confidence"] <= 0.45
        assert "does not show" in s["explanation"]["caveat"]


# ------------------------------------------------------------------------------ decoder


def test_decoder_recognises_only_verified_layouts() -> None:
    assert sp.ORCA_INITIALIZE_POOL.hex() == "5fb40aac54aee828"
    wrong_tag = mint_tx(1, MINT, tag=1)  # InitializeAccount, not a mint
    assert sp.decode_discoveries(wrong_tag) == []
    unknown_program = block_tx(sig(2), [PAYER, key(0xCC), key(0xCD)], [ix(2, [0, 1], b"\x01" * 16)])
    assert sp.decode_discoveries(unknown_program) == []
    truncated = block_tx(sig(3), [PAYER, sp.RAYDIUM_AMM_V4], [ix(1, [0], bytes([1]))])
    assert sp.decode_discoveries(truncated) == []  # too few accounts to name a pool
    malformed: dict[str, Any] = {"transaction": {"message": {"accountKeys": "nope"}}}
    assert sp.decode_discoveries(malformed) == []
    assert [k.name for k in sp.KNOWN_PROGRAMS] == [
        "Upgradeable BPF loader",
        "Raydium AMM v4",
        "Orca Whirlpools",
    ]
    assert all(k.source_url.startswith("https://") and k.verified_at for k in sp.KNOWN_PROGRAMS)


def test_admin_only_and_status_names_its_sources(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.user("ana", "analyst")
    try:
        with TestClient(rig.app) as c:
            r = c.post(f"{API}/auth/login", json={"username": "ana", "password": PASSWORD})
            headers = {"X-CSRF-Token": r.json()["csrf_token"]}
            assert c.post(f"{API}/discovery/collect/solana", headers=headers).status_code == 403
            body = c.get(f"{API}/discovery/collector/solana").json()
        assert {p["name"] for p in body["recognised_programs"]} >= {"Raydium AMM v4"}
        assert body["limits"]["max_rpc_calls_per_run"] <= 150
    finally:
        rig.dispose()
