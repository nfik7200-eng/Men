"""Availability of the optional lake runtime (pyarrow, duckdb).

They are the default ``lake`` dependency group; ``uv sync --no-group lake`` omits them (for
example on Android/Termux). Nothing imports them at API startup, and the lake modules that need
them (:mod:`bip.storage.lake.parquet`, :mod:`bip.storage.lake.reader`) are imported on demand.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass

from bip.core.exceptions import BIPError

REQUIRED = ("pyarrow", "duckdb")


class LakeUnavailableError(BIPError):
    """pyarrow or duckdb is not installed in this environment."""


@dataclass(frozen=True, slots=True)
class LakeRuntime:
    available: bool
    missing: tuple[str, ...]


def lake_runtime() -> LakeRuntime:
    missing: list[str] = []
    for name in REQUIRED:
        try:
            found = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            missing.append(name)
    return LakeRuntime(not missing, tuple(missing))


def require_lake() -> None:
    rt = lake_runtime()
    if not rt.available:
        raise LakeUnavailableError(
            f"the lake runtime is not installed (missing: {', '.join(rt.missing)}); "
            "install it with `uv sync` (the default `lake` group)"
        )
