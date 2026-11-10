"""Check a BIP deployment configuration before starting it: ``python -m bip.tools.preflight``.

Reads the environment (or the file given with ``--env-file``, a ``KEY=VALUE`` list such as
``/etc/bip/bip.env``), builds the real settings, and reports ``PASS``, ``WARN`` or ``FAIL`` for each
condition a reachable host should meet. It contacts nothing, changes nothing and never prints a
setting's value, only the setting's name and the reason. Exit status: 0 when nothing failed,
1 when something failed, 2 when the configuration is invalid.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import sqlite3
import stat
import sys
from collections.abc import Generator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

from bip.config.settings import Environment, Settings, load_settings
from bip.core.exceptions import BIPError

LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    level: str  # "PASS" | "WARN" | "FAIL"
    message: str


def parse_env_file(text: str) -> dict[str, str]:
    """``KEY=VALUE`` lines; ``#`` comments and blank lines ignored; one pair of quotes stripped."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


@contextlib.contextmanager
def _environment(values: Mapping[str, str]) -> Generator[None]:
    """Settings read ``BIP_*`` from the process environment; use exactly ``values`` for that."""
    kept = {k: v for k, v in os.environ.items() if not k.startswith("BIP_")}
    with mock.patch.dict(os.environ, {**kept, **values}, clear=True):
        yield


def settings_from(values: Mapping[str, str]) -> Settings:
    with _environment(values):
        return load_settings()


def _db_has_admin(db: Path) -> bool | None:
    try:
        with contextlib.closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as conn:
            row = conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()
        return bool(row and row[0] > 0)
    except sqlite3.Error:
        return None


