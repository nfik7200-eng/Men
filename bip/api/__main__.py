"""Run the API: ``uv run python -m bip.api``.

Listens on ``BIP_API__HOST``:``BIP_API__PORT``, or on the Unix socket ``BIP_API__UNIX_SOCKET`` when
that is set (the recommended way to sit behind a reverse proxy on a shared host). Settings saved on
the Setup page are merged in at start, and ``POST /setup/restart`` restarts the server in place.
"""

from __future__ import annotations

import os
import socket
import stat
from pathlib import Path

import uvicorn
from starlette.types import ASGIApp

from bip.api.app import create_app
from bip.api.lifecycle import RestartController
from bip.config.settings import Settings
from bip.config.store import load_effective_settings
from bip.core.logging import configure_logging, get_logger

log = get_logger("bip.api")

SOCKET_UMASK = 0o117
"""The socket file is created 0660: owner and group (the proxy's group), nobody else."""


def uvicorn_config(settings: Settings, app: ASGIApp) -> uvicorn.Config:
    """The server configuration for ``settings`` (a pure function, so it can be tested)."""
    if settings.api.unix_socket is not None:
        # Only the proxy can open the socket, so uvicorn must not rewrite the client address from
        # a header (BIP's own proxy-header setting decides whether to trust X-Forwarded-For).
        return uvicorn.Config(
            app,
            uds=str(settings.api.unix_socket),
            proxy_headers=False,
            log_config=None,
            access_log=False,
            server_header=False,
        )
    return uvicorn.Config(
        app,
        host=settings.api.host,
        port=settings.api.port,
        log_config=None,
        access_log=False,
        server_header=False,
    )


def bind_unix_socket(path: Path) -> socket.socket:
    """Bind and listen on ``path`` with mode 0660, clearing a stale socket left by a crashed run.

    uvicorn would bind the socket itself but then ``chmod`` it to 0666, so the socket is made here
    under a restrictive umask and handed over already bound. A regular file at ``path`` is never
    removed.
    """
    if path.exists() or path.is_symlink():
        if not stat.S_ISSOCK(path.lstat().st_mode):
            raise SystemExit(f"{path} exists and is not a socket; refusing to remove it")
        path.unlink()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    previous = os.umask(SOCKET_UMASK)
    try:
        sock.bind(str(path))
    finally:
        os.umask(previous)
    sock.listen(2048)
    return sock


def serve_once(controller: RestartController) -> bool:
    """Load the settings (environment plus Setup-page overrides), run until stopped, and say
    whether a restart was requested from the Setup page."""
    settings, report = load_effective_settings()
    configure_logging(settings.log_level, settings.log_format)
    if report.error:
        log.warning("setup-page settings were not applied: %s", report.error)
    app = create_app(settings, restart=controller, setup_report=report)
    server = uvicorn.Server(uvicorn_config(settings, app))
    controller.attach(server)
    if settings.api.unix_socket is None:
        server.run()
    else:
        server.run(sockets=[bind_unix_socket(settings.api.unix_socket)])
    return controller.consume()


def main() -> None:
    controller = RestartController()
    while serve_once(controller):
        log.info("restarting with the saved setup")


if __name__ == "__main__":
    main()
