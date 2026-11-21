"""Pure parsers for Ethereum JSON-RPC results -> BIP contracts.

All inputs are untrusted. Quantities must be ``0x``-hex (bounded length), hashes 32 bytes,
addresses 20 bytes. Anything else raises :class:`bip.chains.common.ParseError`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from pydantic import ValidationError

from bip.chains.common import SQL_INT_MAX, ParseError, SourceInfo, arr, obj, unix_time
from bip.contracts.blockchain import AddressRef, BlockPosition
from bip.contracts.blocks import EvmBlock
from bip.contracts.events import EvmLog
from bip.contracts.identifiers import EvmTxRef
from bip.contracts.provenance import (
    Finality,
    IngestionMethod,
    Provenance,
    RawDataRef,
    SourceKind,
    VerificationStatus,
)
from bip.contracts.transactions import EvmTransaction, TxStatus
from bip.contracts.transfers import TokenStandard, TokenTransfer, TransferClassification

CHAIN = "ethereum"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
"""keccak256("Transfer(address,address,uint256)"), shared by ERC-20 and ERC-721."""

_QTY = re.compile(r"^0x[0-9a-fA-F]{1,64}$")
_HASH = re.compile(r"^0x[0-9a-fA-F]{64}$")
_ADDR = re.compile(r"^0x[0-9a-fA-F]{40}$")


def quantity(value: object, field: str, *, maximum: int | None = None) -> int:
    if not isinstance(value, str) or not _QTY.fullmatch(value):
        raise ParseError(field, "expected a 0x-hex quantity")
    n = int(value, 16)
    if maximum is not None and n > maximum:
        raise ParseError(field, "quantity out of range")
    return n


def position(value: object, field: str) -> int:
    """Block numbers, indexes and nonces: bounded to what SQLite INTEGER can store."""
    return quantity(value, field, maximum=SQL_INT_MAX)


def opt_quantity(value: object, field: str) -> int | None:
    return None if value is None else quantity(value, field)


def hash32(value: object, field: str) -> str:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ParseError(field, "expected a 32-byte hex hash")
    return value.lower()


def address(value: object, field: str) -> str:
    if not isinstance(value, str) or not _ADDR.fullmatch(value):
        raise ParseError(field, "expected a 20-byte hex address")
    return value.lower()


def provenance(
    src: SourceInfo,
    *,
    block_number: int | None,
    block_hash: str | None,
    tx_hash: str | None,
    event_time: datetime | None,
    finality: Finality,
) -> Provenance:
    return Provenance(
        chain=CHAIN,
        source_kind=SourceKind.RPC,
        source_provider=src.provider_id,
        source_method=src.method,
        collected_at=src.collected_at,
        event_time=event_time,
        ingestion_method=IngestionMethod.ON_DEMAND,
        finality=finality,
        verification_status=VerificationStatus.UNVERIFIED,
        schema_version=1,
        raw_ref=RawDataRef(uri=f"sqlite:raw_payloads/{src.raw_sha256}", content_hash=src.raw_sha256)
        if src.raw_sha256
        else None,
        position=BlockPosition(block_number=block_number, block_hash=block_hash)
        if block_number is not None
        else None,
        tx=EvmTxRef(tx_hash=tx_hash) if tx_hash else None,
    )


@dataclass(frozen=True, slots=True)
class BlockHeader:
    number: int
    hash: str
    parent_hash: str
    timestamp: datetime
    tx_count: int | None


def parse_block_header(raw: object) -> BlockHeader:
    b = obj(raw, "block")
    txs = b.get("transactions")
    return BlockHeader(
        number=position(b.get("number"), "block.number"),
        hash=hash32(b.get("hash"), "block.hash"),
        parent_hash=hash32(b.get("parentHash"), "block.parentHash"),
        timestamp=unix_time(position(b.get("timestamp"), "block.timestamp"), "block.timestamp"),
        tx_count=len(arr(txs, "block.transactions")) if txs is not None else None,
    )


def to_block_contract(h: BlockHeader, src: SourceInfo, finality: Finality) -> EvmBlock:
    return EvmBlock(
        chain=CHAIN,
        number=h.number,
        hash=h.hash,
        parent_hash=h.parent_hash,
        timestamp=h.timestamp,
        tx_count=h.tx_count,
        provenance=provenance(
            src,
            block_number=h.number,
            block_hash=h.hash,
            tx_hash=None,
            event_time=h.timestamp,
            finality=finality,
        ),
    )


@dataclass(frozen=True, slots=True)
class ParsedTransaction:
    tx: EvmTransaction
    value_wei: int
    gas_used: int | None
    gas_price_wei: int | None


def parse_transaction(
    raw_tx: object,
    raw_receipt: object | None,
    *,
    block_time: datetime | None,
    src: SourceInfo,
    finality: Finality,
) -> ParsedTransaction:
    t = obj(raw_tx, "tx")
    tx_hash = hash32(t.get("hash"), "tx.hash")
    block_number = (
        position(t.get("blockNumber"), "tx.blockNumber")
        if t.get("blockNumber") is not None
        else None
    )
    block_hash = t.get("blockHash")
    if block_number is None or block_hash is None:
        raise ParseError("tx.blockNumber", "transaction is pending (no block); not stored")
    bhash = hash32(block_hash, "tx.blockHash")
    to_raw = t.get("to")
    value = quantity(t.get("value"), "tx.value")
    status: TxStatus | None = None
    gas_used: int | None = None
    created: str | None = None
    if raw_receipt is not None:
        r = obj(raw_receipt, "receipt")
        if hash32(r.get("transactionHash"), "receipt.transactionHash") != tx_hash:
            raise ParseError("receipt.transactionHash", "receipt does not belong to this tx")
        if hash32(r.get("blockHash"), "receipt.blockHash") != bhash:
            raise ParseError("receipt.blockHash", "receipt block differs from tx block (reorg?)")
        st = r.get("status")
        if st is not None:
            code = quantity(st, "receipt.status")
            status = TxStatus.SUCCESS if code == 1 else TxStatus.FAILED
        gas_used = opt_quantity(r.get("gasUsed"), "receipt.gasUsed")
        if (
            to_raw is None
            and r.get("contractAddress") is not None
            and status is not TxStatus.FAILED  # clients fill contractAddress on reverted creations
        ):
            created = address(r.get("contractAddress"), "receipt.contractAddress")
    try:
        tx = EvmTransaction(
            chain=CHAIN,
            tx_hash=tx_hash,
            block_number=block_number,
            block_hash=bhash,
            tx_index=position(t.get("transactionIndex"), "tx.transactionIndex"),
            from_address=address(t.get("from"), "tx.from"),
            to_address=address(to_raw, "tx.to") if to_raw is not None else None,
            value_wei=value,
            nonce=position(t.get("nonce"), "tx.nonce"),
            status=status,
            gas_used=gas_used,
            created_contract=created,
            provenance=provenance(
                src,
                block_number=block_number,
                block_hash=bhash,
                tx_hash=tx_hash,
                event_time=block_time,
                finality=finality,
            ),
        )
    except ValidationError as exc:
        raise ParseError("tx", exc.errors(include_input=False)[0]["msg"]) from None
    gas_price = opt_quantity(t.get("effectiveGasPrice") or t.get("gasPrice"), "tx.gasPrice")
    return ParsedTransaction(tx, value, gas_used, gas_price)


def parse_log(
    raw: object, src: SourceInfo, *, block_time: datetime | None, finality: Finality
) -> EvmLog:
    lg = obj(raw, "log")
    block_number = position(lg.get("blockNumber"), "log.blockNumber")
    bhash = hash32(lg.get("blockHash"), "log.blockHash")
    tx_hash = hash32(lg.get("transactionHash"), "log.transactionHash")
    topics = tuple(
        hash32(x, "log.topics[]") for x in arr(lg.get("topics"), "log.topics", max_len=4)
    )
    data = lg.get("data")
    if not isinstance(data, str) or not re.fullmatch(r"0x([0-9a-fA-F]{2}){0,4096}", data):
        raise ParseError("log.data", "expected bounded even-length hex data")
    removed = lg.get("removed", False)
    if not isinstance(removed, bool):
        raise ParseError("log.removed", "expected a boolean")
    return EvmLog(
        chain=CHAIN,
        block_number=block_number,
        block_hash=bhash,
        tx_hash=tx_hash,
        tx_index=position(lg.get("transactionIndex"), "log.transactionIndex"),
        log_index=position(lg.get("logIndex"), "log.logIndex"),
        address=address(lg.get("address"), "log.address"),
        topics=topics,
        data=data.lower(),
        removed=removed,
        provenance=provenance(
            src,
            block_number=block_number,
            block_hash=bhash,
            tx_hash=tx_hash,
            event_time=block_time,
            finality=finality,
        ),
    )


def topic_address(topic: str) -> str | None:
    """Decode an indexed address topic; ``None`` if the upper 12 bytes are not zero."""
    body = topic[2:]
    if body[:24] != "0" * 24:
        return None
    return "0x" + body[24:]


def pad_address_topic(addr: str) -> str:
    return "0x" + "0" * 24 + addr.lower()[2:]


def decode_transfer_log(log: EvmLog) -> tuple[TokenTransfer | None, str | None]:
    """Decode a standard Transfer log. Returns (transfer, None) or (None, reason-if-unparseable).

    ERC-20: 3 topics + 32-byte data (amount). ERC-721: 4 topics (token id) + empty data.
    Non-standard shapes are reported, not guessed. Removed (reorged) logs are never decoded.
    """
    if not log.topics or log.topics[0] != TRANSFER_TOPIC:
        return None, None
    if log.removed:
        return None, "log marked removed by the provider (reorg)"
    sender = topic_address(log.topics[1]) if len(log.topics) > 1 else None
    recipient = topic_address(log.topics[2]) if len(log.topics) > 2 else None
    if sender is None or recipient is None:
        return None, "Transfer topic present but indexed addresses are malformed or missing"
    data_bytes = (len(log.data) - 2) // 2
    if len(log.topics) == 3 and data_bytes == 32:
        standard, amount, token_id = TokenStandard.ERC20, int(log.data, 16), None
    elif len(log.topics) == 4 and data_bytes == 0:
        standard, amount, token_id = TokenStandard.ERC721, 1, int(log.topics[3], 16)
    else:
        return (
            None,
            f"non-standard Transfer shape ({len(log.topics)} topics, {data_bytes} data bytes)",
        )
    zero = "0x" + "0" * 40
    classification = TransferClassification.UNCLASSIFIED
    if sender == zero:
        classification = TransferClassification.MINT
    elif recipient == zero:
        classification = TransferClassification.BURN
    transfer = TokenTransfer(
        chain=CHAIN,
        tx=EvmTxRef(tx_hash=log.tx_hash),
        position=BlockPosition(block_number=log.block_number, block_hash=log.block_hash),
        log_index=log.log_index,
        standard=standard,
        token=AddressRef(chain=CHAIN, address=log.address),
        from_address=AddressRef(chain=CHAIN, address=sender),
        to_address=AddressRef(chain=CHAIN, address=recipient),
        amount_raw=amount,
        token_id=token_id,
        classification=classification,
        provenance=log.provenance,
    )
    return transfer, None


def native_transfer(parsed: ParsedTransaction) -> TokenTransfer | None:
    """Top-level ETH value transfer. Internal (contract-initiated) transfers are not visible."""
    tx = parsed.tx
    if parsed.value_wei == 0 or tx.to_address is None or tx.status is TxStatus.FAILED:
        return None  # a reverted transaction moved no value
    return TokenTransfer(
        chain=CHAIN,
        tx=EvmTxRef(tx_hash=tx.tx_hash),
        position=BlockPosition(block_number=tx.block_number, block_hash=tx.block_hash),
        standard=TokenStandard.NATIVE,
        token=None,
        from_address=AddressRef(chain=CHAIN, address=tx.from_address),
        to_address=AddressRef(chain=CHAIN, address=tx.to_address),
        amount_raw=parsed.value_wei,
        decimals=18,
        provenance=tx.provenance,
    )