def run_checks(settings: Settings, *, env_file: Path | None = None) -> list[Check]:
    s = settings
    out: list[Check] = []

    def add(name: str, ok: bool, good: str, bad: str, level_if_bad: str = "FAIL") -> None:
        out.append(Check(name, "PASS" if ok else level_if_bad, good if ok else bad))

    add(
        "environment",
        s.env is Environment.PRODUCTION,
        "BIP_ENV=production",
        "BIP_ENV is not production (production also enforces an absolute data root and JSON logs)",
    )
    add(
        "authentication",
        s.auth.mode == "required",
        "BIP_AUTH__MODE=required",
        "BIP_AUTH__MODE is not 'required': every endpoint, including ones that contact "
        "providers, would be open to whoever can connect",
    )
    add(
        "insecure_bind",
        not s.auth.allow_insecure_bind,
        "BIP_AUTH__ALLOW_INSECURE_BIND is off",
        "BIP_AUTH__ALLOW_INSECURE_BIND=true must never be used on a reachable host",
    )
    add(
        "cookie_secure",
        s.auth.cookie_secure,
        "session cookie is Secure (HTTPS only)",
        "BIP_AUTH__COOKIE_SECURE is off: the session cookie would travel over plain HTTP",
    )
    loopback = s.api.host in LOOPBACK
    add(
        "bind_address",
        loopback,
        "API binds to loopback; only the reverse proxy can reach it",
        f"BIP_API__HOST is {s.api.host!r}: reachable without the proxy's TLS; prefer 127.0.0.1",
        "WARN",
    )
    if s.auth.trust_proxy_headers and not loopback:
        out.append(
            Check(
                "trust_proxy_headers",
                "FAIL",
                "BIP_AUTH__TRUST_PROXY_HEADERS is on while the API is reachable directly: "
                "a client could spoof its address and dodge throttling",
            )
        )
    else:
        out.append(
            Check(
                "trust_proxy_headers",
                "PASS" if s.auth.trust_proxy_headers else "WARN",
                "proxy headers trusted behind a loopback bind"
                if s.auth.trust_proxy_headers
                else "BIP_AUTH__TRUST_PROXY_HEADERS is off: BIP then ignores X-Forwarded-For in "
                "its own throttling and audit log (the HTTP server may still rewrite the peer "
                "address for loopback callers; see deploy/README.md, known limits)",
            )
        )
    bad_origins = [
        o
        for o in s.api.cors_origins
        if o == "*"
        or (urlsplit(o).scheme == "http" and (urlsplit(o).hostname or "") not in LOOPBACK)
    ]
    add(
        "cors_origins",
        not bad_origins,
        "CORS origins are HTTPS or loopback",
        f"{len(bad_origins)} CORS origin(s) use plain HTTP on a non-loopback host",
    )
    collecting = [
        name
        for name, on in (
            ("BIP_WORKERS__COLLECTOR_ENABLED", s.workers.collector_enabled),
            ("BIP_WORKERS__SOLANA_COLLECTOR_ENABLED", s.workers.solana_collector_enabled),
            ("BIP_WORKERS__WATCHLIST_REFRESH_ENABLED", s.workers.watchlist_refresh_enabled),
        )
        if on
    ]
    out.append(
        Check(
            "provider_contact",
            "WARN" if collecting else "PASS",
            f"scheduled provider use is on ({', '.join(collecting)}): confirm each endpoint is "
            "free and its limits are respected"
            if collecting
            else "no scheduled job contacts a provider",
        )
    )
    add(
        "backups",
        s.workers.enabled and s.workers.backup_enabled,
        "scheduled verified backups are on",
        "scheduled backups are off (BIP_WORKERS__ENABLED and BIP_WORKERS__BACKUP_ENABLED)",
        "WARN",
    )
    root = s.data_root
    add(
        "data_root_absolute",
        root.is_absolute(),
        "data root is an absolute path",
        "data root is relative",
    )
    if root.exists():
        add(
            "data_root_writable",
            os.access(root, os.W_OK),
            "data root is writable",
            "data root is not writable",
        )
        free = shutil.disk_usage(root).free
        floor = int(s.min_free_bytes)
        need = floor + int(s.max_data_bytes)
        if free < floor:
            out.append(
                Check("disk_space", "FAIL", f"free space {free >> 20} MiB is below the write floor")
            )
        elif free < need:
            out.append(
                Check(
                    "disk_space",
                    "WARN",
                    f"free space {free >> 20} MiB is below cap + floor ({need >> 20} MiB); "
                    "BIP would stop writing before reaching its cap",
                )
            )
        else:
            out.append(
                Check("disk_space", "PASS", f"{free >> 20} MiB free covers the cap and floor")
            )
        db = root / "operational" / s.database.filename
        if db.exists():
            has_admin = _db_has_admin(db)
            out.append(
                Check(
                    "administrator",
                    "PASS" if has_admin else "WARN",
                    "an administrator exists"
                    if has_admin
                    else "no administrator found; create one with python -m bip.tools.users",
                )
            )
    else:
        out.append(
            Check(
                "data_root_exists", "WARN", "data root does not exist yet (created on first start)"
            )
        )
    if env_file is not None and env_file.exists():
        mode = stat.S_IMODE(env_file.stat().st_mode)
        add(
            "env_file_permissions",
            mode & 0o027 == 0,
            f"env file is not group-writable or world-accessible (mode {mode:o})",
            f"env file mode {mode:o} is group-writable or world-readable; use 640 or stricter, "
            "owned by root",
        )
    return out


def _say(message: str, *, error: bool = False) -> None:
    print(message, file=sys.stderr if error else sys.stdout)


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check a BIP deployment configuration.")
    ap.add_argument("--env-file", type=Path, help="KEY=VALUE file such as /etc/bip/bip.env")
    ap.add_argument(
        "--use-environment",
        action="store_true",
        help="judge the process environment only (as systemd passes it); use --env-file just for "
        "the file-permission check. The service unit uses this so the check and the service "
        "cannot disagree about what the file says.",
    )
    args = ap.parse_args(argv)
    values = {
        k: v
        for k, v in (environ if environ is not None else os.environ).items()
        if k.startswith("BIP_")
    }
    if args.env_file is not None and not args.use_environment:
        try:
            values.update(parse_env_file(args.env_file.read_text(encoding="utf-8")))
        except OSError:
            _say("FAIL  env_file: cannot read the file", error=True)
            return 2
    try:
        settings = settings_from(values)
    except BIPError as exc:
        _say(f"FAIL  configuration: {exc}", error=True)
        return 2
    checks = run_checks(settings, env_file=args.env_file)
    for c in checks:
        _say(f"{c.level:<5} {c.name}: {c.message}")
    failed = sum(c.level == "FAIL" for c in checks)
    warned = sum(c.level == "WARN" for c in checks)
    _say(f"\n{len(checks)} checks: {failed} failed, {warned} warning(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
