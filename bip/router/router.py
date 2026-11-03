"""Quota- and capability-aware JSON-RPC routing.

For each request the router builds the eligible set for ``(chain, method)``:
the provider is enabled, has an adapter for the chain's protocol, lists the method,
has a resolved endpoint, has not failed network-identity verification in this process, and is not
in a rate-limit cooldown. Candidates are ordered healthy → untested → degraded → unavailable
(failed providers are tried last, never first). It then:

- verifies network identity (chain id / genesis hash) before a provider's first use;
- spaces requests per provider by its ``client_rps`` (BIP's own politeness throttle);
- retries transient failures with exponential backoff and full jitter, at most
  ``rpc.max_attempts`` attempts per request in total;
- on HTTP 429 / rate-limit errors, honours ``Retry-After`` (capped) as a provider cooldown and
  fails over to the next eligible provider;
- never retries ``invalid_request`` / ``not_available`` and never sends a method to a provider
  that reported it unsupported;
- records every outcome in :class:`ProviderStateStore`;
- runs requests sequentially (no parallel fan-out).

A :class:`CallBudget` bounds total calls and wall-clock time for one ingestion run.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from bip.chains.identity import EXPECTED_IDENTITY, identity_matches
from bip.config.settings import Settings
from bip.core.exceptions import BIPError
from bip.core.logging import get_logger
from bip.providers.registry import ProviderSpec, Registry
from bip.providers.state import ProviderState, ProviderStateStore
from bip.providers.status import ProviderView, compute_view
from bip.rpc.jsonrpc import RpcError, RpcRequest, classify_http_status, parse_response
from bip.rpc.transport import Transport, TransportError

log = get_logger("bip.router")

IDENTITY_TTL_S = 3600.0
INLINE_RETRY_AFTER_MAX_S = 5.0
_RANK = {"healthy": 0, "untested": 1, "degraded": 2, "unavailable": 3}


class NoEligibleProviderError(BIPError):
    def __init__(self, chain: str, method: str, reasons: Mapping[str, str]) -> None:
        detail = "; ".join(f"{k}: {v}" for k, v in reasons.items()) or "no providers registered"
        super().__init__(f"no eligible provider for {chain} {method} ({detail})")
        self.chain = chain
        self.method = method
        self.reasons = dict(reasons)


class RoutingFailedError(BIPError):
    """Every attempt failed. ``last_error`` explains the final failure."""

    def __init__(self, chain: str, method: str, attempts: list[str]) -> None:
        super().__init__(
            f"{chain} {method} failed after {len(attempts)} attempt(s): " + " | ".join(attempts)
        )
        self.attempts = attempts


class BudgetExhaustedError(BIPError):
    pass


@dataclass
class CallBudget:
    max_calls: int
    deadline: float
    calls: int = 0

    @classmethod
    def create(
        cls, max_calls: int, seconds: float, clock: Callable[[], float] = time.monotonic
    ) -> CallBudget:
        return cls(max_calls=max_calls, deadline=clock() + seconds)

    def take(self, clock: Callable[[], float]) -> None:
        if self.calls >= self.max_calls:
            raise BudgetExhaustedError(f"RPC call budget of {self.max_calls} exhausted")
        if clock() >= self.deadline:
            raise BudgetExhaustedError("run deadline reached")
        self.calls += 1


@dataclass(frozen=True, slots=True)
class RpcOutcome:
    result: object
    provider_id: str
    method: str
    latency_ms: int
    raw: bytes


@dataclass
class _ProviderRuntime:
    next_slot: float = 0.0
    cooldown_until: float = 0.0
    identity_ok_at: float | None = None
    identity_failed: bool = False
    unsupported: set[str] = field(default_factory=set[str])


class ProviderRouter:
    def __init__(
        self,
        registry: Registry,
        settings: Settings,
        transport: Transport,
        state: ProviderStateStore | None = None,
        *,
        environ: Mapping[str, str] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self._registry = registry
        self._cfg = settings.rpc
        self._transport = transport
        self._state = state
        self._environ = environ
        self._clock = clock
        self._sleep = sleep
        self._rng = rng
        self._runtime: dict[str, _ProviderRuntime] = {}
        self._lock = threading.Lock()
        self._next_id = 0

    @property
    def environ(self) -> Mapping[str, str] | None:
        return self._environ

    # ------------------------------------------------------------------ selection

    def _rt(self, provider_id: str) -> _ProviderRuntime:
        return self._runtime.setdefault(provider_id, _ProviderRuntime())

    def _states(self) -> dict[str, ProviderState]:
        return self._state.states() if self._state is not None else {}

    def view(self, spec: ProviderSpec, states: Mapping[str, ProviderState]) -> ProviderView:
        return compute_view(spec, states.get(spec.id), self._environ)

    def eligible(self, chain: str, method: str) -> tuple[list[ProviderView], dict[str, str]]:
        states = self._states()
        now = self._clock()
        usable: list[ProviderView] = []
        reasons: dict[str, str] = {}
        for spec in self._registry.for_chain(chain):
            v = self.view(spec, states)
            rt = self._rt(spec.id)
            if not v.enabled:
                reasons[spec.id] = "disabled"
            elif not spec.has_adapter:
                reasons[spec.id] = "no adapter"
            elif method not in spec.methods:
                reasons[spec.id] = f"{method} not supported"
            elif method in rt.unsupported:
                reasons[spec.id] = f"{method} reported unsupported by provider"
            elif v.resolution.endpoint is None:
                reasons[spec.id] = v.resolution.reason
            elif rt.identity_failed:
                reasons[spec.id] = "network identity mismatch"
            elif rt.cooldown_until > now:
                reasons[spec.id] = f"rate-limited for {rt.cooldown_until - now:.0f}s"
            else:
                usable.append(v)
        usable.sort(key=lambda v: (_RANK.get(v.lifecycle, 9), v.state.consecutive_failures))
        return usable, reasons

    # ------------------------------------------------------------------ calls

    def call(
        self,
        chain: str,
        method: str,
        params: list[object] | dict[str, object],
        *,
        budget: CallBudget | None = None,
        provider_id: str | None = None,
    ) -> RpcOutcome:
        """Route one read-only request. ``provider_id`` pins the provider (no failover)."""
        found, reasons = self.eligible(chain, method)
        candidates: list[ProviderView] = found
        if provider_id is not None:
            candidates = [v for v in candidates if v.spec.id == provider_id]
            if not candidates:
                reasons = {provider_id: reasons.get(provider_id, "not registered for this chain")}
        if not candidates:
            raise NoEligibleProviderError(chain, method, reasons)

        attempts: list[str] = []
        attempt = 0
        index = 0
        while attempt < self._cfg.max_attempts and candidates:
            view = candidates[index % len(candidates)]
            spec = view.spec
            attempt += 1
            try:
                self._ensure_identity(chain, view, budget)
                outcome = self._send(view, method, params, budget)
                return outcome
            except BudgetExhaustedError:
                raise
            except RpcError as exc:
                attempts.append(f"{spec.id}: {exc.error_class} ({exc})")
                if exc.error_class in {"invalid_request", "not_available", "range_too_large"}:
                    raise
                if exc.error_class == "method_unsupported":
                    self._rt(spec.id).unsupported.add(method)
                    candidates = [c for c in candidates if c.spec.id != spec.id]
                    continue
                if exc.error_class == "unauthorized":
                    candidates = [c for c in candidates if c.spec.id != spec.id]
                    continue
                if exc.error_class == "rate_limited":
                    others = [c for c in candidates if c.spec.id != spec.id]
                    if others:
                        candidates = others
                        continue
                    wait = self._rt(spec.id).cooldown_until - self._clock()
                    if wait > INLINE_RETRY_AFTER_MAX_S:
                        break  # honour Retry-After: do not hammer a throttled provider
                    self._sleep(max(wait, 0.0))
                    self._rt(spec.id).cooldown_until = 0.0
                    continue
                self._backoff(attempt)
                index += 1
            except _IdentityMismatchError as exc:
                attempts.append(f"{spec.id}: {exc}")
                candidates = [c for c in candidates if c.spec.id != spec.id]
            except TransportError as exc:
                attempts.append(f"{spec.id}: {exc.kind}")
                if exc.kind in {"unsafe_destination", "redirect", "too_large"}:
                    candidates = [c for c in candidates if c.spec.id != spec.id]
                    continue
                self._backoff(attempt)
                index += 1
        raise RoutingFailedError(chain, method, attempts)

    def _backoff(self, attempt: int) -> None:
        cap = min(self._cfg.backoff_cap_s, self._cfg.backoff_base_s * (2 ** (attempt - 1)))
        self._sleep(cap * self._rng())

    def _ensure_identity(self, chain: str, view: ProviderView, budget: CallBudget | None) -> None:
        rt = self._rt(view.spec.id)
        now = self._clock()
        if rt.identity_ok_at is not None and now - rt.identity_ok_at < IDENTITY_TTL_S:
            return
        check = EXPECTED_IDENTITY[chain]
        outcome = self._send(view, check.method, [], budget)
        if not identity_matches(chain, outcome.result):
            rt.identity_failed = True
            self._record(
                view.spec.id, ok=False, latency_ms=outcome.latency_ms, outcome="network_mismatch"
            )
            raise _IdentityMismatchError(
                f"expected {check.label}, got {str(outcome.result)[:80]!r}"
            )
        rt.identity_ok_at = now

    def _send(
        self,
        view: ProviderView,
        method: str,
        params: list[object] | dict[str, object],
        budget: CallBudget | None,
    ) -> RpcOutcome:
        spec = view.spec
        endpoint = view.resolution.endpoint
        assert endpoint is not None  # noqa: S101 - guaranteed by eligible()
        rt = self._rt(spec.id)
        if budget is not None:
            budget.take(self._clock)
        interval = 1.0 / spec.client_rps if spec.client_rps else 0.25
        with self._lock:
            now = self._clock()
            wait = rt.next_slot - now
            rt.next_slot = max(now, rt.next_slot) + interval
            self._next_id += 1
            request = RpcRequest(self._next_id, method, params)
        if wait > 0:
            self._sleep(wait)
        try:
            response = self._transport.post_json(
                endpoint,
                request.encode(),
                timeout_s=self._cfg.timeout_s,
                max_bytes=self._cfg.max_response_bytes,
            )
        except TransportError as exc:
            self._record(spec.id, ok=False, latency_ms=None, outcome=f"transport_{exc.kind}")
            raise
        http_class = classify_http_status(response.status)
        if http_class is not None:
            if http_class == "rate_limited":
                retry_after = response.retry_after_s
                cooldown = min(
                    retry_after if retry_after is not None else 10.0, self._cfg.max_retry_after_s
                )
                rt.cooldown_until = self._clock() + cooldown
            self._record(
                spec.id, ok=False, latency_ms=response.elapsed_ms, outcome=f"http_{response.status}"
            )
            raise RpcError(http_class, f"HTTP {response.status}")
        try:
            result = parse_response(response.body, request)
        except RpcError as exc:
            if exc.error_class == "rate_limited":
                rt.cooldown_until = self._clock() + min(10.0, self._cfg.max_retry_after_s)
            self._record(
                spec.id,
                ok=not exc.counts_against_provider,
                latency_ms=response.elapsed_ms,
                outcome=f"rpc_{exc.error_class}",
            )
            raise
        self._record(spec.id, ok=True, latency_ms=response.elapsed_ms, outcome="ok")
        return RpcOutcome(result, spec.id, method, response.elapsed_ms, response.body)

    def _record(self, provider_id: str, *, ok: bool, latency_ms: int | None, outcome: str) -> None:
        if self._state is None:
            return
        try:
            self._state.record_call(provider_id, ok=ok, latency_ms=latency_ms, outcome=outcome)
        except BIPError as exc:  # recording must never break a read, but must be visible
            log.warning("could not record provider outcome for %s: %s", provider_id, exc)


class _IdentityMismatchError(BIPError):
    pass
