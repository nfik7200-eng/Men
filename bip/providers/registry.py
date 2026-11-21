"""Structured provider registry loaded from ``registry.toml``.

The registry describes providers; it never contacts them. Health and lifecycle state are computed
in :mod:`bip.providers.status` from this metadata plus recorded health checks.

Security rules enforced at load and resolution time:
- only read-only JSON-RPC methods from :data:`READ_ONLY_METHODS` can be listed;
- built-in endpoint URLs and URLs read from ``endpoint_env`` must pass
  :func:`bip.security.url_validation.validate_url` (https only, public hosts) **and** match the
  provider's ``allowed_hosts``, so neither users nor misconfiguration can redirect BIP to
  arbitrary hosts;
- URLs from the environment are secrets (they often embed API keys): they are registered for
  log redaction and never returned by the API.
"""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Annotated, Literal, Self, cast
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator

from bip.contracts.blockchain import ChainFamily, get_chain
from bip.core.exceptions import BIPError, UnsafeURLError
from bip.security.secrets import register_secret, validate_secret_name
from bip.security.url_validation import validate_url

REGISTRY_PATH = Path(__file__).with_name("registry.toml")

Protocol = Literal["evm-jsonrpc", "solana-jsonrpc", "none"]
Kind = Literal["rpc", "dataset", "indexer_api", "web"]
Access = Literal["public", "credentials"]

EVM_READ_METHODS = frozenset(
    {
        "eth_chainId",
        "eth_blockNumber",
        "eth_getBlockByNumber",
        "eth_getBalance",
        "eth_getTransactionByHash",
        "eth_getTransactionReceipt",
        "eth_getLogs",
        "eth_getTransactionCount",
        "eth_getCode",
    }
)
SOLANA_READ_METHODS = frozenset(
    {
        "getHealth",
        "getGenesisHash",
        "getSlot",
        "getBlock",
        "getBalance",
        "getSignaturesForAddress",
        "getTransaction",
        "getTokenAccountsByOwner",
        "getVersion",
        "getBlockHeight",
        "getLatestBlockhash",
    }
)
WRITE_METHOD_PATTERN = re.compile(
    r"^(eth_send|eth_sign|eth_submit|personal_|admin_|debug_|miner_|wallet_|engine_|txpool_)"
    r"|^(sendTransaction|requestAirdrop|simulateTransaction|eth_accounts|eth_coinbase)$"
)
"""State-changing, signing, account or node-administration methods. Never permitted, even if a
later edit adds one to an allow-list (checked at import and when a probe is planned)."""


def is_write_capable(method: str) -> bool:
    return bool(WRITE_METHOD_PATTERN.search(method))


for _m in EVM_READ_METHODS | SOLANA_READ_METHODS:
    if is_write_capable(_m):  # pragma: no cover - guards future edits
        raise RuntimeError(f"write-capable method {_m!r} in the read-only allow-list")
READ_ONLY_METHODS: Mapping[str, frozenset[str]] = {
    "evm-jsonrpc": EVM_READ_METHODS,
    "solana-jsonrpc": SOLANA_READ_METHODS,
    "none": frozenset(),
}
_PROTOCOL_FAMILY = {"evm-jsonrpc": ChainFamily.EVM, "solana-jsonrpc": ChainFamily.SOLANA}


class RegistryError(BIPError):
    """The provider registry file is invalid."""


class ProviderNotFoundError(BIPError):
    """No provider with that identifier is registered."""


Slug = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9\-]{1,63}$")]
Host = Annotated[
    str, Field(pattern=r"^\.?[a-z0-9]([a-z0-9\-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]*[a-z0-9])?)+$")
]


class ProviderSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: Slug
    name: Annotated[str, Field(min_length=1, max_length=80)]
    operator: Annotated[str, Field(min_length=1, max_length=80)]
    kind: Kind
    protocol: Protocol
    chains: tuple[str, ...]
    network: str | None = None
    access: Access
    endpoint_url: str | None = None
    endpoint_env: str | None = None
    allowed_hosts: tuple[Host, ...] = ()
    enabled: bool = True
    infrastructure: Annotated[str, Field(pattern=r"^[a-z0-9\-]{2,40}$")] | None = None
    """Operator group. Providers in the same group are never treated as independent."""
    methods: tuple[str, ...] = ()
    client_rps: Annotated[float, Field(gt=0, le=50)] | None = None
    rate_limit: Annotated[str, Field(max_length=300)] | None = None
    archive: Literal["yes", "no"] | None = None
    """``None`` means unknown."""
    max_log_block_range: Annotated[int, Field(ge=1)] | None = None
    """Documented eth_getLogs block-range limit. ``None`` means unknown."""
    documentation_basis: Literal["official_docs", "search_derived", "unverified"] = "unverified"
    """How the documented facts in this entry (limits, archive, methods) were obtained."""
    documentation_checked: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")] | None = None
    """Date the documentation facts were last checked (YYYY-MM-DD)."""
    docs_url: str
    terms_url: str
    sources: tuple[str, ...] = ()
    notes: Annotated[str, Field(max_length=600)] | None = None

    @property
    def has_adapter(self) -> bool:
        return self.protocol != "none"

    @property
    def requires_credentials(self) -> bool:
        return self.access == "credentials"

    @model_validator(mode="after")
    def _rules(self) -> Self:
        for chain in self.chains:
            get_chain(chain)
        allowed = READ_ONLY_METHODS[self.protocol]
        bad = [m for m in self.methods if m not in allowed]
        if bad:
            raise ValueError(f"methods not permitted (not read-only or wrong protocol): {bad}")
        if self.protocol != "none":
            family = _PROTOCOL_FAMILY[self.protocol]
            if len(self.chains) != 1 or get_chain(self.chains[0]).family is not family:
                raise ValueError("an RPC provider entry must serve exactly one chain of its family")
            if self.network is None:
                raise ValueError("RPC providers must declare their network")
            if not self.allowed_hosts:
                raise ValueError("RPC providers must declare allowed_hosts")
            if self.endpoint_url is None and self.endpoint_env is None:
                raise ValueError("RPC providers need endpoint_url or endpoint_env")
            if self.infrastructure is None:
                raise ValueError("RPC providers must declare an infrastructure group")
        if self.endpoint_url is not None:
            if self.access != "public":
                raise ValueError("built-in endpoint_url is only allowed for public providers")
            try:
                _check_url(self.endpoint_url, self.allowed_hosts)
            except UnsafeURLError as exc:
                raise ValueError(f"endpoint_url rejected: {exc}") from None
        if self.endpoint_env is not None:
            validate_secret_name(self.endpoint_env)
            if not self.endpoint_env.startswith("BIP_PROVIDER_"):
                raise ValueError("endpoint_env must start with BIP_PROVIDER_")
        for url in (self.docs_url, self.terms_url, *self.sources):
            if not url.startswith("https://"):
                raise ValueError("documentation links must be https URLs")
        return self


def host_allowed(host: str, allowed_hosts: tuple[str, ...]) -> bool:
    host = host.lower().rstrip(".")
    for pattern in allowed_hosts:
        if pattern.startswith("."):
            if host.endswith(pattern) and len(host) > len(pattern):
                return True
        elif host == pattern:
            return True
    return False


def _check_url(url: str, allowed_hosts: tuple[str, ...]) -> None:
    validated = validate_url(url, allowed_schemes={"https"})
    if not host_allowed(validated.host, allowed_hosts):
        raise UnsafeURLError(f"host {validated.host!r} is not in this provider's allowed_hosts")
    if urlsplit(url).fragment:
        raise UnsafeURLError("endpoint URLs must not contain a fragment")


@dataclass(frozen=True, slots=True)
class ResolvedEndpoint:
    url: SecretStr
    host: str
    from_environment: bool


