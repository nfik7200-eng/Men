"""SQLite online backups with integrity verification and retention.

``sqlite3``'s backup API copies a consistent snapshot while the application keeps running (the
database is in WAL mode), so no downtime and no file-copy race. A backup is written to a temporary
name, checked with ``PRAGMA integrity_check`` and only then renamed into place, so a failed or
interrupted backup never looks valid. Files are created owner-only (0600) in an owner-only
directory.

Restore is deliberately manual (stop BIP, copy the chosen file over the database, start BIP); see
``docs/OPERATIONS.md``. A tool that silently replaces the live database is more dangerous than the
failure it would repair.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from bip.config.settings import Settings
from bip.core.exceptions import BIPError
from bip.db.engine import database_path
from bip.db.types import utcnow
from bip.storage.paths import Layer, StorageRoot

_NAME = re.compile(r"^bip-(\d{8}T\d{6}Z)(?:-(\d+))?\.sqlite3$")
_RESERVE_BYTES = 64 * 1024 * 1024
"""Free space that must remain after the copy, on top of the copy itself."""


def _sequence(name: str) -> int:
    """The ``-N`` suffix of a same-second backup (0 for the first), so order is by creation."""
    match = _NAME.fullmatch(name)
    return int(match.group(2) or 0) if match else 0


def _disk_free(path: str) -> int:
    return shutil.disk_usage(path).free


class BackupError(BIPError):
    """A backup could not be made, verified or removed."""


@dataclass(frozen=True, slots=True)
class BackupInfo:
    name: str
    size_bytes: int
    created_at: datetime

    def public(self) -> dict[str, object]:
        return {
            "name": self.name,
            "size_bytes": self.size_bytes,
            "created_at": self.created_at.isoformat(),
        }


class BackupService:
    def __init__(
        self,
        settings: Settings,
        *,
        clock: Callable[[], datetime] = utcnow,
        free_bytes: Callable[[str], int] | None = None,
    ) -> None:
        self._settings = settings
        self._root = StorageRoot(settings.resolved_data_root)
        self._clock = clock
        self._free: Callable[[str], int] = free_bytes or _disk_free

    @property
    def directory(self) -> Path:
        return self._root.layer_path(Layer.OPERATIONAL, "backups")

    def create(self) -> BackupInfo:
        source = database_path(self._settings)
        if not source.exists():
            raise BackupError("there is no database to back up")
        directory = self.directory
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        wal = source.with_name(source.name + "-wal")
        need = source.stat().st_size + (wal.stat().st_size if wal.exists() else 0) + _RESERVE_BYTES
        free = self._free(str(directory))
        if free < need:
            raise BackupError(
                f"not enough free disk space for a backup: {free} bytes free, {need} needed"
            )
        stamp = self._clock().strftime("%Y%m%dT%H%M%SZ")
        final = directory / f"bip-{stamp}.sqlite3"
        counter = 0
        while final.exists():
            counter += 1
            final = directory / f"bip-{stamp}-{counter}.sqlite3"
        partial = directory / f".{final.name}.partial"
        try:
            with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(partial)) as dst:
                src.backup(dst)
            os.chmod(partial, 0o600)
            problem = self._integrity(partial)
            if problem:
                raise BackupError(f"the backup failed its integrity check: {problem}")
            os.replace(partial, final)
        except sqlite3.Error as exc:
            raise BackupError(f"the backup could not be written: {exc}") from None
        finally:
            with contextlib.suppress(OSError):
                partial.unlink()
        return self._info(final)

    @staticmethod
    def _integrity(path: Path) -> str | None:
        try:
            with closing(sqlite3.connect(path)) as conn:
                rows = conn.execute("PRAGMA integrity_check").fetchall()
        except sqlite3.Error as exc:
            return str(exc)
        return None if rows == [("ok",)] else "; ".join(str(r[0]) for r in rows[:3])

    def _info(self, path: Path) -> BackupInfo:
        match = _NAME.fullmatch(path.name)
        assert match is not None  # noqa: S101 - callers pass only names we generated
        created = datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(
            tzinfo=self._clock().tzinfo
        )
        return BackupInfo(path.name, path.stat().st_size, created)

    def list(self) -> list[BackupInfo]:
        directory = self.directory
        if not directory.exists():
            return []
        found = [
            self._info(p) for p in directory.iterdir() if p.is_file() and _NAME.fullmatch(p.name)
        ]
        return sorted(found, key=lambda b: (b.created_at, _sequence(b.name)), reverse=True)

    def verify(self, name: str) -> bool:
        """True if the named backup opens and passes ``PRAGMA integrity_check``."""
        if not _NAME.fullmatch(name):
            raise BackupError("not a backup file name")
        path = self.directory / name
        if not path.is_file():
            raise BackupError("no such backup")
        return self._integrity(path) is None

    def prune(self, keep: int) -> list[str]:
        """Delete all but the newest ``keep`` backups (never fewer than one)."""
        keep = max(1, keep)
        removed: list[str] = []
        for info in self.list()[keep:]:
            try:
                (self.directory / info.name).unlink()
            except OSError as exc:
                raise BackupError(f"could not remove {info.name}: {exc.strerror}") from None
            removed.append(info.name)
        return removed
