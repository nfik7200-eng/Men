"""Pure parsers for Solana JSON-RPC results (``getTransaction`` with ``encoding=json``).

Observation kinds are kept distinct and never conflated:

- ``native_balance_change``: pre/post lamport balances of an account (a fact about balances,
  not a transfer between two parties);
- ``token_balance_change``: pre/post SPL token balances (``meta.pre/postTokenBalances``);
- decoded transfer instructions: System Program ``Transfer`` and SPL Token / Token-2022
  ``Transfer`` / ``TransferChecked``, decoded from instruction data, top-level and inner;
- every other instruction is counted as *unknown*, never interpreted.

Transaction versions ``legacy``, ``0`` and ``1`` are supported. v0 lookup-table keys from
``meta.loadedAddresses`` are appended in the documented order (writable, then readonly); v1
has no lookup tables, and its resource limits live in ``transactionConfig``, which is not used
for transfer decoding.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import ValidationError

from bip.chains.common import ParseError, SourceInfo, arr, integer, obj, text, unix_time
from bip.contracts.blockchain import AddressKind, AddressRef, SlotPosition
from bip.contracts.identifiers import SolanaTxRef, b58decode
from bip.contracts.provenance import (
    Finality,
    IngestionMethod,
    Provenance,
    RawDataRef,
    SourceKind,
    VerificationStatus,
)
from bip.contracts.transactions import SolanaTransaction, TxStatus
from bip.contracts.transfers import TokenStandard, TokenTransfer

CHAIN = "solana"
SYSTEM_PROGRAM = "11111111111111111111111111111111"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"  # noqa: S105 - program id
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PQnBqCXEpPxuEb"  # noqa: S105 - program id
_TOKEN_PROGRAMS = {
    TOKEN_PROGRAM: TokenStandard.SPL_TOKEN,
    TOKEN_2022_PROGRAM: TokenStandard.SPL_TOKEN_2022,
}

Commitment = Literal["confirmed", "finalized"]


def finality_for(commitment: Commitment) -> Finality:
    return Finality.FINALIZED if commitment == "finalized" else Finality.CONFIRMED


def _pubkey(value: object, field_name: str) -> str:
    key = text(value, field_name, max_len=44)
    try:
        if len(b58decode(key)) != 32:
            raise ParseError(field_name, "public key must decode to 32 bytes")
    except ValueError:
        raise ParseError(field_name, "invalid base58 public key") from None
    return key


def _signature(value: object, field_name: str) -> str:
    sig = text(value, field_name, max_len=88)
    try:
        if len(b58decode(sig)) != 64:
            raise ParseError(field_name, "signature must decode to 64 bytes")
    except ValueError:
        raise ParseError(field_name, "invalid base58 signature") from None
    return sig


def _time(value: object, field_name: str) -> datetime | None:
    if value is None:
        return None
    return unix_time(integer(value, field_name), field_name)


@dataclass(frozen=True, slots=True)
class SignatureInfo:
    signature: str
    slot: int
    block_time: datetime | None
    failed: bool
    confirmation_status: str | None


def parse_signatures(raw: object) -> list[SignatureInfo]:
    out: list[SignatureInfo] = []
    for i, item in enumerate(arr(raw, "signatures", max_len=1000)):
        o = obj(item, f"signatures[{i}]")
        status = o.get("confirmationStatus")
        out.append(
            SignatureInfo(
                signature=_signature(o.get("signature"), f"signatures[{i}].signature"),
                slot=integer(o.get("slot"), f"signatures[{i}].slot"),
                block_time=_time(o.get("blockTime"), f"signatures[{i}].blockTime"),
                failed=o.get("err") is not None,
                confirmation_status=status if isinstance(status, str) else None,
            )
        )
    return out


@dataclass(frozen=True, slots=True)
class BalanceChange:
    account: str
    owner: str | None
    mint: str | None
    kind: Literal["native_balance_change", "token_balance_change"]
    pre_raw: int | None
    post_raw: int | None
    decimals: int | None

    @property
    def delta_raw(self) -> int | None:
        if self.pre_raw is None or self.post_raw is None:
            return None
        return self.post_raw - self.pre_raw


@dataclass
class ParsedSolanaTx:
    tx: SolanaTransaction
    account_keys: tuple[str, ...]
    program_ids: tuple[str, ...]
    instruction_count: int
    unknown_instruction_count: int
    transfers: list[tuple[TokenTransfer, str]] = field(
        default_factory=list[tuple[TokenTransfer, str]]
    )
    balance_changes: list[BalanceChange] = field(default_factory=list[BalanceChange])
    issues: list[str] = field(default_factory=list[str])

    @property
    def parse_status(self) -> Literal["complete", "partial"]:
        return "partial" if self.issues else "complete"


def _provenance(
    src: SourceInfo, slot: int, signature: str, event_time: datetime | None, finality: Finality
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
        position=SlotPosition(slot=slot),
        tx=SolanaTxRef(signature=signature),
    )


def _decode_data(value: object) -> bytes | None:
    if not isinstance(value, str) or len(value) > 4096:
        return None
    if value == "":
        return b""
    try:
        return b58decode(value)
    except ValueError:
        return None


def parse_transaction(
    raw: object,
    *,
    wallet: str,
    src: SourceInfo,
    commitment: Commitment,
) -> ParsedSolanaTx:
    """Parse one ``getTransaction`` result. ``wallet`` selects which balance changes to keep."""
    if raw is None:
        raise ParseError("transaction", "provider returned null (not found at this commitment)")
    r = obj(raw, "transaction")
    slot = integer(r.get("slot"), "slot")
    block_time = _time(r.get("blockTime"), "blockTime")
    version_raw = r.get("version", "legacy")
    # Supported: legacy, v0 and v1 (live on mainnet since epoch 1035, 2026-09-15). Anything else
    # is rejected rather than guessed. Booleans are excluded explicitly because True == 1.
    version: Literal["legacy"] | int
    if version_raw == "legacy":
        version = "legacy"
    elif (
        isinstance(version_raw, int) and not isinstance(version_raw, bool) and version_raw in (0, 1)
    ):
        version = version_raw
    else:
        raise ParseError("version", f"unsupported transaction version {version_raw!r}")
    txo = obj(r.get("transaction"), "transaction.transaction")
    sigs = arr(txo.get("signatures"), "transaction.signatures", max_len=64)
    if not sigs:
        raise ParseError("transaction.signatures", "no signatures")
    signature = _signature(sigs[0], "transaction.signatures[0]")
    msg = obj(txo.get("message"), "transaction.message")
    static_keys = [
        _pubkey(k, "message.accountKeys[]")
        for k in arr(msg.get("accountKeys"), "message.accountKeys", max_len=256)
    ]
    if not static_keys:
        raise ParseError("message.accountKeys", "no account keys")
    finality = finality_for(commitment)
    issues: list[str] = []

    meta_raw = r.get("meta")
    keys = list(static_keys)
    status: TxStatus | None = None
    error: str | None = None
    fee: int | None = None
    meta = None
    if meta_raw is None:
        issues.append("meta missing: status, fee and balance changes unknown")
    else:
        meta = obj(meta_raw, "meta")
        loaded = meta.get("loadedAddresses")
        if loaded is not None:
            lo = obj(loaded, "meta.loadedAddresses")
            keys += [
                _pubkey(k, "loadedAddresses.writable[]")
                for k in arr(lo.get("writable", []), "writable", max_len=256)
            ]
            keys += [
                _pubkey(k, "loadedAddresses.readonly[]")
                for k in arr(lo.get("readonly", []), "readonly", max_len=256)
            ]
        elif version == 0:
            issues.append(
                "v0 transaction without loadedAddresses: lookup-table accounts unresolved"
            )
        err = meta.get("err")
        status = TxStatus.FAILED if err is not None else TxStatus.SUCCESS
        error = json.dumps(err, separators=(",", ":"))[:500] if err is not None else None
        fee = integer(meta.get("fee"), "meta.fee")

    try:
        tx = SolanaTransaction(
            chain=CHAIN,
            signature=signature,
            slot=slot,
            block_time=block_time,
            fee_payer=static_keys[0],
            fee_lamports=fee,
            status=status,
            error=error,
            version=version,
            provenance=_provenance(src, slot, signature, block_time, finality),
        )
    except ValidationError as exc:
        raise ParseError("transaction", exc.errors(include_input=False)[0]["msg"]) from None

    def key_at(index: object, where: str) -> str | None:
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(keys):
            issues.append(f"{where}: account index out of range")
            return None
        return keys[index]

    # Token balance metadata, used to resolve mints/owners for SPL instructions.
    token_meta: dict[int, tuple[str, str | None, int]] = {}
    pre_tokens: dict[tuple[int, str], int] = {}
    post_tokens: dict[tuple[int, str], int] = {}
    if meta is not None:
        for label, target in (("preTokenBalances", pre_tokens), ("postTokenBalances", post_tokens)):
            for j, tb in enumerate(arr(meta.get(label) or [], f"meta.{label}", max_len=512)):
                t = obj(tb, f"meta.{label}[{j}]")
                idx = integer(t.get("accountIndex"), f"{label}[{j}].accountIndex", maximum=1024)
                mint = _pubkey(t.get("mint"), f"{label}[{j}].mint")
                owner_raw = t.get("owner")
                owner = _pubkey(owner_raw, f"{label}[{j}].owner") if owner_raw is not None else None
                ui = obj(t.get("uiTokenAmount"), f"{label}[{j}].uiTokenAmount")
                amount_text = text(ui.get("amount"), f"{label}[{j}].amount", max_len=80)
                if not (amount_text.isascii() and amount_text.isdigit()):
                    raise ParseError(f"{label}[{j}].amount", "expected a decimal integer string")
                decimals = integer(ui.get("decimals"), f"{label}[{j}].decimals", maximum=255)
                target[(idx, mint)] = int(amount_text)
                token_meta[idx] = (mint, owner, decimals)

    # Instructions: outer + inner, decode only the documented transfer layouts.
    program_ids: list[str] = []
    instruction_count = 0
    unknown = 0
    outer = arr(msg.get("instructions"), "message.instructions", max_len=256)
    inner_map: dict[int, list[object]] = {}
    if meta is not None:
        for k, group in enumerate(
            arr(meta.get("innerInstructions") or [], "meta.innerInstructions", max_len=256)
        ):
            g = obj(group, f"innerInstructions[{k}]")
            inner_map[integer(g.get("index"), f"innerInstructions[{k}].index", maximum=256)] = arr(
                g.get("instructions"), f"innerInstructions[{k}].instructions", max_len=512
            )

    transfers: list[tuple[TokenTransfer, str]] = []

    def handle(ix_raw: object, path: tuple[int, ...]) -> None:
        nonlocal instruction_count, unknown
        instruction_count += 1
        where = "ix" + ".".join(str(p) for p in path)
        ix = obj(ix_raw, where)
        program = key_at(ix.get("programIdIndex"), f"{where}.programIdIndex")
        if program is None:
            unknown += 1
            return
        if program not in program_ids:
            program_ids.append(program)
        accounts_raw = arr(ix.get("accounts", []), f"{where}.accounts", max_len=256)
        accts = [key_at(a, f"{where}.accounts[]") for a in accounts_raw]
        acct_idx = [
            a if isinstance(a, int) and not isinstance(a, bool) else -1 for a in accounts_raw
        ]
        data = _decode_data(ix.get("data"))
        decoded = None
        try:
            decoded = _decode_transfer(program, data, accts, acct_idx, token_meta, tx, path)
        except (ValidationError, ValueError) as exc:
            issues.append(f"{where}: transfer instruction failed validation ({type(exc).__name__})")
        if decoded is None:
            unknown += 1
        else:
            transfers.append(decoded)

    if status is TxStatus.FAILED:
        # A failed transaction's instructions were not applied: count them, decode nothing.
        issues.append("failed transaction: instructions were not applied, no transfers decoded")
        for i, ix in enumerate(outer):
            instruction_count += 1 + len(inner_map.get(i, []))
            unknown += 1 + len(inner_map.get(i, []))
            program = key_at(obj(ix, f"ix{i}").get("programIdIndex"), f"ix{i}.programIdIndex")
            if program is not None and program not in program_ids:
                program_ids.append(program)
    else:
        for i, ix in enumerate(outer):
            handle(ix, (i,))
            for j, inner in enumerate(inner_map.get(i, [])):
                handle(inner, (i, j))

    parsed = ParsedSolanaTx(
        tx=tx,
        account_keys=tuple(keys),
        program_ids=tuple(program_ids),
        instruction_count=instruction_count,
        unknown_instruction_count=unknown,
        transfers=transfers,
        issues=issues,
    )

    # Balance changes for the wallet itself and for token accounts it owns.
    if meta is not None:
        pre = arr(meta.get("preBalances"), "meta.preBalances", max_len=512)
        post = arr(meta.get("postBalances"), "meta.postBalances", max_len=512)
        if len(pre) != len(keys) or len(post) != len(keys):
            issues.append("pre/postBalances length does not match account keys")
        for idx, key in enumerate(keys):
            if key != wallet or idx >= len(pre) or idx >= len(post):
                continue
            parsed.balance_changes.append(
                BalanceChange(
                    account=key,
                    owner=None,
                    mint=None,
                    kind="native_balance_change",
                    pre_raw=integer(pre[idx], f"preBalances[{idx}]"),
                    post_raw=integer(post[idx], f"postBalances[{idx}]"),
                    decimals=9,
                )
            )
        for idx_mint in sorted(set(pre_tokens) | set(post_tokens)):
            idx, mint = idx_mint
            _, owner, decimals = token_meta.get(idx, (mint, None, 0))
            account = keys[idx] if 0 <= idx < len(keys) else None
            if account is None:
                issues.append(f"token balance accountIndex {idx} out of range")
                continue
            if owner != wallet and account != wallet:
                continue
            parsed.balance_changes.append(
                BalanceChange(
                    account=account,
                    owner=owner,
                    mint=mint,
                    kind="token_balance_change",
                    pre_raw=pre_tokens.get(idx_mint, 0 if idx_mint in post_tokens else None),
                    post_raw=post_tokens.get(idx_mint, 0 if idx_mint in pre_tokens else None),
                    decimals=decimals,
                )
            )
    return parsed


def _decode_transfer(
    program: str,
    data: bytes | None,
    accts: list[str | None],
    acct_idx: list[int],
    token_meta: dict[int, tuple[str, str | None, int]],
    tx: SolanaTransaction,
    path: tuple[int, ...],
) -> tuple[TokenTransfer, str] | None:
    if data is None:
        return None
    common = {
        "chain": CHAIN,
        "tx": SolanaTxRef(signature=tx.signature),
        "position": SlotPosition(slot=tx.slot),
        "instruction_path": path,
        "provenance": tx.provenance,
    }
    if program == SYSTEM_PROGRAM:
        if len(data) == 12 and struct.unpack_from("<I", data, 0)[0] == 2 and len(accts) >= 2:
            src, dst = accts[0], accts[1]
            if src is None or dst is None:
                return None
            lamports = struct.unpack_from("<Q", data, 4)[0]
            t = TokenTransfer(
                standard=TokenStandard.NATIVE,
                token=None,
                from_address=AddressRef(chain=CHAIN, address=src),
                to_address=AddressRef(chain=CHAIN, address=dst),
                amount_raw=lamports,
                decimals=9,
                **common,  # type: ignore[arg-type]
            )
            return t, "solana_system_transfer_ix"
        return None
    standard = _TOKEN_PROGRAMS.get(program)
    if standard is None or not data:
        return None
    tag = data[0]
    if tag == 3 and len(data) == 9 and len(accts) >= 3:  # Transfer
        src, dst = accts[0], accts[1]
        amount = struct.unpack_from("<Q", data, 1)[0]
        meta = token_meta.get(acct_idx[0]) or token_meta.get(acct_idx[1])
        if meta is None or src is None or dst is None:
            return None  # mint not resolvable from metadata: left unknown, not guessed
        mint, decimals = meta[0], meta[2]
    elif tag == 12 and len(data) == 10 and len(accts) >= 4:  # TransferChecked
        src, mint_key, dst = accts[0], accts[1], accts[2]
        amount = struct.unpack_from("<Q", data, 1)[0]
        decimals = data[9]
        if src is None or dst is None or mint_key is None:
            return None
        mint = mint_key
    else:
        return None
    t = TokenTransfer(
        standard=standard,
        token=AddressRef(chain=CHAIN, address=mint),
        from_address=AddressRef(chain=CHAIN, address=src, kind=AddressKind.TOKEN_ACCOUNT),
        to_address=AddressRef(chain=CHAIN, address=dst, kind=AddressKind.TOKEN_ACCOUNT),
        amount_raw=amount,
        decimals=decimals,
        **common,  # type: ignore[arg-type]
    )
    return t, "spl_transfer_ix" if tag == 3 else "spl_transfer_checked_ix"
