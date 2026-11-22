"""Keccak-256 in pure Python (stdlib only).

Ethereum uses the original Keccak submission, not the FIPS-202 SHA3-256 in ``hashlib``. Both use
the Keccak-f[1600] permutation with a 136-byte rate; they differ only in the domain padding
byte (Keccak ``0x01``, SHA3 ``0x06``). ``hashlib.sha3_256`` therefore cannot produce Keccak-256
digests, but it can check this permutation: ``_sponge(data, 0x06)`` must equal it.

Speed is adequate for short inputs such as EIP-55 address checksums, not for bulk hashing.
"""

from __future__ import annotations

_RATE = 136
_MASK = (1 << 64) - 1

_ROUND_CONSTANTS = (
    0x0000000000000001,
    0x0000000000008082,
    0x800000000000808A,
    0x8000000080008000,
    0x000000000000808B,
    0x0000000080000001,
    0x8000000080008081,
    0x8000000000008009,
    0x000000000000008A,
    0x0000000000000088,
    0x0000000080008009,
    0x000000008000000A,
    0x000000008000808B,
    0x800000000000008B,
    0x8000000000008089,
    0x8000000000008003,
    0x8000000000008002,
    0x8000000000000080,
    0x000000000000800A,
    0x800000008000000A,
    0x8000000080008081,
    0x8000000000008080,
    0x0000000080000001,
    0x8000000080008008,
)

# Rotation offsets r[x][y], indexed as lane x + 5 * y.
_ROTATIONS = (
    0, 1, 62, 28, 27,
    36, 44, 6, 55, 20,
    3, 10, 43, 25, 39,
    41, 45, 15, 21, 8,
    18, 2, 61, 56, 14,
)  # fmt: skip


def _rotl(value: int, shift: int) -> int:
    return ((value << shift) | (value >> (64 - shift))) & _MASK if shift else value


def _keccak_f(state: list[int]) -> None:
    for rc in _ROUND_CONSTANTS:
        # theta
        c = [
            state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20]
            for x in range(5)
        ]
        d = [c[(x - 1) % 5] ^ _rotl(c[(x + 1) % 5], 1) for x in range(5)]
        for i in range(25):
            state[i] ^= d[i % 5]
        # rho and pi: B[y, 2x + 3y] = rot(A[x, y], r[x, y])
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                i = x + 5 * y
                b[y + 5 * ((2 * x + 3 * y) % 5)] = _rotl(state[i], _ROTATIONS[i])
        # chi
        for y in range(0, 25, 5):
            row = b[y : y + 5]
            for x in range(5):
                state[y + x] = row[x] ^ ((~row[(x + 1) % 5]) & row[(x + 2) % 5])
        # iota
        state[0] ^= rc


def _sponge(data: bytes, pad: int) -> bytes:
    """Absorb ``data`` with domain byte ``pad`` and squeeze 32 bytes."""
    padded = bytearray(data)
    padded.append(pad)
    padded.extend(b"\x00" * (-len(padded) % _RATE))
    padded[-1] |= 0x80
    state = [0] * 25
    for offset in range(0, len(padded), _RATE):
        block = padded[offset : offset + _RATE]
        for i in range(_RATE // 8):
            state[i] ^= int.from_bytes(block[8 * i : 8 * i + 8], "little")
        _keccak_f(state)
    return b"".join(lane.to_bytes(8, "little") for lane in state[:4])


def keccak256(data: bytes) -> bytes:
    """Return the 32-byte Keccak-256 digest of ``data`` (Ethereum's hash)."""
    return _sponge(data, 0x01)
