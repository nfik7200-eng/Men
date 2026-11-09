"""Shared builders for the security tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from sqlalchemy import Engine

from bip.api.app import create_app
from bip.config import Settings, load_settings
from bip.db.engine import create_db_engine
from bip.db.repository import Repository
from bip.security.auth import AuthService
from tests.api.conftest import roomy_guard
from tests.stage2.fakes import ETH_HOST, SOL_HOST, FakeEthNode, FakeSolNode, FakeTransport

PASSWORD = "correct-horse-battery"
API = "/api/v1"


class Rig:
    def __init__(
        self,
        tmp_path: Path,
        *,
        mode: str = "required",
        settings: dict[str, Any] | None = None,
        **auth: Any,
    ) -> None:
        self.settings: Settings = load_settings(
            data_root=tmp_path / "data",
            env="test",
            auth={"mode": mode, "cookie_secure": False, **auth},
            **(settings or {}),
        )
        self.engine: Engine = create_db_engine(self.settings)
        self.repo = Repository(self.engine)
        self.auth = AuthService(self.repo, self.settings.auth)
        self.eth = FakeEthNode()
        self.sol = FakeSolNode()
        self.app: FastAPI = create_app(
            self.settings,
            engine=self.engine,
            transport=FakeTransport({ETH_HOST: self.eth.handle, SOL_HOST: self.sol.handle}),
            disk_guard=roomy_guard(self.settings.resolved_data_root),
            environ={},
            rpc_sleep=lambda _s: None,
        )

    def user(self, name: str = "alice", role: str = "admin", password: str = PASSWORD) -> None:
        self.auth.create_user(name, password, role)

    def dispose(self) -> None:
        self.engine.dispose()
