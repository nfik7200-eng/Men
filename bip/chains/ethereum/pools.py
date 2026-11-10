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
SYNC_TOPIC = "0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1"
"""``Sync(uint112 reserve0, uint112 reserve1)``, emitted by v2-style pairs after every change."""

_WORD = 64  # hex characters per 32-byte word

VERIFIED_AT = "2026-11-10"


@dataclass(frozen=True, slots=True)
class KnownFactory:
    address: str
    name: str
    source_url: str
    verified_at: str = VERIFIED_AT


KNOWN_FACTORIES: tuple[KnownFactory, ...] = (
    KnownFactory(
        "0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f",
        "Uniswap v2 factory",
        "https://docs.uniswap.org/contracts/v2/reference/smart-contracts/v2-deployments",
    ),
    KnownFactory(
        "0x1f98431c8ad98523631ae4a59f267346ea31f984",
        "Uniswap v3 factory",
        "https://raw.githubusercontent.com/Uniswap/v3-periphery/main/deploys.md",
    ),
)
"""Factories whose address came from the protocol's own documentation. Any contract can emit the
creation events, so only pools from a trusted factory feed token transfers and reserves; other
factories can be added with ``collector.extra_factories`` after the operator verifies them."""

KNOWN_FACTORY_ADDRESSES = frozenset(f.address for f in KNOWN_FACTORIES)

BASE_ASSETS: tuple[tuple[str, str, str], ...] = (
    (
        "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
        "WETH9",
        "https://docs.uniswap.org/contracts/v3/reference/deployments/ethereum-deployments",
    ),
)
"""Tokens that pair with almost everything. A pool "TOKEN/WETH" says something about TOKEN, not
about WETH: storing every WETH transfer on the chain would use the whole per-run cap (seen live)
and crowd out the new token. ``(address, name, source_url)``, verified 2026-11-10."""

BASE_ASSET_ADDRESSES = frozenset(a for a, _, _ in BASE_ASSETS)


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


def decode_sync(log: EvmLog) -> tuple[int, int] | None:
    """The two reserves a v2-style ``Sync`` log states, or ``None`` if the shape is wrong."""
    body = log.data[2:]
    if log.removed or log.topics != (SYNC_TOPIC,) or len(body) != 2 * _WORD:
        return None
    reserve0, reserve1 = int(body[:_WORD], 16), int(body[_WORD:], 16)
    if reserve0 >= 2**112 or reserve1 >= 2**112:
        return None  # uint112: anything larger is not a Sync event
    return reserve0, reserve1
