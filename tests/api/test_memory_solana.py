"""Intelligence memory folds Solana programs and pools in like Ethereum deployments and pools."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import insert

from bip.config.settings import Settings
from bip.db import schema
from bip.db.repository import Repository
from bip.intel.memory import MemoryService

T0 = datetime(2026, 11, 1, tzinfo=UTC)
PAYER = "Payer1111111111111111111111111111111111111"
MINT_A = "MintA111111111111111111111111111111111111"
MINT_B = "MintB111111111111111111111111111111111111"


def discover(repo: Repository, kind: str, ref: str, n: int, **detail: object) -> str:
    sig = f"Sig{n:080d}"
    with repo.transaction() as conn:
        conn.execute(
            insert(schema.solana_discoveries).values(
                kind=kind,
                ref=ref,
                slot=300_000_000 + n,
                blockhash=f"Hash{n:040d}",
                signature=sig,
                block_time=T0,
                detail_json=json.dumps(detail),
                finality="finalized",
                stored_at=T0,
            )
        )
    return sig


def fact(memory: MemoryService, ref: str, key: str) -> dict[str, object]:
    profile = memory.entity("solana", ref)
    assert profile is not None
    return next(f for f in profile["current_facts"] if f["key"] == key)


def test_a_program_records_its_deploy_payer(repo: Repository, settings: Settings) -> None:
    memory = MemoryService(repo, settings.memory)
    sig = discover(repo, "program", "Prog1", 1, payer=PAYER, program_data="Data1")
    memory.refresh("solana")
    deployed = fact(memory, "Prog1", "deployed_by")
    assert deployed["value"] == {
        "deployer": PAYER,
        "basis": "payer of the upgradeable-loader deploy instruction",
    }
    assert (deployed["evidence_class"], deployed["source"], deployed["source_tx"]) == (
        "observed",
        "solana_discoveries",
        sig,
    )
    payer = memory.entity("solana", PAYER)
    assert payer is not None
    assert payer["kind"] == "wallet"
    discover(repo, "program", "Prog2", 3, payer=PAYER, program_data="Data2")
    discover(repo, "program", "Prog3", 4, payer="SomeoneElse11111111111111111111111111111111")
    memory.refresh("solana")
    role = fact(memory, PAYER, "role:deployer")
    assert role["value"] == {
        "programs_deployed": 2,
        "basis": "payer of the upgradeable-loader deploy instruction",
    }
    assert role["source"] == "solana_discoveries"


def test_a_pool_links_both_mints(repo: Repository, settings: Settings) -> None:
    memory = MemoryService(repo, settings.memory)
    discover(repo, "pool", "Pool1", 2, dex="Orca Whirlpools", mint_a=MINT_A, mint_b=MINT_B)
    memory.refresh("solana")
    pool = fact(memory, "Pool1", "pool")
    assert pool["value"] == {"protocol": "Orca Whirlpools", "token0": MINT_A, "token1": MINT_B}
    assert pool["evidence_class"] == "observed"
    for mint in (MINT_A, MINT_B):
        ent = memory.entity("solana", mint)
        assert ent is not None
        assert ent["kind"] == "token"
    first = memory.refresh("solana")
    assert (first["facts_new"], first["facts_changed"]) == (0, 0)  # idempotent
