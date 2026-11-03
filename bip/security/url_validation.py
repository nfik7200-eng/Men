"""Validation of outbound URLs (SSRF defence, first layer).

Use :func:`validate_url` for every configurable provider or web URL before it is stored or used.

What this module checks
-----------------------
* scheme is in an allow-list (default ``https``/``wss``);
* no ``user:password@`` userinfo (prevents ``https://trusted@evil`` confusion and leaked creds);
* host is present and is not ``localhost``, an internal or special-use suffix
  (``.local``, ``.internal``, ``.localhost``, ``.lan``, ``.home.arpa``…), or a single-label name;
* literal IPv4/IPv6 addresses (including IPv4-mapped, 6to4, Teredo and NAT64-embedded IPv4) are
  globally routable, not private, loopback, link-local (incl. cloud metadata ``169.254.169.254``),
  multicast, reserved or unspecified;
* ambiguous numeric hosts (``2130706433``, ``0x7f.1``, ``127.1``) and IPv6 zone IDs are rejected;
* optionally (``resolve=True``) every address a hostname resolves to passes the same IP checks.

What it cannot guarantee
------------------------
Validation happens at one moment. **DNS rebinding** can return a public address now and a
private one when the HTTP client connects later. **Redirects** can send a client anywhere. Future
HTTP clients (roadmap Stage 3+) must therefore also:

1. resolve the host at connection time, validate every resolved IP with :func:`check_ip`, and
   connect to that validated IP (pinning), never re-resolving between check and connect;
2. disable automatic redirects, or validate each hop with :func:`validate_redirect`, capped at
   :data:`MAX_REDIRECTS` and never downgrading ``https`` to ``http``;
3. set connect, read and total timeouts and a maximum response size;
4. never pass arbitrary user-controlled URLs to an HTTP client; only URLs from validated
   configuration or the provider registry.

This module performs no network I/O unless ``resolve=True`` is passed, and tests inject a
fake resolver.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from bip.core.exceptions import UnsafeURLError

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int], Iterable[str]]

DEFAULT_ALLOWED_SCHEMES: frozenset[str] = frozenset({"https", "wss"})
MAX_REDIRECTS = 3
_DEFAULT_PORTS = {"https": 443, "wss": 443, "http": 80, "ws": 80}

BLOCKED_HOSTNAMES: frozenset[str] = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "metadata",
        "metadata.google.internal",
        "instance-data",
        "kubernetes.default",
    }
)
BLOCKED_SUFFIXES: tuple[str, ...] = (
    ".localhost",
    ".local",
    ".localdomain",
    ".internal",
    ".intranet",
    ".lan",
    ".home",
    ".home.arpa",
    ".corp",
    ".private",
    ".test",
    ".invalid",
    ".example",
    ".onion",
    ".arpa",
)
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_EXTRA_BLOCKED_NETS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "100.64.0.0/10",
        "198.18.0.0/15",
        "fc00::/7",
        "fe80::/10",
        "fec0::/10",  # deprecated IPv6 site-local; not covered by ipaddress.is_private
    )
)
_NUMERIC_LABEL = re.compile(r"^(?:0x[0-9a-f]*|[0-9]+)$", re.IGNORECASE)
_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


@dataclass(frozen=True, slots=True)
class ValidatedURL:
    """A URL that passed validation. ``resolved_ips`` is empty unless resolution was requested."""

    url: str
    scheme: str
    host: str
    port: int
    resolved_ips: tuple[str, ...] = ()


def check_ip(ip: IPAddress | str, *, allow_private: bool = False) -> IPAddress:
    """Raise :class:`UnsafeURLError` unless ``ip`` is a public unicast address.

    Embedded IPv4 addresses (IPv4-mapped, 6to4, Teredo, NAT64) are checked as well.
    ``allow_private`` permits private/loopback ranges for explicitly flagged local endpoints
    (an ADR is required to use it); it never permits multicast, unspecified or reserved.
    """
    try:
        addr = ipaddress.ip_address(ip) if isinstance(ip, str) else ip
    except ValueError:
        raise UnsafeURLError(f"not an IP address: {ip!r}") from None

    if isinstance(addr, ipaddress.IPv6Address):
        if addr.scope_id:
            raise UnsafeURLError("IPv6 zone identifiers are not allowed")
        embedded: list[ipaddress.IPv4Address] = []
        if addr.ipv4_mapped is not None:
            embedded.append(addr.ipv4_mapped)
        if addr.sixtofour is not None:
            embedded.append(addr.sixtofour)
        if addr.teredo is not None:
            embedded.extend(addr.teredo)
        if addr in _NAT64:
            embedded.append(ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF))
        for inner in embedded:
            check_ip(inner, allow_private=allow_private)

    if addr.is_multicast or addr.is_unspecified or addr.is_reserved:
        raise UnsafeURLError(f"address {addr} is multicast, unspecified or reserved")
    if allow_private:
        return addr
    if addr.is_loopback:
        raise UnsafeURLError(f"address {addr} is loopback")
    if addr.is_link_local:
        raise UnsafeURLError(f"address {addr} is link-local (includes cloud metadata endpoints)")
    if addr.is_private or any(addr in net for net in _EXTRA_BLOCKED_NETS):
        raise UnsafeURLError(f"address {addr} is private or special-use")
    if not addr.is_global:
        raise UnsafeURLError(f"address {addr} is not globally routable")
    return addr


def _check_hostname(host: str) -> None:
    if host in BLOCKED_HOSTNAMES or host.endswith(BLOCKED_SUFFIXES):
        raise UnsafeURLError(f"host {host!r} is local or internal")
    if "." not in host:
        raise UnsafeURLError(f"single-label host {host!r} is not allowed")
    labels = host.split(".")
    if not all(_LABEL.fullmatch(label) for label in labels):
        raise UnsafeURLError(f"host {host!r} is not a valid DNS name")
    if labels[-1].isdigit():
        raise UnsafeURLError(f"host {host!r} has a numeric top-level label")


def _default_resolver(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def validate_url(
    url: str,
    *,
    allowed_schemes: Iterable[str] = DEFAULT_ALLOWED_SCHEMES,
    allow_private: bool = False,
    resolve: bool = False,
    resolver: Resolver | None = None,
) -> ValidatedURL:
    """Validate ``url`` for outbound use; raise :class:`UnsafeURLError` with a reason if unsafe.

    ``resolve=True`` additionally resolves the host (via ``resolver`` or the system resolver)
    and checks every address. Even then, the caller's HTTP client must pin and re-check at
    connection time (see module docstring).
    """
    if not url or len(url) > 2048:
        raise UnsafeURLError("URL is empty or longer than 2048 characters")
    if any(ch.isspace() or ord(ch) < 0x20 for ch in url):
        raise UnsafeURLError("URL contains whitespace or control characters")
    if "\\" in url:
        raise UnsafeURLError("URL contains a backslash")

    try:
        parts = urlsplit(url)
        port_raw = parts.port
    except ValueError as exc:
        raise UnsafeURLError(f"malformed URL: {exc}") from None

    scheme = parts.scheme.lower()
    allowed = {s.lower() for s in allowed_schemes}
    if scheme not in allowed:
        raise UnsafeURLError(
            f"scheme {scheme or '(none)'!r} not allowed; allowed: {sorted(allowed)}"
        )
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise UnsafeURLError("credentials in URL userinfo are not allowed")

    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise UnsafeURLError("URL has no host")
    if "%" in host:
        raise UnsafeURLError(
            "percent-encoding or IPv6 zone identifiers in the host are not allowed"
        )

    port = port_raw if port_raw is not None else _DEFAULT_PORTS.get(scheme, 0)
    if not 0 < port < 65536:
        raise UnsafeURLError(f"port {port} is out of range")

    literal: IPAddress | None
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None

    if literal is not None:
        check_ip(literal, allow_private=allow_private)
    else:
        if all(_NUMERIC_LABEL.fullmatch(label) for label in host.split(".")):
            # inet_aton-style forms (2130706433, 0x7f.1, 127.1) that some resolvers accept
            raise UnsafeURLError(f"ambiguous numeric host {host!r}")
        try:
            host = host.encode("idna").decode("ascii").lower()
        except UnicodeError:
            raise UnsafeURLError(f"host {host!r} is not a valid internationalized name") from None
        if not allow_private:
            _check_hostname(host)

    resolved: tuple[str, ...] = ()
    if resolve and literal is None:
        resolve_fn = resolver if resolver is not None else _default_resolver
        try:
            addresses = tuple(dict.fromkeys(resolve_fn(host, port)))
        except OSError as exc:
            raise UnsafeURLError(f"cannot resolve host {host!r}: {exc}") from None
        if not addresses:
            raise UnsafeURLError(f"host {host!r} resolved to no addresses")
        for address in addresses:
            check_ip(address, allow_private=allow_private)
        resolved = addresses

    return ValidatedURL(url=url, scheme=scheme, host=host, port=port, resolved_ips=resolved)


def validate_redirect(
    current_url: str,
    location: str,
    *,
    hop: int,
    allowed_schemes: Iterable[str] = DEFAULT_ALLOWED_SCHEMES,
    resolver: Resolver | None = None,
    resolve: bool = False,
) -> ValidatedURL:
    """Validate one redirect hop from ``current_url`` to the ``Location`` header value.

    ``hop`` is 1 for the first redirect. Raises when the hop count exceeds
    :data:`MAX_REDIRECTS`, the scheme downgrades from https/wss, or the target is unsafe.
    """
    if hop > MAX_REDIRECTS:
        raise UnsafeURLError(f"too many redirects (limit {MAX_REDIRECTS})")
    target = urljoin(current_url, location)
    validated = validate_url(
        target, allowed_schemes=allowed_schemes, resolve=resolve, resolver=resolver
    )
    if urlsplit(current_url).scheme.lower() in {"https", "wss"} and validated.scheme not in {
        "https",
        "wss",
    }:
        raise UnsafeURLError("redirect downgrades from a secure scheme")
    return validated
