"""The signal page mirrors the review rules for its form; the server enforces them. This test
fails when the two copies drift apart."""

from __future__ import annotations

import re
from pathlib import Path

from bip.discovery.review import FINISHED, STATE_REASONS

SOURCE = Path(__file__).resolve().parents[2] / "frontend/src/pages/signals/SignalDetail.tsx"


def _block(text: str, name: str) -> str:
    match = re.search(rf"const {name}[^=]*=\s*(\{{.*?\n\}}|\[.*?\]);", text, re.S)
    assert match, f"{name} not found in {SOURCE.name}"
    return match.group(1)


def test_the_frontend_state_reasons_match_the_server() -> None:
    text = SOURCE.read_text()
    block = _block(text, "STATE_REASONS")
    mirrored = {
        state: re.findall(r'"([a-z_]+)"', reasons)
        for state, reasons in re.findall(r"(\w+):\s*\[([^\]]*)\]", block)
    }
    assert mirrored == {k: list(v) for k, v in STATE_REASONS.items()}


def test_the_frontend_finished_states_match_the_server() -> None:
    text = SOURCE.read_text()
    assert re.findall(r'"([a-z_]+)"', _block(text, "FINISHED")) == list(FINISHED)
