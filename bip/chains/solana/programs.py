"""Wallet-agnostic decoding of the instructions that announce new things on Solana.

The wallet parser (``parse.py``) answers "what did *this wallet* do". Discovery asks the opposite:
"what was created, whoever did it". This module reads the compiled (``json`` encoded) instructions
of a ``getBlock`` transaction and recognises exactly four layouts, each tied to an official source
checked on ``VERIFIED_AT``. Anything else is left alone: an unknown program is never guessed at.

========================  ====================================================================
SPL Token / Token-2022    ``InitializeMint`` (tag 0) and ``InitializeMint2`` (tag 20); the mint
                          is account 0. Source: solana-program/token, ``interface/src/
                          instruction.rs`` (pack writes ``buf.push(0)`` / ``buf.push(20)``).
Upgradeable BPF loader    ``DeployWithMaxDataLen`` is variant 2 of a bincode enum, so the data
                          starts with u32 LE 2; the program is account 2. Source: anza-xyz/
                          solana-sdk, ``loader-v3-interface/src/instruction.rs``; account
                          order also in agave ``transaction-status/src/parse_bpf_loader.rs``.
Raydium AMM v4            ``Initialize2`` is tag 1; pool = account 4, coin mint = account 8,
                          pc mint = account 9. Source: raydium-io/raydium-amm, ``program/src/
                          instruction.rs`` and ``lib.rs`` (``declare_id!``).
Orca Whirlpools           ``initialize_pool``; Anchor discriminator = first 8 bytes of
                          sha256("global:initialize_pool"); pool = account 4, mint A = 1,
                          mint B = 2. Source: orca-so/whirlpools, ``programs/whirlpool/src/
                          lib.rs`` and ``instructions/initialize_pool.rs``.
========================  ====================================================================

Not covered, on purpose: ``initialize_pool_v2`` and other pool programs (Raydium CPMM/CLMM,
Meteora, pump.fun) whose creation layouts were not verified here.
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from bip.contracts.identifiers import b58decode

VERIFIED_AT = "2026-11-09"

SYSTEM_PROGRAM = "11111111111111111111111111111111"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"  # noqa: S105 - program id
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PQnBqCXEpPxuEb"  # noqa: S105 - program id
BPF_LOADER_UPGRADEABLE = "BPFLoaderUpgradeab1e11111111111111111111111"
RAYDIUM_AMM_V4 = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
ORCA_WHIRLPOOL = "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"
ORCA_INITIALIZE_POOL = hashlib.sha256(b"global:initialize_pool").digest()[:8]

RELEVANT_PROGRAMS = frozenset(
    {
        SYSTEM_PROGRAM,
        TOKEN_PROGRAM,
        TOKEN_2022_PROGRAM,
        BPF_LOADER_UPGRADEABLE,
        RAYDIUM_AMM_V4,
        ORCA_WHIRLPOOL,
    }
)
MAX_DATA_CHARS = 400
"""Longest base58 instruction data decoded (about 290 bytes; the layouts used are under 60)."""

INIT_MINT, INIT_MINT2 = 0, 20
DEPLOY_WITH_MAX_DATA_LEN = 2
RAYDIUM_INITIALIZE2 = 1
SYSTEM_TRANSFER = 2


@dataclass(frozen=True, slots=True)
class KnownProgram:
    program_id: str
    name: str
    source_url: str
    verified_at: str = VERIFIED_AT


KNOWN_PROGRAMS: tuple[KnownProgram, ...] = (
    KnownProgram(
        BPF_LOADER_UPGRADEABLE,
        "Upgradeable BPF loader",
        "https://raw.githubusercontent.com/anza-xyz/solana-sdk/master/sdk-ids/src/lib.rs",
    ),
    KnownProgram(
        RAYDIUM_AMM_V4,
        "Raydium AMM v4",
        "https://raw.githubusercontent.com/raydium-io/raydium-amm/master/program/src/lib.rs",
    ),
    KnownProgram(
        ORCA_WHIRLPOOL,
        "Orca Whirlpools",
        "https://raw.githubusercontent.com/orca-so/whirlpools/main/programs/whirlpool/src/lib.rs",
    ),
)


@dataclass(frozen=True, slots=True)
class Discovery:
    kind: str  # "mint" | "program" | "pool"
    ref: str
    detail: Mapping[str, object] = field(default_factory=dict[str, object])


@dataclass(frozen=True, slots=True)
class Ix:
    program: str
    accounts: tuple[str, ...]
    data: bytes


def as_dict(x: object) -> dict[str, Any] | None:
    return cast("dict[str, Any]", x) if isinstance(x, dict) else None


def as_list(x: object) -> list[Any] | None:
    return cast("list[Any]", x) if isinstance(x, list) else None


def _keys(tx: Mapping[str, Any]) -> list[str] | None:
    """Static account keys followed by the address-table keys the runtime resolved (v0/v1)."""
    msg = as_dict((as_dict(tx.get("transaction")) or {}).get("message"))
    keys = as_list((msg or {}).get("accountKeys"))
    if keys is None or not all(isinstance(k, str) for k in keys):
        return None
    out = [str(k) for k in keys]
    loaded = as_dict((as_dict(tx.get("meta")) or {}).get("loadedAddresses")) or {}
    for side in ("writable", "readonly"):
        out.extend(str(k) for k in as_list(loaded.get(side)) or [])
    return out


def _ix(raw: object, keys: list[str]) -> Ix | None:
    ix = as_dict(raw)
    if ix is None:
        return None
    pid, accts, data = ix.get("programIdIndex"), as_list(ix.get("accounts")), ix.get("data")
    if not isinstance(pid, int) or isinstance(pid, bool) or not 0 <= pid < len(keys):
        return None
    if accts is None or not isinstance(data, str):
        return None
    # Only programs this module decodes are looked at, and only short data: base58 decoding is
    # quadratic, and a hostile provider could otherwise pin a core with one huge string.
    if keys[pid] not in RELEVANT_PROGRAMS or len(data) > MAX_DATA_CHARS:
        return None
    names: list[str] = []
    for a in accts:
        if not isinstance(a, int) or isinstance(a, bool) or not 0 <= a < len(keys):
            return None
        names.append(keys[a])
    try:
        return Ix(keys[pid], tuple(names), b58decode(data) if data else b"")
    except ValueError:
        return None


def instructions(tx: Mapping[str, Any]) -> list[Ix]:
    """Outer and inner instructions of one ``getBlock`` transaction entry (compiled form)."""
    keys = _keys(tx)
    if keys is None:
        return []
    message = as_dict((as_dict(tx.get("transaction")) or {}).get("message")) or {}
    raw: list[Any] = list(as_list(message.get("instructions")) or [])
    for group in as_list((as_dict(tx.get("meta")) or {}).get("innerInstructions")) or []:
        raw.extend(as_list((as_dict(group) or {}).get("instructions")) or [])
    return [ix for r in raw if (ix := _ix(r, keys)) is not None]


def _failed(tx: Mapping[str, Any]) -> bool:
    """Failed, or of unknown status (no ``meta``): neither applied its instructions as far as
    BIP can tell."""
    meta = as_dict(tx.get("meta"))
    return meta is None or meta.get("err") is not None


def decode_discoveries(tx: Mapping[str, Any]) -> list[Discovery]:
    """New mints, programs and pools announced by a *successful* transaction."""
    if _failed(tx):
        return []  # a failed transaction applied none of its instructions
    found: dict[tuple[str, str], Discovery] = {}
    for ix in instructions(tx):
        d = _decode(ix)
        if d is not None:
            found.setdefault((d.kind, d.ref), d)
    return list(found.values())


def _decode(ix: Ix) -> Discovery | None:
    a, d = ix.accounts, ix.data
    if ix.program in (TOKEN_PROGRAM, TOKEN_2022_PROGRAM) and a and d:
        if d[0] == INIT_MINT or d[0] == INIT_MINT2:
            return Discovery(
                "mint",
                a[0],
                {
                    "token_program": ix.program,
                    "decimals": d[1] if len(d) > 1 else None,
                    "instruction": "InitializeMint2" if d[0] == INIT_MINT2 else "InitializeMint",
                },
            )
    elif ix.program == BPF_LOADER_UPGRADEABLE and len(a) >= 3 and len(d) >= 4:
        if struct.unpack_from("<I", d)[0] == DEPLOY_WITH_MAX_DATA_LEN:
            return Discovery("program", a[2], {"payer": a[0], "program_data": a[1]})
    elif ix.program == RAYDIUM_AMM_V4 and len(a) >= 10 and d and d[0] == RAYDIUM_INITIALIZE2:
        return Discovery("pool", a[4], {"dex": "Raydium AMM v4", "mint_a": a[8], "mint_b": a[9]})
    elif ix.program == ORCA_WHIRLPOOL and len(a) >= 5 and d[:8] == ORCA_INITIALIZE_POOL:
        return Discovery("pool", a[4], {"dex": "Orca Whirlpools", "mint_a": a[1], "mint_b": a[2]})
    return None


def system_transfers(tx: Mapping[str, Any]) -> list[tuple[str, str, int]]:
    """``(from, to, lamports)`` of every System Program transfer in a successful transaction."""
    if _failed(tx):
        return []
    out: list[tuple[str, str, int]] = []
    for ix in instructions(tx):
        if ix.program == SYSTEM_PROGRAM and len(ix.accounts) >= 2 and len(ix.data) == 12:
            tag, lamports = struct.unpack("<IQ", ix.data)
            if tag == SYSTEM_TRANSFER:
                out.append((ix.accounts[0], ix.accounts[1], int(lamports)))
    return out
