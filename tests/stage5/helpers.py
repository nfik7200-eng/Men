"""Test helpers for Stage 5 that do not import the optional ML runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bip.config import load_settings
from tests.stage3.test_intel_api import Env

API = "/api/v1"
J = dict[str, Any]


def env_with(tmp_path: Path, **ml: Any) -> Env:
    e = Env(tmp_path, second=False)
    e.settings = load_settings(data_root=e.settings.resolved_data_root, env="test", ml=ml)
    from bip.api.app import create_app
    from tests.api.conftest import roomy_guard

    e.app = create_app(
        e.settings,
        engine=e.engine,
        transport=e.transport,
        disk_guard=roomy_guard(e.settings.resolved_data_root),
        environ={},
        rpc_sleep=lambda _s: None,
    )
    return e
