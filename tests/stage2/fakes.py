# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false
# Justification: these fixtures build and mutate raw, untyped JSON-RPC payloads on purpose.
"""Deterministic offline stand-ins for Ethereum and Solana JSON-RPC nodes (tests only).

They answer the real method shapes so the real router, adapters, parsers and ingestion run
end to end without network access. Data is synthetic. Nothing here proves a real provider works.
"""

from __future__ import annotations

import json
import struct
from collections.abc import Callable
from dataclasses import dataclass, field

from bip.providers.registry import ResolvedEndpoint
from bip.rpc.transport import TransportError, TransportResponse
from tests.base58 import b58encode as _b58encode

TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
SOL_GENESIS = "5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d"
SYSTEM = "11111111111111111111111111111111"
TOKEN = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"


def h32(n: int) -> str:
    return "0x" + f"{n:064x}"


def addr(n: int) -> str:
    return "0x" + f"{n:040x}"


def topic(a: str) -> str:
    return "0x" + "0" * 24 + a[2:]


def key(n: int) -> str:
    return _b58encode(bytes([n]) * 32)


def sig(n: int) -> str:
    return _b58encode(bytes([n]) * 64)


# --------------------------------------------------------------------------- Ethereum


@dataclass
class FakeEthNode:
    head: int = 20_000_000
    chain_id: str = "0x1"
    max_log_range: int | None = None
    wallet: str = addr(0xAA)
    token: str = addr(0x70)
    other: str = addr(0xBB)
    calls: list[str] = field(default_factory=list[str])
    break_receipt_for: str | None = None
    reorg_tx: str | None = None
    unsupported: set[str] = field(default_factory=set[str])
    history_floor: int = 20_000_000 - 200_000
    """Blocks below this number are 'not served' (null), like a pruned node."""

    def __post_init__(self) -> None:
        h = self.head
        self.blocks = {n: self._block(n) for n in (h - 64, h - 10, h - 5, h)}
        # tx A: wallet sends 1.5 tokens (ERC-20) to other, no ETH value
        self.txa = h32(0xA1)
        # tx B: other sends 2 ETH + 3 tokens to wallet
        self.txb = h32(0xB2)
        self.txs = {
            self.txa: self._tx(self.txa, h - 10, 0, self.wallet, self.token, 0),
            self.txb: self._tx(self.txb, h - 5, 3, self.other, self.wallet, 2 * 10**18),
            # unrelated head-block tx (listed in the head block; used by capability probes)
            h32(h): self._tx(h32(h), h, 0, self.other, self.token, 0),
        }
        self.logs = [
            self._log(
                self.txa, h - 10, 0, 7, [TRANSFER, topic(self.wallet), topic(self.other)], 1_500_000
            ),
            self._log(
                self.txb, h - 5, 3, 2, [TRANSFER, topic(self.other), topic(self.wallet)], 3_000_000
            ),
            # head block: other -> token contract (not involving the wallet)
            self._log(h32(h), h, 0, 0, [TRANSFER, topic(self.other), topic(self.token)], 7),
        ]

    def _block(self, n: int) -> dict[str, object]:
        return {
            "number": hex(n),
            "hash": h32(0xB10C000 + n % 100000),
            "parentHash": h32(0xB10C000 + (n - 1) % 100000),
            "timestamp": hex(1_760_000_000 + n % 100000 * 12),
            "transactions": [h32(n)],
        }

    def _tx(self, txh: str, n: int, idx: int, frm: str, to: str, value: int) -> dict[str, object]:
        return {
            "hash": txh,
            "blockNumber": hex(n),
            "blockHash": self.blocks[n]["hash"],
            "transactionIndex": hex(idx),
            "from": frm,
            "to": to,
            "value": hex(value),
            "nonce": "0x4",
            "gasPrice": hex(30 * 10**9),
        }

    def _log(
        self, txh: str, n: int, txi: int, li: int, topics: list[str], amount: int
    ) -> dict[str, object]:
        return {
            "address": self.token,
            "blockNumber": hex(n),
            "blockHash": self.blocks[n]["hash"],
            "transactionHash": txh,
            "transactionIndex": hex(txi),
            "logIndex": hex(li),
            "topics": topics,
            "data": "0x" + f"{amount:064x}",
            "removed": False,
        }

    def handle(self, method: str, params: list[object]) -> object:
        self.calls.append(method)
        if method in self.unsupported:
            raise _RpcFaultError(-32601, "the method does not exist/is not available")
        if method == "eth_getBalance" and params and params[0] == "bad":
            raise _RpcFaultError(-32602, "invalid argument 0: hex string without 0x prefix")
        if method == "eth_chainId":
            return self.chain_id
        if method == "eth_blockNumber":
            return hex(self.head)
        if method == "eth_getBlockByNumber":
            tag = params[0]
            n = (
                self.head
                if tag == "latest"
                else self.head - 64
                if tag == "finalized"
                else int(str(tag), 16)
            )
            if n not in self.blocks and self.history_floor <= n <= self.head:
                return self._block(n)  # synthesized historical header
            return self.blocks.get(n)
        if method == "eth_getCode":
            return "0x"
        if method == "eth_getBalance":
            return hex(5 * 10**18)
        if method == "eth_getTransactionCount":
            return "0x5"
        if method == "eth_getLogs":
            f = params[0]
            assert isinstance(f, dict)
            a, b = int(f["fromBlock"], 16), int(f["toBlock"], 16)
            if self.max_log_range is not None and b - a + 1 > self.max_log_range:
                raise _RpcFaultError(-32005, "query exceeds max block range 1000")
            want = f["topics"]
            out = []
            for lg in self.logs:
                n = int(str(lg["blockNumber"]), 16)
                t = lg["topics"]
                assert isinstance(t, list)
                if a <= n <= b and all(
                    w is None or (i < len(t) and t[i] == w) for i, w in enumerate(want)
                ):
                    out.append(lg)
            return out
        if method == "eth_getTransactionByHash":
            tx = self.txs.get(str(params[0]))
            if tx is not None and params[0] == self.reorg_tx:
                return {**tx, "blockHash": h32(0xDEAD)}
            return tx
        if method == "eth_getTransactionReceipt":
            txh = str(params[0])
            tx = self.txs.get(txh)
            if tx is None or txh == self.break_receipt_for:
                return None
            return {
                "transactionHash": txh,
                "blockHash": tx["blockHash"],
                "status": "0x1",
                "gasUsed": hex(51_000),
                "contractAddress": None,
                "logs": [lg for lg in self.logs if lg["transactionHash"] == txh],
            }
        raise _RpcFaultError(-32601, "method not found")


