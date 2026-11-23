"""In-process restart for settings saved from the Setup page (ADR-056).

``python -m bip.api`` runs the server in a loop. ``POST /setup/restart`` calls
:meth:`RestartController.request`, which asks the running uvicorn server to exit after the
response is sent; the loop then re-reads the settings and starts a fresh app. Nothing outside the
process is involved, so it works the same with or without systemd.
"""

from __future__ import annotations

import threading
from typing import Protocol


class _Server(Protocol):
    should_exit: bool


class RestartController:
    def __init__(self, *, delay_s: float = 0.5) -> None:
        self._delay = delay_s
        self._server: _Server | None = None
        self._requested = threading.Event()
        self._lock = threading.Lock()

    def attach(self, server: _Server) -> None:
        with self._lock:
            self._server = server
            self._requested.clear()

    @property
    def available(self) -> bool:
        return self._server is not None

    def request(self) -> bool:
        """Ask the server to exit and restart. False when no server loop is attached."""
        with self._lock:
            server = self._server
            if server is None:
                return False
            self._requested.set()

        def stop() -> None:
            server.should_exit = True

        if self._delay <= 0:
            stop()
        else:  # let the 202 response reach the browser first
            threading.Timer(self._delay, stop).start()
        return True

    def wait_requested(self, timeout: float) -> bool:
        return self._requested.wait(timeout)

    def consume(self) -> bool:
        """True once after a requested restart (the server loop then starts again)."""
        with self._lock:
            if self._requested.is_set():
                self._requested.clear()
                return True
            return False
