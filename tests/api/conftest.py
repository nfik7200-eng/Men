"""Fixtures for API and database tests: temporary data root, real SQLite, injected disk guard."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from bip.api.app import create_app
from bip.config import Settings, load_settings
from bip.db.engine import create_db_engine
from bip.db.repository import Repository
from bip.storage.disk_guard import DiskGuard, DiskUsage

GIB = 1024**3


def roomy_guard(root: Path, *, free: int = 50 * GIB) -> DiskGuard:
    return DiskGuard(
        root,
        max_data_bytes=GIB,
        min_free_bytes=GIB,
        min_free_ratio=0.05,
        disk_usage=lambda _p: DiskUsage(total=100 * GIB, used=100 * GIB - free, free=free),
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return load_settings(data_root=tmp_path / "data", env="test")


@pytest.fixture
def engine(settings: Settings) -> Iterator[Engine]:
    eng = create_db_engine(settings)
    yield eng
    eng.dispose()


@pytest.fixture
def repo(engine: Engine) -> Repository:
    return Repository(engine)


@pytest.fixture
def client(settings: Settings, engine: Engine) -> Iterator[TestClient]:
    app = create_app(settings, engine=engine, disk_guard=roomy_guard(settings.resolved_data_root))
    with TestClient(app) as c:
        yield c
