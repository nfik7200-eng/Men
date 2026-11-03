"""Secret references and redaction.

Secrets reach BIP only through environment variables, or through files named by a
``<NAME>_FILE`` environment variable (compatible with Docker secrets). They are wrapped in
:class:`pydantic.SecretStr` so ``repr``/``str`` never reveal them.

Redaction is layered:

1. **Exact values** registered at load time (:func:`register_secret`) are replaced wherever they
   appear. This is the most reliable layer, because it does not depend on guessing formats.
2. **Patterns** catch common credential shapes: ``user:password@`` URL userinfo, sensitive query
   parameters (``api_key=``, ``token=``…), ``Authorization: Bearer …`` headers, ``key=value``
   pairs with sensitive key names, and long opaque URL path segments (many RPC providers embed
   API keys in the path, e.g. ``/v2/<key>``).

Blockchain identifiers must stay readable in logs, so the path-segment rule deliberately skips
``0x``-prefixed hex (transaction hashes, addresses). It may still redact other long opaque path
segments, such as a Solana signature inside a URL path. That is an accepted, conservative
trade-off.
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from pydantic import SecretStr

from bip.core.exceptions import SecretError

REDACTED = "[REDACTED]"

_SECRET_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
_MIN_REGISTERED_LENGTH = 6  # shorter values would cause false-positive redaction everywhere

SENSITIVE_KEY_SUFFIXES: tuple[str, ...] = (
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "apikey",
    "api_key",
    "access_key",
    "secret_key",
    "private_key",
    "authorization",
    "auth",
    "credential",
    "credentials",
    "dsn",
    "key",
)
"""A key is sensitive when its normalized name ends with one of these (``db_password``,
``x_api_key``), so descriptive names such as ``token_address`` are not masked."""

_sensitive_key_alt = (
    r"[A-Za-z0-9_\-]{0,40}?(?:password|passwd|pwd|secret|token|api[_\-]?key|access[_\-]?key|"
    r"secret[_\-]?key|private[_\-]?key|authorization|auth|credentials?|dsn|key)"
)

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # scheme://user:password@host  -> scheme://[REDACTED]@host
    (
        re.compile(r"(?<![a-zA-Z0-9+.\-])(?P<pre>[a-zA-Z][a-zA-Z0-9+.\-]{0,31}://)[^/\s@]{1,512}@"),
        rf"\g<pre>{REDACTED}@",
    ),
    # Authorization: Bearer xyz / Basic xyz
    (
        re.compile(r"(?i)(?P<pre>\b(?:bearer|basic|token)\s+)[A-Za-z0-9._~+/=\-]{8,}"),
        rf"\g<pre>{REDACTED}",
    ),
    # query/body/key=value pairs with sensitive key names: ?api_key=..., password: "..."
    (
        re.compile(
            rf"(?i)(?P<pre>\b{_sensitive_key_alt}\b[\"']?\s*[=:]\s*[\"']?)(?P<val>[^\s&\"',;}}]+)"
        ),
        rf"\g<pre>{REDACTED}",
    ),
)

_URL = re.compile(r"(?<![a-zA-Z0-9+.\-])[a-zA-Z][a-zA-Z0-9+.\-]{0,31}://[^\s\"'<>]{1,4096}")
_OPAQUE_SEGMENT = re.compile(r"^[A-Za-z0-9_\-]{20,}$")


def _redact_path_segment(segment: str) -> str:
    if (
        not segment.startswith("0x")
        and _OPAQUE_SEGMENT.fullmatch(segment)
        and any(c.isdigit() for c in segment)
        and any(c.isalpha() for c in segment)
    ):
        return REDACTED
    return segment


def _redact_url(match: re.Match[str]) -> str:
    url = match.group(0)
    scheme_end = url.index("://") + 3
    path_start = url.find("/", scheme_end)
    if path_start == -1:
        return url
    tail_start = len(url)
    for sep in "?#":
        idx = url.find(sep, path_start)
        if idx != -1:
            tail_start = min(tail_start, idx)
    path = url[path_start:tail_start]
    redacted_path = "/".join(_redact_path_segment(seg) for seg in path.split("/"))
    return url[:path_start] + redacted_path + url[tail_start:]


_registry_lock = threading.Lock()
_registered: set[str] = set()


def register_secret(value: str | SecretStr | None) -> None:
    """Register an exact secret value so :func:`redact_text` always removes it.

    Values shorter than six characters are ignored, because redacting them would mangle
    ordinary text.
    """
    if value is None:
        return
    raw = value.get_secret_value() if isinstance(value, SecretStr) else value
    if len(raw) < _MIN_REGISTERED_LENGTH:
        return
    with _registry_lock:
        _registered.add(raw)


def clear_registered_secrets() -> None:
    """Forget all registered secret values (intended for tests)."""
    with _registry_lock:
        _registered.clear()


MAX_REDACT_CHARS = 256 * 1024
"""Longer input is truncated before redaction, bounding worst-case regex cost."""


def redact_text(text: str) -> str:
    """Return ``text`` with registered secrets and credential-shaped substrings removed.

    Input longer than :data:`MAX_REDACT_CHARS` is truncated first (marked ``[TRUNCATED]``).
    All patterns are bounded so cost stays linear in the input length.
    """
    if len(text) > MAX_REDACT_CHARS:
        text = text[:MAX_REDACT_CHARS] + "[TRUNCATED]"
    with _registry_lock:
        exact = sorted(_registered, key=len, reverse=True)
    for value in exact:
        text = text.replace(value, REDACTED)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    # Long opaque URL path segments (provider keys embedded in the path). Linear-time.
    return _URL.sub(_redact_url, text)


def is_sensitive_key(key: str) -> bool:
    """True if a mapping key name suggests its value is a credential."""
    lowered = key.lower().replace("-", "_")
    return lowered.endswith(SENSITIVE_KEY_SUFFIXES)


def redact_value(value: object) -> object:
    """Recursively redact strings, mappings and sequences; mask values under sensitive keys."""
    if isinstance(value, SecretStr):
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        mapping = cast("Mapping[object, object]", value)
        return {
            k: REDACTED if is_sensitive_key(str(k)) else redact_value(v) for k, v in mapping.items()
        }
    if isinstance(value, list | tuple):
        seq = cast("list[object] | tuple[object, ...]", value)
        items = [redact_value(v) for v in seq]
        return tuple(items) if isinstance(seq, tuple) else items
    return value


def validate_secret_name(name: str) -> str:
    """Validate an environment-variable name used as a secret reference."""
    if not _SECRET_NAME.fullmatch(name):
        raise SecretError(
            f"invalid secret reference name {name!r}: use UPPER_SNAKE_CASE, 2-128 characters"
        )
    return name


def read_secret(name: str, environ: Mapping[str, str] | None = None) -> SecretStr | None:
    """Resolve a secret reference by name.

    Lookup order: ``$NAME`` then the file named by ``$NAME_FILE``. Returns ``None`` when neither
    is set. Setting both is rejected as ambiguous. The value is registered for redaction.
    Errors never include the secret value itself.
    """
    validate_secret_name(name)
    env = os.environ if environ is None else environ
    direct = env.get(name)
    file_ref = env.get(f"{name}_FILE")
    if direct is not None and file_ref is not None:
        raise SecretError(f"both {name} and {name}_FILE are set; set only one")
    if direct is not None:
        if direct == "":
            raise SecretError(f"{name} is set but empty")
        register_secret(direct)
        return SecretStr(direct)
    if file_ref is not None:
        path = Path(file_ref)
        try:
            content = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise SecretError(f"cannot read secret file for {name}: {exc.strerror}") from None
        if not content:
            raise SecretError(f"secret file for {name} is empty")
        register_secret(content)
        return SecretStr(content)
    return None
