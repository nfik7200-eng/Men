"""Read-only Ethereum JSON-RPC adapter (execution-layer standard methods only)."""

from __future__ import annotations

from typing import cast

from bip.chains.base import ChainAdapter
from bip.chains.common import ParseError, SourceInfo, arr
from bip.chains.ethereum.parse import (
    TRANSFER_TOPIC,
    BlockHeader,
    pad_address_topic,
    parse_block_header,
    quantity,
)
from bip.rpc.jsonrpc import RpcError

BlockTag = int | str  # number or "latest" / "finalized" / "safe"

MAX_LOGS_PER_RESPONSE = 10_000
"""Largest eth_getLogs result BIP accepts before splitting the block window."""


def _tag(block: BlockTag) -> str:
    return hex(block) if isinstance(block, int) else block


def _length(value: object) -> int:
    """Number of entries in a JSON array result; 0 for anything that is not an array."""
    return len(cast("list[object]", value)) if isinstance(value, list) else 0


class EthereumAdapter(ChainAdapter):
    chain = "ethereum"
    single_block_logs = False
    """Set once a provider has refused an address-less ``eth_getLogs`` over several blocks (seen
    live on PublicNode); later address-less queries then go block by block without trying a wider
    window first."""

    def chain_id(self) -> int:
        result, _ = self.call("eth_chainId", [])
        return quantity(result, "eth_chainId")

    def block_number(self) -> int:
        result, _ = self.call("eth_blockNumber", [])
        return quantity(result, "eth_blockNumber")

    def block(self, block: BlockTag) -> tuple[BlockHeader, SourceInfo] | None:
        """Block header (tx hashes, not bodies); ``None`` if the provider has no such block."""
        result, src = self.call("eth_getBlockByNumber", [_tag(block), False])
        if result is None:
            return None
        return parse_block_header(result), src

    def block_full(self, block: BlockTag) -> tuple[BlockHeader, list[object], SourceInfo] | None:
        """Block header plus every transaction body; ``None`` if the provider has no such block."""
        result, src = self.call("eth_getBlockByNumber", [_tag(block), True])
        if result is None:
            return None
        header = parse_block_header(result)
        raw = cast("dict[str, object]", result).get("transactions")
        return header, list(cast("list[object]", raw)) if isinstance(raw, list) else [], src

    def finalized_block(self) -> tuple[BlockHeader, SourceInfo] | None:
        """The ``finalized`` tag (post-Merge). ``None`` when the provider does not support it."""
        try:
            return self.block("finalized")
        except RpcError as exc:
            if exc.error_class in {"invalid_request", "method_unsupported", "not_available"}:
                return None
            raise

    def balance(self, address: str, block: BlockTag = "latest") -> int:
        result, _ = self.call("eth_getBalance", [address, _tag(block)])
        return quantity(result, "eth_getBalance")

    def transaction_count(self, address: str, block: BlockTag = "latest") -> int:
        result, _ = self.call("eth_getTransactionCount", [address, _tag(block)])
        return quantity(result, "eth_getTransactionCount")

    def transaction(self, tx_hash: str) -> tuple[object, SourceInfo]:
        return self.call("eth_getTransactionByHash", [tx_hash])

    def receipt(self, tx_hash: str) -> tuple[object, SourceInfo]:
        return self.call("eth_getTransactionReceipt", [tx_hash])

    def transfer_logs(
        self, wallet: str, from_block: int, to_block: int, *, direction: str
    ) -> tuple[list[object], SourceInfo]:
        """``Transfer`` logs where ``wallet`` is the sender (``from``) or recipient (``to``)."""
        topic = pad_address_topic(wallet)
        topics: list[object] = (
            [TRANSFER_TOPIC, topic] if direction == "from" else [TRANSFER_TOPIC, None, topic]
        )
        result, src = self.call(
            "eth_getLogs",
            [{"fromBlock": hex(from_block), "toBlock": hex(to_block), "topics": topics}],
        )
        # An oversized response is a provider-side range problem, not a malformed payload: report
        # it as range_too_large so transfer_logs_adaptive splits the window instead of aborting.
        returned = _length(result)
        if returned > MAX_LOGS_PER_RESPONSE:
            raise RpcError(
                "range_too_large",
                f"eth_getLogs returned more than {MAX_LOGS_PER_RESPONSE} logs for "
                f"{from_block}-{to_block}; the window must be split",
            )
        logs = arr(result, "eth_getLogs", max_len=MAX_LOGS_PER_RESPONSE)
        return logs, src

    def event_logs_adaptive(
        self,
        from_block: int,
        to_block: int,
        *,
        topics: tuple[str, ...],
        addresses: tuple[str, ...] | None = None,
        min_span: int = 1,
    ) -> tuple[list[tuple[object, SourceInfo]], list[str]]:
        """Logs whose first topic is any of ``topics``, optionally from given contracts.

        No address filter means every contract: fine for rare factory events, heavy for common
        ones such as ERC-20 ``Transfer``. The window is split whenever the provider (or the
        10,000-log cap) says the range is too large; a one-block window that is still too large
        raises.
        """
        out: list[tuple[object, SourceInfo]] = []
        notes: list[str] = []
        stack = (
            [(n, n) for n in range(to_block, from_block - 1, -1)]
            if self.single_block_logs and not addresses
            else [(from_block, to_block)]
        )
        while stack:
            a, b = stack.pop()
            flt: dict[str, object] = {
                "fromBlock": hex(a),
                "toBlock": hex(b),
                "topics": [list(topics)],
            }
            if addresses:
                flt["address"] = list(addresses)
            try:
                result, src = self.call("eth_getLogs", [flt])
                if _length(result) > MAX_LOGS_PER_RESPONSE:
                    raise RpcError("range_too_large", f"more than {MAX_LOGS_PER_RESPONSE} logs")
                logs = arr(result, "eth_getLogs", max_len=MAX_LOGS_PER_RESPONSE)
                out.extend((lg, src) for lg in logs)
            except RpcError as exc:
                if exc.error_class != "range_too_large" or b - a + 1 <= min_span:
                    raise
                if not addresses and "specify an address" in str(exc).lower():
                    # The provider only serves address-less queries one block at a time: do not
                    # bisect (that costs about twice the blocks in failed requests); go block by
                    # block, here and for the rest of this adapter's life.
                    self.single_block_logs = True
                    notes.append(f"provider requires an address over several blocks: {a}-{b}")
                    stack.extend((n, n) for n in range(a, b + 1))
                    continue
                mid = (a + b) // 2
                notes.append(f"split {a}-{b} after provider range limit")
                stack.extend([(a, mid), (mid + 1, b)])
        return out, notes

    def transfer_logs_adaptive(
        self, wallet: str, from_block: int, to_block: int, *, direction: str, min_span: int = 1
    ) -> tuple[list[tuple[object, SourceInfo]], list[str]]:
        """Like :meth:`transfer_logs` but splits the range when the provider says it is too large.

        Returns ``(logs_with_source, notes)``; notes record any splits performed.
        """
        out: list[tuple[object, SourceInfo]] = []
        notes: list[str] = []
        stack = [(from_block, to_block)]
        while stack:
            a, b = stack.pop()
            try:
                logs, src = self.transfer_logs(wallet, a, b, direction=direction)
                out.extend((lg, src) for lg in logs)
            except RpcError as exc:
                if exc.error_class != "range_too_large" or b - a + 1 <= min_span:
                    raise
                mid = (a + b) // 2
                notes.append(f"split {a}-{b} after provider range limit")
                stack.extend([(a, mid), (mid + 1, b)])
            except ParseError:
                raise
        return out, notes
