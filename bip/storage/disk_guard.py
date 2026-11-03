"""Disk-usage protection.

:class:`DiskGuard` answers "may BIP write N more bytes?" by checking three limits:

1. BIP's own usage under the storage root plus the planned write stays within ``max_data_bytes``;
2. free space after the write stays at or above ``min_free_bytes``;
3. the free fraction after the write stays at or above ``min_free_ratio``.

It fails safe: if usage cannot be measured, the write is refused rather than assumed to fit.
It never deletes anything. Future writers call :meth:`DiskGuard.require` before each batch.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from bip.config.settings import Settings
from bip.core.exceptions import StorageLimitError
from bip.storage.paths import StorageRoot


class DiskUsage(NamedTuple):
    total: int
    used: int
    free: int


DiskUsageFn = Callable[[Path], DiskUsage]
MeasureFn = Callable[[Path], int]


def _system_disk_usage(path: Path) -> DiskUsage:
    usage = shutil.disk_usage(path)
    return DiskUsage(usage.total, usage.used, usage.free)


def directory_size(path: Path) -> int:
    """Total size of regular files under ``path`` in bytes, without following symlinks.

    Returns 0 if ``path`` does not exist. Raises :class:`OSError` if any part cannot be read.
    """
    if not path.exists():
        return 0
    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
    return total


def _nearest_existing(path: Path) -> Path:
    current = path
    while not current.exists():
        if current.parent == current:
            break
        current = current.parent
    return current


@dataclass(frozen=True, slots=True)
class DiskStatus:
    total_bytes: int
    free_bytes: int
    bip_used_bytes: int
    max_data_bytes: int
    min_free_bytes: int
    min_free_ratio: float

    @property
    def free_ratio(self) -> float:
        return self.free_bytes / self.total_bytes if self.total_bytes else 0.0


@dataclass(frozen=True, slots=True)
class DiskCheck:
    allowed: bool
    planned_bytes: int
    reasons: tuple[str, ...]
    status: DiskStatus


class DiskGuard:
    def __init__(
        self,
        root: StorageRoot | Path | str,
        *,
        max_data_bytes: int,
        min_free_bytes: int,
        min_free_ratio: float,
        disk_usage: DiskUsageFn = _system_disk_usage,
        measure: MeasureFn = directory_size,
    ) -> None:
        if max_data_bytes <= 0:
            raise ValueError("max_data_bytes must be positive")
        if min_free_bytes < 0 or not 0.0 <= min_free_ratio < 1.0:
            raise ValueError("min_free_bytes must be >= 0 and 0 <= min_free_ratio < 1")
        self._root = root if isinstance(root, StorageRoot) else StorageRoot(root)
        self._max = max_data_bytes
        self._min_free = min_free_bytes
        self._min_ratio = min_free_ratio
        self._disk_usage = disk_usage
        self._measure = measure

    def status(self) -> DiskStatus:
        """Measure the data filesystem and BIP's usage. Raises StorageLimitError if unmeasurable."""
        probe = _nearest_existing(self._root.root)
        try:
            usage = self._disk_usage(probe)
            used = self._measure(self._root.root)
        except OSError as exc:
            raise StorageLimitError(
                f"cannot measure disk usage for {self._root.root}: {exc.strerror or exc}; "
                "refusing writes until it can be measured"
            ) from None
        return DiskStatus(
            total_bytes=usage.total,
            free_bytes=usage.free,
            bip_used_bytes=used,
            max_data_bytes=self._max,
            min_free_bytes=self._min_free,
            min_free_ratio=self._min_ratio,
        )

    def check(self, planned_bytes: int = 0) -> DiskCheck:
        if planned_bytes < 0:
            raise ValueError("planned_bytes must be >= 0")
        st = self.status()
        reasons: list[str] = []
        if st.bip_used_bytes + planned_bytes > st.max_data_bytes:
            reasons.append(
                f"BIP data would reach {st.bip_used_bytes + planned_bytes} bytes, "
                f"above the {st.max_data_bytes}-byte limit"
            )
        free_after = st.free_bytes - planned_bytes
        if free_after < st.min_free_bytes:
            reasons.append(
                f"free space would drop to {max(free_after, 0)} bytes, "
                f"below the {st.min_free_bytes}-byte floor"
            )
        if st.total_bytes <= 0 or free_after / st.total_bytes < st.min_free_ratio:
            reasons.append(f"free space would fall below {st.min_free_ratio:.1%} of the disk")
        return DiskCheck(not reasons, planned_bytes, tuple(reasons), st)

    def require(self, planned_bytes: int = 0) -> DiskStatus:
        """Return the status if the write is allowed, else raise :class:`StorageLimitError`."""
        result = self.check(planned_bytes)
        if not result.allowed:
            raise StorageLimitError("write refused: " + "; ".join(result.reasons))
        return result.status


def guard_from_settings(settings: Settings) -> DiskGuard:
    """Build a guard from application settings."""
    return DiskGuard(
        settings.resolved_data_root,
        max_data_bytes=int(settings.max_data_bytes),
        min_free_bytes=int(settings.min_free_bytes),
        min_free_ratio=settings.min_free_ratio,
    )