@dataclass(frozen=True, slots=True)
class EndpointResolution:
    endpoint: ResolvedEndpoint | None
    configuration: Literal["configured", "not_configured", "invalid", "not_required"]
    reason: str


def resolve_endpoint(
    spec: ProviderSpec, environ: Mapping[str, str] | None = None
) -> EndpointResolution:
    """Find the endpoint BIP may use for ``spec``. Never raises; never returns secret text."""
    env = os.environ if environ is None else environ
    if spec.protocol == "none":
        configured = spec.endpoint_env is not None and bool(env.get(spec.endpoint_env, ""))
        if spec.endpoint_env is None:
            return EndpointResolution(
                None, "not_required", "No credentials needed; no adapter yet."
            )
        return EndpointResolution(
            None,
            "configured" if configured else "not_configured",
            f"{spec.endpoint_env} is {'set' if configured else 'not set'}; no adapter yet.",
        )
    if spec.endpoint_env is not None:
        raw = env.get(spec.endpoint_env, "")
        if raw:
            register_secret(raw)
            try:
                _check_url(raw, spec.allowed_hosts)
            except UnsafeURLError as exc:
                return EndpointResolution(
                    None, "invalid", f"{spec.endpoint_env} is set but rejected: {exc}"
                )
            host = urlsplit(raw).hostname or ""
            return EndpointResolution(
                ResolvedEndpoint(SecretStr(raw), host, True),
                "configured",
                f"Using {spec.endpoint_env}.",
            )
    if spec.endpoint_url is not None:
        host = urlsplit(spec.endpoint_url).hostname or ""
        return EndpointResolution(
            ResolvedEndpoint(SecretStr(spec.endpoint_url), host, False),
            "not_required",
            "Public endpoint; no credentials needed.",
        )
    return EndpointResolution(
        None,
        "not_configured",
        f"Set {spec.endpoint_env} to enable this provider."
        if spec.endpoint_env
        else "No endpoint.",
    )


def independent(a: ProviderSpec, b: ProviderSpec) -> bool:
    """True only for different, declared infrastructure groups."""
    return (
        a.id != b.id
        and a.infrastructure is not None
        and b.infrastructure is not None
        and a.infrastructure != b.infrastructure
    )


class Registry:
    """Immutable view over the loaded provider specs."""

    def __init__(self, specs: tuple[ProviderSpec, ...]) -> None:
        ids = [s.id for s in specs]
        if len(set(ids)) != len(ids):
            raise RegistryError("duplicate provider ids in registry")
        self._specs = specs
        self._by_id = {s.id: s for s in specs}

    def all(self) -> tuple[ProviderSpec, ...]:
        return self._specs

    def get(self, provider_id: str) -> ProviderSpec:
        spec = self._by_id.get(provider_id)
        if spec is None:
            raise ProviderNotFoundError(f"provider {provider_id!r} is not registered")
        return spec

    def for_chain(self, chain: str) -> tuple[ProviderSpec, ...]:
        return tuple(s for s in self._specs if chain in s.chains)

    def rpc_providers(self, chain: str) -> tuple[ProviderSpec, ...]:
        return tuple(s for s in self.for_chain(chain) if s.has_adapter)

    def methods(self, provider_id: str) -> tuple[str, ...]:
        return self.get(provider_id).methods


def load_registry(path: Path = REGISTRY_PATH) -> Registry:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RegistryError(f"cannot read provider registry: {exc}") from None
    entries = data.get("provider")
    if not isinstance(entries, list) or not entries:
        raise RegistryError("registry has no [[provider]] entries")
    specs: list[ProviderSpec] = []
    for i, entry in enumerate(cast("list[object]", entries)):
        try:
            specs.append(ProviderSpec.model_validate(entry))
        except ValidationError as exc:
            err = exc.errors(include_input=False, include_url=False)[0]
            where = ".".join(str(p) for p in err["loc"])
            raise RegistryError(f"provider entry {i}: {where}: {err['msg']}") from None
    return Registry(tuple(specs))


@cache
def default_registry() -> Registry:
    return load_registry()
