"""Deterministic fake LLM providers for research tests (no network, no model)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from bip.research.llm import LLMError, LLMStatus


class FakeLLM:
    """Returns a scripted JSON answer built from the claims it receives; records every call."""

    name = "fake"

    def __init__(
        self,
        respond: Callable[[dict[str, Any]], str] | None = None,
        available: bool = True,
        fail: bool = False,
    ) -> None:
        self.model: str | None = "fake-1"
        self.calls: list[dict[str, Any]] = []
        self._respond = respond
        self._available = available
        self._fail = fail

    def status(self) -> LLMStatus:
        return LLMStatus(
            "fake", self.model, self._available, "ok" if self._available else "fake model offline"
        )

    def generate(self, system: str, user: str, *, timeout_s: float, max_bytes: int) -> str:
        bundle = json.loads(user)
        self.calls.append(
            {"system": system, "bundle": bundle, "timeout_s": timeout_s, "max_bytes": max_bytes}
        )
        if self._fail:
            raise LLMError("connection failed (fake)")
        if self._respond is not None:
            return self._respond(bundle)
        ids = [c["id"] for c in bundle["claims"]]
        return json.dumps(
            {
                "executive_summary": "Observed activity is limited; classifications are "
                "inferences.",
                "interpretations": [
                    {
                        "statement": "The wallet shows limited observed activity.",
                        "claim_refs": ids[:1],
                    }
                ],
                "hypotheses": [
                    {"statement": "It may be a lightly used account.", "claim_refs": ids[:2]}
                ],
                "next_steps": ["Retrieve more history."],
            }
        )