# --------------------------------------------------------------------------- Solana


def system_transfer_data(lamports: int) -> str:
    return _b58encode(struct.pack("<IQ", 2, lamports))


def transfer_checked_data(amount: int, decimals: int) -> str:
    return _b58encode(bytes([12]) + struct.pack("<Q", amount) + bytes([decimals]))


@dataclass
class FakeSolNode:
    head_slot: int = 300_000_000
    genesis: str = SOL_GENESIS
    wallet: str = key(1)
    calls: list[str] = field(default_factory=list[str])
    missing_sig: str | None = None

    def __post_init__(self) -> None:
        self.recipient = key(2)
        self.mint = key(3)
        self.src_ata = key(4)
        self.dst_ata = key(5)
        self.lut_key = key(6)
        self.unknown_program = key(7)
        self.sig_a = sig(0x11)
        self.sig_b = sig(0x22)
        self.sig_c = sig(0x33)
        self.sigs = [
            {
                "signature": self.sig_a,
                "slot": self.head_slot - 10,
                "blockTime": 1_760_000_100,
                "err": None,
                "confirmationStatus": "finalized",
            },
            {
                "signature": self.sig_b,
                "slot": self.head_slot - 20,
                "blockTime": None,
                "err": None,
                "confirmationStatus": "finalized",
            },
            {
                "signature": self.sig_c,
                "slot": self.head_slot - 30,
                "blockTime": 1_760_000_000,
                "err": {"InstructionError": [0, "Custom"]},
                "confirmationStatus": "finalized",
            },
        ]

    def _tx_a(self) -> dict[str, object]:
        # legacy: system transfer wallet -> recipient (1 SOL) + SPL TransferChecked
        keys = [self.wallet, self.recipient, self.src_ata, self.dst_ata, self.mint, SYSTEM, TOKEN]
        return {
            "slot": self.head_slot - 10,
            "blockTime": 1_760_000_100,
            "transaction": {
                "signatures": [self.sig_a],
                "message": {
                    "accountKeys": keys,
                    "instructions": [
                        {
                            "programIdIndex": 5,
                            "accounts": [0, 1],
                            "data": system_transfer_data(10**9),
                        },
                        {
                            "programIdIndex": 6,
                            "accounts": [2, 4, 3, 0],
                            "data": transfer_checked_data(2_500_000, 6),
                        },
                    ],
                },
            },
            "meta": {
                "err": None,
                "fee": 5000,
                "preBalances": [3 * 10**9, 0, 2039280, 2039280, 1461600, 1, 1],
                "postBalances": [2 * 10**9 - 5000, 10**9, 2039280, 2039280, 1461600, 1, 1],
                "preTokenBalances": [
                    {
                        "accountIndex": 2,
                        "mint": self.mint,
                        "owner": self.wallet,
                        "uiTokenAmount": {"amount": "10000000", "decimals": 6},
                    },
                    {
                        "accountIndex": 3,
                        "mint": self.mint,
                        "owner": self.recipient,
                        "uiTokenAmount": {"amount": "0", "decimals": 6},
                    },
                ],
                "postTokenBalances": [
                    {
                        "accountIndex": 2,
                        "mint": self.mint,
                        "owner": self.wallet,
                        "uiTokenAmount": {"amount": "7500000", "decimals": 6},
                    },
                    {
                        "accountIndex": 3,
                        "mint": self.mint,
                        "owner": self.recipient,
                        "uiTokenAmount": {"amount": "2500000", "decimals": 6},
                    },
                ],
                "innerInstructions": [],
            },
            "version": "legacy",
        }

    def _tx_b(self) -> dict[str, object]:
        # v0 with a lookup-table account and an unknown program instruction; wallet pays fee
        return {
            "slot": self.head_slot - 20,
            "blockTime": None,
            "transaction": {
                "signatures": [self.sig_b],
                "message": {
                    "accountKeys": [self.wallet, self.unknown_program],
                    "instructions": [
                        {"programIdIndex": 1, "accounts": [0, 2], "data": "3Bxs4h24hBtQy9rw"}
                    ],
                },
            },
            "meta": {
                "err": None,
                "fee": 5000,
                "preBalances": [2 * 10**9, 1, 0],
                "postBalances": [2 * 10**9 - 5000, 1, 0],
                "preTokenBalances": [],
                "postTokenBalances": [],
                "innerInstructions": [],
                "loadedAddresses": {"writable": [self.lut_key], "readonly": []},
            },
            "version": 0,
        }

    def handle(self, method: str, params: list[object]) -> object:
        self.calls.append(method)
        if method == "getGenesisHash":
            return self.genesis
        if method == "getHealth":
            return "ok"
        if method == "getVersion":
            return {"solana-core": "2.1.0-fake", "feature-set": 1}
        if method == "getBlockHeight":
            return 280_000_000
        if method == "getLatestBlockhash":
            return {
                "context": {"slot": self.head_slot},
                "value": {"blockhash": key(9), "lastValidBlockHeight": 1},
            }
        if method == "getSlot":
            return self.head_slot
        if method == "getBlock":
            slot = params[0]
            if slot == self.head_slot:
                raise _RpcFaultError(
                    -32007, f"Slot {slot} was skipped, or missing due to ledger jump"
                )
            return {
                "blockhash": key(9),
                "previousBlockhash": key(8),
                "parentSlot": int(str(slot)) - 1,
                "blockTime": 1_760_000_200,
                "blockHeight": 280_000_000,
            }
        if method == "getBalance":
            return {"context": {"slot": self.head_slot}, "value": 2 * 10**9}
        if method == "getSignaturesForAddress":
            opts = params[1]
            assert isinstance(opts, dict)
            items = self.sigs
            if opts.get("until"):
                cut = next(
                    (i for i, s in enumerate(items) if s["signature"] == opts["until"]), len(items)
                )
                items = items[:cut]
            if opts.get("before"):
                cut = next(
                    (i for i, s in enumerate(items) if s["signature"] == opts["before"]), len(items)
                )
                items = items[cut + 1 :]
            return items[: int(opts["limit"])]
        if method == "getTransaction":
            s = params[0]
            if s == self.missing_sig:
                return None
            if s == self.sig_a:
                return self._tx_a()
            if s == self.sig_b:
                return self._tx_b()
            if s == self.sig_c:
                raise _RpcFaultError(-32011, "Transaction history is not available from this node")
            return None
        raise _RpcFaultError(-32601, "Method not found")


