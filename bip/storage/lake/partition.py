"""Partition addressing for lake files (``docs/DATA_ARCHITECTURE.md`` §4.1).

``<layer>/chain=<chain>/dataset=<dataset>/<key>=<value>/...``. Names and values are restricted
to a safe character set, so a partition can never produce a path outside its directory.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import PurePosixPath

from bip.storage.paths import Layer

DATA_LAYERS = frozenset({Layer.BRONZE, Layer.SILVER, Layer.GOLD})
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_VALUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True, slots=True)
class Partition:
    layer: Layer
    chain: str
    dataset: str
    parts: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.layer not in DATA_LAYERS:
            raise ValueError(f"lake files live in bronze, silver or gold, not {self.layer.value}")
        for label, name in (("chain", self.chain), ("dataset", self.dataset)):
            if not _NAME_RE.match(name):
                raise ValueError(f"{label} must match [a-z][a-z0-9_]*")
        seen: set[str] = set()
        for key, value in self.parts:
            if not _NAME_RE.match(key) or key in {"chain", "dataset"}:
                raise ValueError(f"invalid partition key {key!r}")
            if key in seen:
                raise ValueError(f"duplicate partition key {key!r}")
            seen.add(key)
            if not _VALUE_RE.match(value):
                raise ValueError(f"invalid value for partition key {key!r}")

    @property
    def table(self) -> str:
        return f"{self.chain}.{self.dataset}"

    @property
    def key(self) -> str:
        """``k=v/k=v`` for the extra parts; empty when there are none."""
        return "/".join(f"{k}={v}" for k, v in self.parts)

    def table_dir(self) -> PurePosixPath:
        return PurePosixPath(self.layer.value, f"chain={self.chain}", f"dataset={self.dataset}")

    def relative_dir(self) -> PurePosixPath:
        return self.table_dir().joinpath(*(f"{k}={v}" for k, v in self.parts))


def block_range_part(block: int, width: int = 1000) -> tuple[str, str]:
    """The ``range=`` part holding ``block``: ``021000000-021000999`` for width 1,000."""
    if block < 0:
        raise ValueError("block must be non-negative")
    if width < 1:
        raise ValueError("width must be >= 1")
    start = block - block % width
    return ("range", f"{start:09d}-{start + width - 1:09d}")


def date_part(day: date) -> tuple[str, str]:
    return ("date", day.isoformat())
