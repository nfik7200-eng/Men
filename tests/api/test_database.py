from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, insert, inspect, select, text

from bip.config import Settings, load_settings
from bip.contracts.blockchain import AddressRef, BlockPosition, SlotPosition
from bip.contracts.identifiers import EvmTxRef, SolanaTxRef
from bip.contracts.transactions import EvmTransaction, SolanaTransaction
from bip.contracts.transfers import TokenStandard, TokenTransfer
from bip.db import schema
from bip.db.engine import create_db_engine, database_path, migrate
from bip.db.errors import DatabaseError, DuplicateRecordError, InvalidRecordError
from bip.db.repository import Repository
from bip.db.types import UTCDateTime, utcnow
from tests.conftest import (
    COLLECTED,
    EVM_ADDR_A,
    EVM_ADDR_B,
    EVM_BLOCK_HASH,
    EVM_TOKEN,
    EVM_TX,
    SOL_KEY_B,
    SOL_SIGNATURE,
    evm_provenance,
    sol_provenance,
)


def evm_tx(**kw: object) -> EvmTransaction:
    data: dict[str, object] = {
        "chain": "ethereum",
        "tx_hash": EVM_TX,
        "block_number": 100,
        "block_hash": EVM_BLOCK_HASH,
        "tx_index": 0,
        "from_address": EVM_ADDR_A,
        "to_address": EVM_ADDR_B,
        "value_wei": 1,
        "nonce": 0,
        "provenance": evm_provenance(event_time=COLLECTED),
    }
    data.update(kw)
    return EvmTransaction.model_validate(data)


def test_database_lives_inside_data_root(settings: Settings) -> None:
    path = database_path(settings)
    assert path.is_relative_to(settings.resolved_data_root)
    assert path.parent.name == "operational"


def test_initialization_creates_empty_schema(engine: Engine, repo: Repository) -> None:
    tables = set(inspect(engine).get_table_names())
    assert set(schema.metadata.tables) <= tables
    assert all(v == 0 for v in repo.counts().values())
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_repeated_startup_is_safe(settings: Settings) -> None:
    first = create_db_engine(settings)
    Repository(first).create_watchlist("Kept", None)
    first.dispose()
    second = create_db_engine(settings)
    assert migrate(second) == schema.SCHEMA_VERSION
    with second.connect() as conn:
        assert conn.execute(select(schema.schema_migrations.c.version)).scalars().all() == [1]
    assert [w.name for w in Repository(second).list_watchlists()] == ["Kept"]
    second.dispose()


def test_newer_schema_is_refused(settings: Settings, engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(schema.schema_migrations).values(
                version=99, applied_at=utcnow(), description="future"
            )
        )
    with pytest.raises(DatabaseError, match="newer than this code"):
        migrate(engine)


def test_unavailable_database_raises_database_error(tmp_path: Path) -> None:
    broken = create_engine(f"sqlite+pysqlite:///{tmp_path}/missing-dir/x.sqlite3")
    with pytest.raises(DatabaseError, match="failed"):
        Repository(broken).list_watchlists()


def test_failed_write_is_not_silent(repo: Repository) -> None:
    repo.create_watchlist("A", None)
    with pytest.raises(DuplicateRecordError):
        repo.create_watchlist("A", "again")
    assert len(repo.list_watchlists()) == 1


def test_naive_datetimes_rejected() -> None:
    from datetime import datetime

    with pytest.raises(ValueError, match="naive"):
        UTCDateTime().process_bind_param(datetime(2026, 1, 1), None)  # type: ignore[arg-type]


def test_transactions_dedupe_and_update_wallets(repo: Repository) -> None:
    assert repo.store_transaction(evm_tx()) is True
    assert repo.store_transaction(evm_tx()) is False
    assert repo.counts()["transactions_stored"] == 1
    profile = repo.get_wallet("ethereum", EVM_ADDR_A.upper().replace("0X", "0x"))
    assert profile.transaction_count == 1
    assert profile.first_observed_at == COLLECTED
    assert profile.first_seen_source == "synthetic"
    assert profile.recent_transactions[0].block_number == 100
    assert profile.recent_transactions[0].slot is None


def test_solana_transactions_use_slots(repo: Repository) -> None:
    tx = SolanaTransaction(
        chain="solana",
        signature=SOL_SIGNATURE,
        slot=250_000_000,
        fee_payer=SOL_KEY_B,
        block_time=COLLECTED,
        provenance=sol_provenance(),
    )
    assert repo.store_transaction(tx)
    stats = repo.chain_data_stats("solana")
    assert stats == {"transactions": 1, "latest_block": None, "latest_slot": 250_000_000}


def test_transfers_store_raw_amounts_and_dedupe(repo: Repository) -> None:
    transfer = TokenTransfer(
        chain="ethereum",
        tx=EvmTxRef(tx_hash=EVM_TX),
        position=BlockPosition(block_number=100),
        log_index=3,
        standard=TokenStandard.ERC20,
        token=AddressRef(chain="ethereum", address=EVM_TOKEN),
        from_address=AddressRef(chain="ethereum", address=EVM_ADDR_A),
        to_address=AddressRef(chain="ethereum", address=EVM_ADDR_B),
        amount_raw=2**255,
        provenance=evm_provenance(),
    )
    assert repo.store_transfer(transfer)
    assert not repo.store_transfer(transfer)
    profile = repo.get_wallet("ethereum", EVM_ADDR_B)
    assert profile.transfer_count == 1
    assert profile.recent_transfers[0].amount_raw == str(2**255)
    assert profile.recent_transfers[0].decimals is None


def test_solana_transfer_event_key(repo: Repository) -> None:
    t = TokenTransfer(
        chain="solana",
        tx=SolanaTxRef(signature=SOL_SIGNATURE),
        position=SlotPosition(slot=5),
        instruction_path=(2, 1),
        standard=TokenStandard.NATIVE,
        token=None,
        from_address=AddressRef(chain="solana", address=SOL_KEY_B),
        to_address=None,
        amount_raw=10,
        provenance=sol_provenance(),
    )
    assert repo.store_transfer(t)
    assert repo.get_wallet("solana", SOL_KEY_B).recent_transfers[0].event_key == "ix:2.1"


def test_invalid_address_rejected(repo: Repository) -> None:
    with pytest.raises(InvalidRecordError):
        repo.get_wallet("ethereum", "0x1234")
    with pytest.raises(InvalidRecordError, match="unknown chain"):
        repo.get_wallet("dogecoin", EVM_ADDR_A)


def test_data_persists_across_engines(settings: Settings) -> None:
    eng = create_db_engine(settings)
    Repository(eng).create_investigation("Persisted", None, None)
    eng.dispose()
    other = load_settings(data_root=settings.data_root, env="test")
    eng2 = create_db_engine(other)
    assert [i.title for i in Repository(eng2).list_investigations()] == ["Persisted"]
    eng2.dispose()
