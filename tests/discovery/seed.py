"""Deterministic stored-data builders for the discovery tests.

Rows are inserted straight into the tables ingestion writes, with valid provenance, so the
detectors run over exactly the shape of data a real ingestion run stores.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import Engine, insert

from bip.db import schema
from tests.conftest import evm_provenance, sol_provenance

NOW = datetime(2026, 11, 8, 12, 0, 0, tzinfo=UTC)
ETH = "ethereum"
SOL = "solana"
E18 = 10**18


def eth_addr(n: int) -> str:
    return "0x" + f"{n:040x}"


def eth_tx(n: int) -> str:
    return "0x" + f"{n:064x}"


class Seed:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._block = 1_000

    def tx(
        self,
        tx_id: str,
        sender: str,
        when: datetime,
        *,
        chain: str = ETH,
        to: str | None = None,
        created: str | None = None,
        finality: str = "finalized",
        parse_status: str = "complete",
        block_hash: str | None = None,
        programs: list[str] | None = None,
    ) -> None:
        self._block += 1
        prov = (evm_provenance() if chain == ETH else sol_provenance()).model_dump_json()
        position: dict[str, object] = (
            {"block_number": self._block} if chain == ETH else {"slot": self._block}
        )
        with self.engine.begin() as conn:
            conn.execute(
                insert(schema.transactions).values(
                    program_ids_json=json.dumps(programs) if programs is not None else None,
                    chain=chain,
                    tx_id=tx_id,
                    block_time=when,
                    from_address=sender,
                    to_address=to,
                    status="success",
                    provenance_json=prov,
                    stored_at=when,
                    network="mainnet" if chain == ETH else "mainnet-beta",
                    block_hash=block_hash or (f"0x{self._block:064x}" if chain == ETH else None),
                    finality=finality,
                    parse_status=parse_status,
                    created_contract=created,
                    **position,
                )
            )

    def transfer(
        self,
        tx_id: str,
        sender: str | None,
        recipient: str | None,
        amount: int,
        when: datetime,
        *,
        token: str | None = None,
        chain: str = ETH,
        event_key: str = "0",
        decimals: int | None = 18,
    ) -> None:
        self._block += 1
        prov = (evm_provenance() if chain == ETH else sol_provenance()).model_dump_json()
        position: dict[str, object] = (
            {"block_number": self._block} if chain == ETH else {"slot": self._block}
        )
        with self.engine.begin() as conn:
            conn.execute(
                insert(schema.transfers).values(
                    chain=chain,
                    tx_id=tx_id,
                    event_key=event_key,
                    standard="erc20" if token else "native",
                    token=token,
                    from_address=sender,
                    to_address=recipient,
                    amount_raw=str(amount),
                    decimals=decimals,
                    classification="transfer",
                    provenance_json=prov,
                    stored_at=when,
                    observation_kind="erc20_transfer_log" if token else "native_tx_value",
                    parse_status="complete",
                    block_time=when,
                    **position,
                )
            )
