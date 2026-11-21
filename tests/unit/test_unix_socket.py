"""Serving the API on a Unix socket: shape, a real server on a real socket, and the guards."""

from __future__ import annotations

import http.client
import json
import socket
import stat
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn

from bip.api.__main__ import bind_unix_socket, uvicorn_config
from bip.api.app import create_app, require_safe_exposure
from bip.config import load_settings
from bip.core.exceptions import ConfigurationError
from bip.tools.preflight import run_checks, settings_from
from tests.security.helpers import Rig


def test_the_socket_must_be_an_absolute_path() -> None:
    with pytest.raises(ConfigurationError, match="absolute"):
        load_settings(data_root=Path("/unused"), env="test", api={"unix_socket": "relative.sock"})


def test_the_config_uses_the_socket_and_never_rewrites_the_client_address(tmp_path: Path) -> None:
    sock = tmp_path / "api.sock"
    settings = load_settings(data_root=tmp_path / "d", env="test", api={"unix_socket": str(sock)})
    config = uvicorn_config(settings, lambda *_a: None)  # type: ignore[arg-type,return-value]
    assert config.uds == str(sock)
    assert config.proxy_headers is False
    tcp = uvicorn_config(load_settings(data_root=tmp_path / "d", env="test"), lambda *_a: None)  # type: ignore[arg-type,return-value]
    assert (tcp.host, tcp.port, tcp.uds) == ("127.0.0.1", 8000, None)


class _UnixConnection(http.client.HTTPConnection):
    """An HTTP/1.1 client over a Unix socket, standard library only."""

    def __init__(self, path: str) -> None:
        super().__init__("bip")
        self._path = path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(10)
        self.sock.connect(self._path)


@pytest.fixture
def served(tmp_path: Path) -> Iterator[Path]:
    """A real server on a real socket (a short path: socket paths are limited to ~100 bytes)."""
    short = Path("/tmp") / f"bip-sock-{time.monotonic_ns() % 10**9}"
    short.mkdir()
    sock = short / "api.sock"
    # the socket is configured, as in deployment: only then does the host check step aside
    rig = Rig(tmp_path, mode="disabled", settings={"api": {"unix_socket": sock}})
    app = create_app(
        rig.settings,
        engine=rig.engine,
        transport=None,
        environ={},
        rpc_sleep=lambda _s: None,
    )
    server = uvicorn.Server(
        uvicorn.Config(app, uds=str(sock), proxy_headers=False, log_config=None, lifespan="on")
    )
    listener = bind_unix_socket(sock)
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "the server did not start"
    try:
        yield sock
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        rig.dispose()
        for f in short.iterdir():
            f.unlink()
        short.rmdir()


def test_the_api_answers_over_the_socket_and_the_file_is_group_only(served: Path) -> None:
    # uvicorn would have chmod'ed its own socket to 0666; ours is 0660: the proxy's group only
    assert stat.S_IMODE(served.stat().st_mode) == 0o660
    conn = _UnixConnection(str(served))
    try:
        conn.request("GET", "/api/v1/health")
        response = conn.getresponse()
        body = json.loads(response.read())
    finally:
        conn.close()
    assert response.status == 200
    assert body["status"] == "ok"


def test_a_stale_socket_is_cleared_but_a_regular_file_is_never_deleted(tmp_path: Path) -> None:
    short = Path("/tmp") / f"bip-stale-{time.monotonic_ns() % 10**9}"
    short.mkdir()
    try:
        stale = short / "old.sock"
        s = socket.socket(socket.AF_UNIX)
        s.bind(str(stale))
        s.close()
        assert stale.exists()
        fresh = bind_unix_socket(stale)  # the stale file is replaced, not an "address in use"
        assert stat.S_ISSOCK(stale.stat().st_mode)
        fresh.close()
        precious = short / "notes.txt"
        precious.write_text("keep me")
        with pytest.raises(SystemExit, match="not a socket"):
            bind_unix_socket(precious)
        assert precious.read_text() == "keep me"
    finally:
        for f in short.iterdir():
            f.unlink()
        short.rmdir()


def test_a_socket_listener_needs_no_loopback_address_and_preflight_accepts_it(
    tmp_path: Path,
) -> None:
    values = {
        "BIP_ENV": "production",
        "BIP_DATA_ROOT": str(tmp_path),
        "BIP_LOG_FORMAT": "json",
        "BIP_AUTH__MODE": "required",
        "BIP_AUTH__TRUST_PROXY_HEADERS": "true",
        "BIP_API__UNIX_SOCKET": "/run/bip/api.sock",
        "BIP_API__HOST": "10.0.0.5",  # unused with a socket (a FAIL with proxy trust otherwise)
        "BIP_MIN_FREE_BYTES": "1MiB",
    }
    settings = settings_from(values)
    checks = {c.name: c for c in run_checks(settings)}
    assert checks["bind_address"].level == "PASS"
    assert checks["trust_proxy_headers"].level == "PASS"
    require_safe_exposure(settings)  # no ConfigurationError: nothing listens on a network address
