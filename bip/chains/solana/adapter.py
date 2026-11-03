"""Read-only Solana JSON-RPC adapter."""

from __future__ import annotations

from bip.chains.base import ChainAdapter
from bip.chains.common import ParseError, SourceInfo, integer, obj, text, unix_time
from bip.chains.solana.parse import Commitment, SignatureInfo, parse_signatures
from bip.rpc.jsonrpc import RpcError


class SolanaAdapter(ChainAdapter):
    chain = "solana"

    def health(self) -> str:
        result, _ = self.call("getHealth", [])
        if result != "ok":
            raise ParseError("getHealth", "node did not report ok")
        return "ok"

    def genesis_hash(self) -> str:
        result, _ = self.call("getGenesisHash", [])
        return text(result, "getGenesisHash", max_len=44)

    def slot(self, commitment: Commitment) -> int:
        result, _ = self.call("getSlot", [{"commitment": commitment}])
        return integer(result, "getSlot")

    def block_summary(
        self, slot: int, commitment: Commitment
    ) -> tuple[dict[str, object], SourceInfo] | None:
        """Block metadata (no transactions/rewards); ``None`` for skipped or missing slots."""
        try:
            result, src = self.call(
                "getBlock",
                [
                    slot,
                    {
                        "commitment": commitment,
                        "transactionDetails": "none",
                        "rewards": False,
                        "maxSupportedTransactionVersion": 0,
                    },
                ],
            )
        except RpcError as exc:
            if exc.error_class == "not_available":
                return None
            raise
        if result is None:
            return None
        b = obj(result, "getBlock")
        bt = b.get("blockTime")
        summary: dict[str, object] = {
            "slot": slot,
            "blockhash": text(b.get("blockhash"), "getBlock.blockhash", max_len=44),
            "previous_blockhash": text(
                b.get("previousBlockhash"), "getBlock.previousBlockhash", max_len=44
            ),
            "parent_slot": integer(b.get("parentSlot"), "getBlock.parentSlot"),
            "block_time": unix_time(integer(bt, "getBlock.blockTime"), "getBlock.blockTime")
            if bt is not None
            else None,
            "block_height": integer(b["blockHeight"], "getBlock.blockHeight")
            if b.get("blockHeight") is not None
            else None,
        }
        return summary, src

    def balance(self, address: str, commitment: Commitment) -> int:
        result, _ = self.call("getBalance", [address, {"commitment": commitment}])
        return integer(obj(result, "getBalance").get("value"), "getBalance.value")

    def signatures(
        self,
        address: str,
        *,
        limit: int,
        commitment: Commitment,
        before: str | None = None,
        until: str | None = None,
    ) -> list[SignatureInfo]:
        opts: dict[str, object] = {"limit": limit, "commitment": commitment}
        if before:
            opts["before"] = before
        if until:
            opts["until"] = until
        result, _ = self.call("getSignaturesForAddress", [address, opts])
        return parse_signatures(result)

    def transaction(self, signature: str, commitment: Commitment) -> tuple[object, SourceInfo]:
        return self.call(
            "getTransaction",
            [
                signature,
                {"encoding": "json", "commitment": commitment, "maxSupportedTransactionVersion": 0},
            ],
        )
