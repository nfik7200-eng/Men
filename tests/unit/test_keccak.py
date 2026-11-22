from __future__ import annotations

import hashlib

import pytest

from bip.chains.ethereum.parse import TRANSFER_TOPIC
from bip.core.keccak import _sponge, keccak256  # pyright: ignore[reportPrivateUsage]


def test_empty_input() -> None:
    assert (
        keccak256(b"").hex() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    )


def test_matches_the_transfer_topic_constant() -> None:
    assert "0x" + keccak256(b"Transfer(address,address,uint256)").hex() == TRANSFER_TOPIC


def test_known_sentence() -> None:
    assert keccak256(b"The quick brown fox jumps over the lazy dog").hex() == (
        "4d741b6f1eb29cb2a9b9911c82f56fa8d73b04959d3d9d222895df6c0b28aa15"
    )


@pytest.mark.parametrize("n", [0, 1, 135, 136, 137, 271, 272, 1000])
def test_permutation_and_absorption_match_stdlib_sha3(n: int) -> None:
    data = bytes(range(256)) * 4
    assert _sponge(data[:n], 0x06) == hashlib.sha3_256(data[:n]).digest()


def test_differs_from_sha3() -> None:
    assert keccak256(b"") != hashlib.sha3_256(b"").digest()
