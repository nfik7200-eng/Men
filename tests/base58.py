"""Base58 encoding for test fixtures (no pytest import, so offline fixture servers can use it)."""


def b58encode(data: bytes) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    n = int.from_bytes(data, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = alphabet[r] + out
    pad = len(data) - len(data.lstrip(b"\x00"))
    return "1" * pad + out
