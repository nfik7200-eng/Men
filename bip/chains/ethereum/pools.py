"""Decode pool-creation events emitted by Uniswap-style factories.

Two event layouts are recognised. They are layouts, not verified protocols: any contract can emit
them, so a decoded pool says "a contract emitted a pool-creation event", nothing more.

* v2-like ``PairCreated(address indexed token0, address indexed token1, address pair, uint256)``
* v3-like ``PoolCreated(address indexed token0, address indexed token1, uint24 indexed fee,
  int24 tickSpacing, address pool)``
"""

from __future__ import annotations

from dataclasses import dataclass

from bip.chains.ethereum.parse import topic_address
from bip.contracts.events import EvmLog

PAIR_CREATED_TOPIC = "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9"
POOL_CREATED_TOPIC = "0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118"
POOL_TOPICS: tuple[str, str] = (PAIR_CREATED_TOPIC, POOL_CREATED_TOPIC)

_WORD = 64  # hex characters per 32-byte word


@dataclass(frozen=True, slots=True)
class PoolCreated:
    protocol: str  # "v2_like" | "v3_like"
    factory: str
    pool: str
    token0: str
    token1: str
    fee: int | None


def decode_pool_created(log: EvmLog) -> PoolCreated | None:
    """The pool a creation log announces, or ``None`` if the log does not fit either layout."""
    if log.removed or not log.topics:
        return None
    body = log.data[2:]
    if log.topics[0] == PAIR_CREATED_TOPIC and len(log.topics) == 3 and len(body) >= 2 * _WORD:
        pool = topic_address("0x" + body[:_WORD])
        protocol, fee = "v2_like", None
    elif log.topics[0] == POOL_CREATED_TOPIC and len(log.topics) == 4 and len(body) >= 2 * _WORD:
        pool = topic_address("0x" + body[_WORD : 2 * _WORD])
        protocol, fee = "v3_like", int(log.topics[3], 16)
    else:
        return None
    token0, token1 = topic_address(log.topics[1]), topic_address(log.topics[2])
    if pool is None or token0 is None or token1 is None or (fee is not None and fee >= 2**24):
        return None
    return PoolCreated(protocol, log.address, pool, token0, token1, fee)