# --------------------------------------------------------------------------- transport


class _RpcFaultError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


Handler = Callable[[str, list[object]], object]


@dataclass
class Scripted:
    """A response to return instead of the node's answer (for failure injection)."""

    status: int = 200
    body: bytes | None = None
    headers: dict[str, str] = field(default_factory=dict[str, str])
    error: TransportError | None = None


class FakeTransport:
    """Routes requests by endpoint host to fake nodes; supports scripted failures per host."""

    def __init__(self, nodes: dict[str, Handler]) -> None:
        self.nodes = nodes
        self.scripts: dict[str, list[Scripted]] = {}
        self.requests: list[tuple[str, str]] = []

    def script(self, host: str, *items: Scripted) -> None:
        self.scripts.setdefault(host, []).extend(items)

    def post_json(
        self, endpoint: ResolvedEndpoint, body: bytes, *, timeout_s: float, max_bytes: int
    ) -> TransportResponse:
        del timeout_s, max_bytes
        req = json.loads(body)
        self.requests.append((endpoint.host, req["method"]))
        queue = self.scripts.get(endpoint.host)
        if queue:
            item = queue.pop(0)
            if item.error is not None:
                raise item.error
            return TransportResponse(item.status, item.body or b"", 5, item.headers)
        node = self.nodes.get(endpoint.host)
        if node is None:
            raise TransportError("connection", endpoint.host, "no fake node for host")
        try:
            result = node(req["method"], req["params"])
            payload: dict[str, object] = {"jsonrpc": "2.0", "id": req["id"], "result": result}
        except _RpcFaultError as fault:
            payload = {
                "jsonrpc": "2.0",
                "id": req["id"],
                "error": {"code": fault.code, "message": fault.message},
            }
        return TransportResponse(200, json.dumps(payload).encode(), 5, {})


ETH_HOST = "ethereum-rpc.publicnode.com"
SOL_HOST = "api.mainnet.solana.com"
