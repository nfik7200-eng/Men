"""Setup store: secrets and settings entered from the Setup page (ADR-056).

Two JSON files under ``<data_root>/config/`` (directory 0700, files 0600):

* ``secrets.json``: secret values by ``BIP_*`` name. Write-only from the API: values are read
  here and by :class:`LayeredEnviron`, never returned to a browser.
* ``settings.json``: overrides for the allowlisted keys in :mod:`bip.config.setup_catalog`.

The process environment always wins over the store. Writes are atomic (temp file, fsync,
replace) and refuse symlinks. A corrupt file is an error, never an empty store.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import ValidationError

from bip.config.settings import Settings, load_settings
from bip.config.setup_catalog import BY_KEY
from bip.core.exceptions import ConfigurationError
from bip.security.secrets import register_secret

_SECRET_NAME = re.compile(r"^BIP_[A-Z0-9_]{2,120}$")
SECRETS_FILE = "secrets.json"
SETTINGS_FILE = "settings.json"
MAX_VALUE_LENGTH = 4096


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ConfigStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ files

    def _path(self, name: str) -> Path:
        return self.directory / name

    def _read(self, name: str) -> dict[str, Any]:
        path = self._path(name)
        if path.is_symlink():
            raise ConfigurationError(f"setup file {name} is a symlink; refusing to read it")
        if not path.exists():
            return {}
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ConfigurationError(
                f"setup file {name} is unreadable or not valid JSON; fix or remove it"
            ) from None
        if not isinstance(doc, dict):
            raise ConfigurationError(f"setup file {name} is not a JSON object")
        return cast(dict[str, Any], doc)

    def _write(self, name: str, doc: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        path = self._path(name)
        if path.is_symlink():
            raise ConfigurationError(f"setup file {name} is a symlink; refusing to write it")
        tmp = self._path(f".{name}.tmp")
        if tmp.exists() or tmp.is_symlink():
            tmp.unlink()
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, indent=2, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                tmp.unlink()
        dfd = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)

    # ------------------------------------------------------------------ secrets

    def get_secret(self, name: str) -> str | None:
        entry = self._read(SECRETS_FILE).get(name)
        if isinstance(entry, dict):
            value = cast(dict[str, Any], entry).get("value")
            if isinstance(value, str) and value:
                register_secret(value)
                return value
        return None

    def secret_names(self) -> list[str]:
        return sorted(self._read(SECRETS_FILE))

    def secret_meta(self, name: str) -> dict[str, Any] | None:
        entry = self._read(SECRETS_FILE).get(name)
        if not isinstance(entry, dict):
            return None
        meta = cast(dict[str, Any], entry)
        return {"updated_at": meta.get("updated_at"), "updated_by": meta.get("updated_by")}

    def set_secret(self, name: str, value: str, *, actor: str | None) -> None:
        if not _SECRET_NAME.match(name):
            raise ConfigurationError("secret names are upper-case BIP_* names")
        value = value.strip()
        if not value:
            raise ConfigurationError(f"{name}: the value is empty")
        if len(value) > MAX_VALUE_LENGTH:
            raise ConfigurationError(f"{name}: the value is too long")
        with self._lock:
            doc = self._read(SECRETS_FILE)
            doc[name] = {"value": value, "updated_at": _now(), "updated_by": actor}
            self._write(SECRETS_FILE, doc)
        register_secret(value)

    def clear_secret(self, name: str, *, actor: str | None) -> bool:
        del actor  # recorded in the audit log by the caller
        with self._lock:
            doc = self._read(SECRETS_FILE)
            if name not in doc:
                return False
            del doc[name]
            self._write(SECRETS_FILE, doc)
            return True

    # ------------------------------------------------------------------ settings

    def _settings_doc(self) -> dict[str, Any]:
        doc = self._read(SETTINGS_FILE)
        doc.setdefault("overrides", {})
        doc.setdefault("meta", {})
        return doc

    def overrides(self) -> dict[str, Any]:
        """Nested overrides exactly as stored (unfiltered)."""
        return cast(dict[str, Any], self._settings_doc()["overrides"])

    def setting_meta(self, key: str) -> dict[str, Any] | None:
        meta = cast(dict[str, Any], self._settings_doc()["meta"]).get(key)
        return cast(dict[str, Any], meta) if isinstance(meta, dict) else None

    def set_setting(self, key: str, value: object, *, actor: str | None) -> None:
        with self._lock:
            doc = self._settings_doc()
            node = cast(dict[str, Any], doc["overrides"])
            *parents, leaf = key.split(".")
            for part in parents:
                node = cast(dict[str, Any], node.setdefault(part, {}))
            node[leaf] = value
            doc["meta"][key] = {"updated_at": _now(), "updated_by": actor}
            self._write(SETTINGS_FILE, doc)

    def clear_setting(self, key: str, *, actor: str | None) -> bool:
        del actor
        with self._lock:
            doc = self._settings_doc()
            node = cast(dict[str, Any], doc["overrides"])
            *parents, leaf = key.split(".")
            for part in parents:
                child = node.get(part)
                if not isinstance(child, dict):
                    return False
                node = cast(dict[str, Any], child)
            if leaf not in node:
                return False
            del node[leaf]
            if parents and not node:
                del cast(dict[str, Any], doc["overrides"])[parents[0]]
            doc["meta"].pop(key, None)
            self._write(SETTINGS_FILE, doc)
            return True


def flatten(overrides: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    """``{"workers": {"enabled": True}}`` -> ``{"workers.enabled": True}``."""
    out: dict[str, Any] = {}
    for k, v in overrides.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict) and key not in BY_KEY:
            out.update(flatten(cast(Mapping[str, Any], v), f"{key}."))
        else:
            out[key] = v
    return out


def _nest(flat: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in flat.items():
        node = out
        *parents, leaf = key.split(".")
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = value
    return out


class LayeredEnviron(Mapping[str, str]):
    """The environment first, then store secrets, read live. A name set in the environment
    (directly or as ``<NAME>_FILE``) is never supplied by the store, so ``read_secret`` never
    sees both."""

    def __init__(self, environ: Mapping[str, str], store: ConfigStore) -> None:
        self._env = environ
        self._store = store

    def _env_has(self, key: str) -> bool:
        return key in self._env or f"{key}_FILE" in self._env

    def __getitem__(self, key: str) -> str:
        if key in self._env:
            return self._env[key]
        if not key.endswith("_FILE") and not self._env_has(key):
            value = self._store.get_secret(key)
            if value is not None:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        seen = set(self._env)
        yield from self._env
        for name in self._store.secret_names():
            if name not in seen and not self._env_has(name):
                yield name

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        try:
            self[key]
        except KeyError:
            return False
        return True

    def source(self, name: str) -> Literal["environment", "setup"] | None:
        if self._env_has(name):
            return "environment"
        return "setup" if self._store.get_secret(name) is not None else None


@dataclass
class EffectiveSettings:
    applied: dict[str, Any] = field(default_factory=dict[str, Any])
    """Nested overrides from the store that are in force."""
    overridden_by_environment: list[str] = field(default_factory=list[str])
    ignored: list[str] = field(default_factory=list[str])
    """Stored keys that are not editable (never applied)."""
    error: str | None = None
    """Why the stored overrides were not applied (validation failed), if they were not."""


def env_sets(environ: Mapping[str, str], key: str) -> bool:
    name = BY_KEY[key].env_name if key in BY_KEY else "BIP_" + key.upper().replace(".", "__")
    upper = {k.upper() for k in environ}
    return name in upper


def store_for(settings: Settings) -> ConfigStore:
    return ConfigStore(settings.resolved_data_root / "config")


def resolve_overrides(
    store: ConfigStore, environ: Mapping[str, str]
) -> tuple[dict[str, Any], EffectiveSettings]:
    """The editable, not environment-set overrides (nested), plus a report."""
    report = EffectiveSettings()
    keep: dict[str, Any] = {}
    for key, value in flatten(store.overrides()).items():
        if key not in BY_KEY:
            report.ignored.append(key)
        elif env_sets(environ, key):
            report.overridden_by_environment.append(key)
        else:
            keep[key] = value
    return _nest(keep), report


def load_effective_settings() -> tuple[Settings, EffectiveSettings]:
    """Settings from the process environment plus the store's allowlisted overrides.

    Invalid stored overrides are not applied (the environment-only settings load instead) and
    the reason is reported, so a bad value saved from the UI can never stop the API starting.
    """
    base = load_settings()
    nested, report = resolve_overrides(store_for(base), os.environ)
    if not nested:
        return base, report
    try:
        settings = load_settings(**nested)
    except ConfigurationError as exc:
        report.error = str(exc)
        return base, report
    report.applied = nested
    return settings, report


def validate_override(base: Settings, key: str, value: object) -> str | None:
    """``None`` when ``base`` with ``key = value`` is a valid configuration, else the reason."""
    data = base.model_dump()
    node = data
    *parents, leaf = key.split(".")
    for part in parents:
        node = node[part]
    node[leaf] = value
    try:
        Settings.model_validate(data)
    except ValidationError as exc:
        first = exc.errors(include_input=False, include_url=False)[0]
        loc = ".".join(str(p) for p in first["loc"]) or key
        return f"{loc}: {first['msg']}"
    return None
