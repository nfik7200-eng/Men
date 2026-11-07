"""Base class for read-only chain adapters built on the router."""

from __future__ import annotations

from typing import Protocol

from bip.chains.common import SourceInfo
from bip.db.types import utcnow
from bip.router.router import CallBudget, ProviderRouter, RpcOutcome


class RawSink(Protocol):
    def store_raw(self, chain: str, provider_id: str, method: str, body: bytes) -> str | None:
        """Persist a raw response; return its SHA-256 (or None if not stored)."""
        ...


class ChainAdapter:
    chain: str = ""

    def __init__(
        self,
        router: ProviderRouter,
        *,
        raw_sink: RawSink | None = None,
        budget: CallBudget | None = None,
        provider_id: str | None = None,
    ) -> None:
        self._router = router
        self._sink = raw_sink
        self._budget = budget
        self._provider_id = provider_id
        self.calls = 0
        self.providers_used: set[str] = set()

    def call(
        self, method: str, params: list[object] | dict[str, object]
    ) -> tuple[object, SourceInfo]:
        outcome: RpcOutcome = self._router.call(
            self.chain, method, params, budget=self._budget, provider_id=self._provider_id
        )
        self.calls += 1
        self.providers_used.add(outcome.provider_id)
        sha = (
            self._sink.store_raw(self.chain, outcome.provider_id, method, outcome.raw)
            if self._sink
            else None
        )
        return outcome.result, SourceInfo(outcome.provider_id, method, utcnow(), sha)
