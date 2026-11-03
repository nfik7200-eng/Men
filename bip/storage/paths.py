"""Containment of all BIP file paths under one configurable storage root.

Every path BIP writes must come from :meth:`StorageRoot.path`, which rejects absolute paths,
``..`` traversal, NUL bytes and symlinks that resolve outside the root. Nothing here deletes
files. :meth:`StorageRoot.create_exclusive` refuses to overwrite: it opens with ``O_EXCL``, so
an existing dataset file can never be truncated, even under a race.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path, PurePath
from typing import BinaryIO

from bip.core.exceptions import StoragePathError


class Layer(StrEnum):
    """Top-level directories under the storage root (``docs/DATA_ARCHITECTURE.md`` §4.1)."""

    BRONZE = "bronze"
    SILVER = "silver"
    GOLD = "gold"
    INTELLIGENCE = "intelligence"
    MANIFESTS = "manifests"
    TMP = "tmp"


class StorageRoot:
    """A resolved directory that all BIP data paths must stay inside."""

    __slots__ = ("_root",)

    def __init__(self, root: Path | str) -> None:
        raw = Path(root)
        if "\x00" in str(raw):
            raise StoragePathError("storage root contains a NUL byte")
        self._root = raw.expanduser().resolve(strict=False)

    @property
    def root(self) -> Path:
        return self._root

    def __repr__(self) -> str:
        return f"StorageRoot({str(self._root)!r})"

    def exists(self) -> bool:
        return self._root.is_dir()

    def require_exists(self) -> None:
        """Raise :class:`StoragePathError` if the root directory does not exist."""
        if not self._root.exists():
            raise StoragePathError(
                f"storage root {self._root} does not exist; create it explicitly before ingesting"
            )
        if not self._root.is_dir():
            raise StoragePathError(f"storage root {self._root} is not a directory")

    def create(self) -> Path:
        """Create the root directory (and parents). Explicit only; never called implicitly."""
        self._root.mkdir(parents=True, exist_ok=True)
        return self._root

    def path(self, *parts: str | PurePath) -> Path:
        """Join ``parts`` under the root and verify the result cannot escape it."""
        if not parts:
            raise StoragePathError("at least one path component is required")
        for part in parts:
            text = str(part)
            if not text or "\x00" in text:
                raise StoragePathError("path components must be non-empty and contain no NUL")
            pure = PurePath(text)
            if pure.is_absolute() or pure.drive or text.startswith(("/", "\\")):
                raise StoragePathError(f"absolute path component not allowed: {text!r}")
            if ".." in pure.parts:
                raise StoragePathError(f"'..' is not allowed in storage paths: {text!r}")
        candidate = self._root.joinpath(*parts).resolve(strict=False)
        if not candidate.is_relative_to(self._root):
            raise StoragePathError("path resolves outside the storage root (symlink escape?)")
        if candidate == self._root:
            raise StoragePathError("path must name something inside the storage root")
        return candidate

    def layer_path(self, layer: Layer, *parts: str | PurePath) -> Path:
        return self.path(layer.value, *parts)

    def create_exclusive(self, *parts: str | PurePath) -> BinaryIO:
        """Open a **new** file for binary writing; fail if anything already exists there.

        The root must already exist; parent directories inside the root are created.
        """
        self.require_exists()
        target = self.path(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Re-check after mkdir: a parent could be a symlink created concurrently.
        if not target.parent.resolve().is_relative_to(self._root):
            raise StoragePathError("parent directory resolves outside the storage root")
        try:
            return target.open("xb")
        except FileExistsError:
            raise StoragePathError(f"refusing to overwrite existing file {target}") from None
