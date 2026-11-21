"""Provider lifecycle, computed from registry metadata, configuration and recorded checks.

States (first match wins):

=================  =========================================================================
``disabled``       registry default or operator override disables it
``registered``     listed, but no adapter exists (non-RPC sources)
``configured``     credentials present for a source without an adapter (never used by BIP)
``adapter_available``  adapter exists, but the endpoint is not configured (or invalid)
``untested``       adapter + endpoint, but no request has ever completed (success or failure)
``healthy``        last request succeeded and no recent failures
``degraded``       last request succeeded, but failures happened recently
``unavailable``    last request failed
=================  =========================================================================

"Healthy" only means recent requests worked. It says nothing about quota left, historical depth
or completeness.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from bip.providers.registry import EndpointResolution, ProviderSpec, Registry, resolve_endpoint
from bip.providers.state import ProviderState

Lifecycle = Literal[
    "disabled",
    "registered",
    "configured",
    "adapter_available",
    "untested",
    "healthy",
    "degraded",
    "unavailable",
]


@dataclass(frozen=True, slots=True)
class ProviderView:
    spec: ProviderSpec
    lifecycle: Lifecycle
    enabled: bool
    resolution: EndpointResolution
    state: ProviderState
    limitations: tuple[str, ...]

    @property
    def usable(self) -> bool:
        return (
            self.enabled
            and self.spec.has_adapter
            and self.resolution.endpoint is not None
            and self.lifecycle in {"untested", "healthy", "degraded", "unavailable"}
        )


def _limitations(spec: ProviderSpec) -> tuple[str, ...]:
    out: list[str] = []
    if spec.has_adapter:
        out.append(
            "Archive depth: "
            + ({"yes": "archive", "no": "not archive"}.get(spec.archive or "", "unknown"))
        )
        if spec.protocol == "evm-jsonrpc":
            out.append(
                "eth_getLogs block range limit: "
                + (str(spec.max_log_block_range) if spec.max_log_block_range else "unknown")
            )
            out.append("No address index: wallet history comes from recent Transfer logs only.")
            out.append(
                "Internal (contract-initiated) ETH transfers are not visible via standard RPC."
            )
        else:
            out.append("History depth served by getSignaturesForAddress/getTransaction: unknown.")
    out.append("Rate limit: " + (spec.rate_limit or "unknown"))
    return tuple(out)


def compute_view(
    spec: ProviderSpec,
    state: ProviderState | None,
    environ: Mapping[str, str] | None = None,
) -> ProviderView:
    st = state if state is not None else ProviderState(provider_id=spec.id)
    resolution = resolve_endpoint(spec, environ)
    enabled = st.enabled_override if st.enabled_override is not None else spec.enabled
    lifecycle: Lifecycle
    if not enabled:
        lifecycle = "disabled"
    elif not spec.has_adapter:
        lifecycle = "configured" if resolution.configuration == "configured" else "registered"
    elif resolution.endpoint is None:
        lifecycle = "adapter_available"
    elif st.last_check_at is None:
        lifecycle = "untested"
    elif st.consecutive_failures > 0:
        lifecycle = "unavailable"
    elif st.last_failure_at is not None and _recent(st.last_failure_at, st.last_success_at):
        lifecycle = "degraded"
    else:
        lifecycle = "healthy"
    return ProviderView(spec, lifecycle, enabled, resolution, st, _limitations(spec))


def _recent(last_failure: datetime, last_success: datetime | None) -> bool:
    """A failure within 15 minutes before the latest success marks the provider degraded."""
    if last_success is None:
        return True
    return (last_success - last_failure).total_seconds() < 900


def all_views(
    registry: Registry,
    states: Mapping[str, ProviderState],
    environ: Mapping[str, str] | None = None,
) -> list[ProviderView]:
    return [compute_view(s, states.get(s.id), environ) for s in registry.all()]
