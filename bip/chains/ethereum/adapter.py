"""Read-only Ethereum JSON-RPC adapter (execution-layer standard methods only)."""

from __future__ import annotations

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


def _tag(block: BlockTag) -> str:
    return hex(block) if isinstance(block, int) else block


class EthereumAdapter(ChainAdapter):
    chain = "ethereum"

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
        logs = arr(result, "eth_getLogs", max_len=10_000)
        return logs, src

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
