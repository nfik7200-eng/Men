"""One-time setup code for creating the first administrator from the sign-in page.

When sign-in is required and no user exists, the API writes a random code to
``<data_root>/config/first-admin-code`` (mode 0600) and logs where it is. Claiming the first
administrator needs that code, so only someone who can read the server's files can do it, never
just whoever reaches the page first. The code is single-use, and it is replaced after
``MAX_FAILURES`` wrong guesses.
"""

from __future__ import annotations

import hmac
import os
import secrets
import threading
from pathlib import Path

CODE_FILE = "first-admin-code"
MAX_FAILURES = 5


class FirstAdminCode:
    def __init__(self, directory: Path) -> None:
        self.path = directory / CODE_FILE
        self._failures = 0
        self._lock = threading.Lock()

    def ensure(self) -> Path:
        """Write a code unless a readable one is already there; return the file's path."""
        with self._lock:
            if not self._read():
                self._write()
            return self.path

    def _read(self) -> str:
        if self.path.is_symlink() or not self.path.is_file():
            return ""
        try:
            return self.path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        if self.path.exists() or self.path.is_symlink():
            self.path.unlink()
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(secrets.token_urlsafe(24) + "\n")
        self._failures = 0

    def check(self, candidate: str) -> bool:
        """Constant-time comparison; a wrong guess counts toward rotating the code."""
        with self._lock:
            expected = self._read()
            ok = bool(expected) and hmac.compare_digest(
                candidate.strip().encode(), expected.encode()
            )
            if not ok:
                self._failures += 1
                if self._failures >= MAX_FAILURES:
                    self._write()
            return ok

    def consume(self) -> None:
        with self._lock:
            if self.path.is_file() and not self.path.is_symlink():
                self.path.unlink()
