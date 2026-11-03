from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from bip.contracts.blockchain import BlockPosition, SlotPosition
from bip.contracts.identifiers import EvmTxRef, SolanaTxRef
from bip.contracts.provenance import (
    Finality,
    IngestionMethod,
    Provenance,
    RawDataRef,
    SourceKind,
    VerificationStatus,
)
from tests.conftest import COLLECTED, EVM_TX, SOL_SIGNATURE, evm_provenance, sol_provenance


def test_defaults_are_conservative() -> None:
    p = evm_provenance()
    assert p.finality is Finality.OBSERVED
    assert p.verification_status is VerificationStatus.UNVERIFIED
    assert p.event_time is None
    assert p.raw_ref is None


def test_round_trips_through_json() -> None:
    p = sol_provenance(
        raw_ref=RawDataRef(uri="bronze/chain=solana/part-1.parquet", row=7, content_hash="a" * 64)
    )
    assert Provenance.model_validate_json(p.model_dump_json()) == p


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"position": SlotPosition(slot=5)}, "block positions"),
        ({"tx": SolanaTxRef(signature=SOL_SIGNATURE)}, "evm_tx_hash"),
        ({"chain": None}, "require a chain"),
        ({"chain": "dogechain"}, "unknown chain"),
        ({"collected_at": datetime(2026, 11, 3, 12, 0)}, "timezone"),
        ({"event_time": COLLECTED + timedelta(hours=1)}, "later than collected_at"),
        ({"source_kind": SourceKind.DERIVED}, "go together"),
        ({"schema_version": 0}, "greater than or equal to 1"),
        ({"schema_version": True}, "integer"),
        ({"source_provider": "Has Spaces"}, "pattern"),
        ({"unexpected": 1}, "Extra inputs"),
    ],
)
def test_invalid_provenance(overrides: dict[str, object], match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        evm_provenance(**overrides)


def test_solana_rejects_block_position() -> None:
    with pytest.raises(ValidationError, match="slot positions"):
        sol_provenance(position=BlockPosition(block_number=1))


def test_chain_agnostic_web_source_allowed() -> None:
    p = Provenance(
        chain=None,
        source_kind=SourceKind.WEB,
        source_provider="github",
        collected_at=COLLECTED,
        ingestion_method=IngestionMethod.ON_DEMAND,
        schema_version=1,
    )
    assert p.chain is None


def test_derived_records_declare_both_fields() -> None:
    p = evm_provenance(
        source_kind=SourceKind.DERIVED,
        ingestion_method=IngestionMethod.DERIVED,
        source_provider="gold.wallet-daily-v1",
    )
    assert p.source_kind is SourceKind.DERIVED


@pytest.mark.parametrize("bad", ["ABC", "a" * 63, "g" * 64])
def test_raw_ref_hash_format(bad: str) -> None:
    with pytest.raises(ValidationError, match="SHA-256"):
        RawDataRef(uri="x", content_hash=bad)


def test_provenance_is_immutable() -> None:
    p = evm_provenance()
    with pytest.raises(ValidationError, match="frozen"):
        p.verification_status = VerificationStatus.CROSS_VERIFIED  # type: ignore[misc]


def test_tx_hash_normalized_to_lowercase() -> None:
    p = evm_provenance(tx=EvmTxRef(tx_hash=EVM_TX.upper().replace("0X", "0x")))
    assert p.tx is not None
    assert p.tx.kind == "evm_tx_hash"
    assert p.tx.tx_hash == EVM_TX
