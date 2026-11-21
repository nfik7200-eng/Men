"""The deployment pre-flight check fails closed on unsafe settings and never prints values."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from bip.tools.preflight import Check, main, parse_env_file, run_checks, settings_from

SYNTHETIC_DSN_PASSWORD = "synthetic-pw-do-not-print"


def safe(tmp_path: Path, **over: str) -> dict[str, str]:
    root = tmp_path / "data"
    root.mkdir(exist_ok=True)
    return {
        "BIP_ENV": "production",
        "BIP_DATA_ROOT": str(root),
        "BIP_LOG_FORMAT": "json",
        "BIP_AUTH__MODE": "required",
        "BIP_AUTH__COOKIE_SECURE": "true",
        "BIP_AUTH__TRUST_PROXY_HEADERS": "true",
        "BIP_API__CORS_ORIGINS": "https://bip.example.org",
        "BIP_MIN_FREE_BYTES": "1MiB",
        "BIP_MAX_DATA_BYTES": "1MiB",
        "BIP_WORKERS__ENABLED": "true",
        "BIP_WORKERS__BACKUP_ENABLED": "true",
        **over,
    }


def levels(checks: list[Check]) -> dict[str, str]:
    return {c.name: c.level for c in checks}


def test_a_safe_production_configuration_has_no_failures(tmp_path: Path) -> None:
    checks = run_checks(settings_from(safe(tmp_path)))
    assert [c for c in checks if c.level == "FAIL"] == []
    assert levels(checks)["provider_contact"] == "PASS"
    assert levels(checks)["backups"] == "PASS"


@pytest.mark.parametrize(
    ("over", "check"),
    [
        ({"BIP_AUTH__MODE": "disabled"}, "authentication"),
        ({"BIP_AUTH__COOKIE_SECURE": "false"}, "cookie_secure"),
        ({"BIP_AUTH__ALLOW_INSECURE_BIND": "true"}, "insecure_bind"),
        (
            {"BIP_API__HOST": "10.0.0.5", "BIP_AUTH__TRUST_PROXY_HEADERS": "true"},
            "trust_proxy_headers",
        ),
        ({"BIP_ENV": "development"}, "environment"),
    ],
)
def test_unsafe_settings_fail_closed(tmp_path: Path, over: dict[str, str], check: str) -> None:
    checks = run_checks(settings_from(safe(tmp_path, **over)))
    assert levels(checks)[check] == "FAIL"


def test_scheduled_provider_use_is_a_warning_and_backups_off_is_too(tmp_path: Path) -> None:
    values = safe(
        tmp_path,
        BIP_WORKERS__COLLECTOR_ENABLED="true",
        BIP_WORKERS__BACKUP_ENABLED="false",
    )
    checks = levels(run_checks(settings_from(values)))
    assert checks["provider_contact"] == "WARN"
    assert checks["backups"] == "WARN"


def test_env_file_permissions_and_parsing(tmp_path: Path) -> None:
    f = tmp_path / "bip.env"
    f.write_text('# comment\nBIP_ENV="production"\n\nBIP_LOG_LEVEL=INFO\nnot a pair\n')
    assert parse_env_file(f.read_text()) == {"BIP_ENV": "production", "BIP_LOG_LEVEL": "INFO"}
    settings = settings_from(safe(tmp_path))
    f.chmod(0o644)
    assert levels(run_checks(settings, env_file=f))["env_file_permissions"] == "FAIL"
    f.chmod(0o640)
    assert levels(run_checks(settings, env_file=f))["env_file_permissions"] == "PASS"


def test_main_reports_without_printing_values_and_sets_the_exit_status(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = tmp_path / "bip.env"
    values = safe(
        tmp_path,
        BIP_AUTH__MODE="disabled",
        BIP_POSTGRES_DSN=f"postgresql://u:{SYNTHETIC_DSN_PASSWORD}@h/db",
    )
    env.write_text("\n".join(f"{k}={v}" for k, v in values.items()))
    env.chmod(0o600)
    assert main(["--env-file", str(env)], environ={}) == 1
    out = capsys.readouterr().out
    assert "FAIL  authentication" in out
    assert SYNTHETIC_DSN_PASSWORD not in out
    assert str(tmp_path) not in out or "data root" in out  # paths only appear in no check text
    good = tmp_path / "good.env"
    good.write_text("\n".join(f"{k}={v}" for k, v in safe(tmp_path).items()))
    good.chmod(0o600)
    assert main(["--env-file", str(good)], environ={}) == 0
    assert "BIP_AUTH__MODE" not in os.environ  # the check never leaks into the process


def test_an_invalid_configuration_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = tmp_path / "bad.env"
    env.write_text("BIP_API__PORT=notaport\n")
    assert main(["--env-file", str(env)], environ={}) == 2
    assert "FAIL  configuration" in capsys.readouterr().err


def test_plain_http_cors_origins_are_refused_when_the_settings_load(tmp_path: Path) -> None:
    from bip.core.exceptions import ConfigurationError

    with pytest.raises(ConfigurationError):
        settings_from(safe(tmp_path, BIP_API__CORS_ORIGINS="http://bip.example.org"))


def test_the_shipped_production_template_loads_and_passes(tmp_path: Path) -> None:
    """deploy/bip.env.production.example must stay valid against the real settings."""
    template = Path(__file__).parents[2] / "deploy" / "bip.env.production.example"
    values = parse_env_file(template.read_text(encoding="utf-8"))
    root = tmp_path / "data"
    root.mkdir()
    values.update({"BIP_DATA_ROOT": str(root), "BIP_MIN_FREE_BYTES": "1MiB"})
    checks = run_checks(settings_from(values))
    assert [c for c in checks if c.level == "FAIL"] == []
    assert levels(checks)["provider_contact"] == "PASS"  # collection is off in the template


def test_a_group_writable_env_file_fails(tmp_path: Path) -> None:
    f = tmp_path / "bip.env"
    f.write_text("BIP_ENV=production\n")
    settings = settings_from(safe(tmp_path))
    f.chmod(0o660)
    assert levels(run_checks(settings, env_file=f))["env_file_permissions"] == "FAIL"
    f.chmod(0o640)
    assert levels(run_checks(settings, env_file=f))["env_file_permissions"] == "PASS"


def test_use_environment_judges_what_the_process_has_not_what_the_file_says(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The unit passes the file to systemd and to the check; they must not disagree."""
    env = tmp_path / "bip.env"
    env.write_text("\n".join(f"{k}={v}" for k, v in safe(tmp_path).items()))  # says: required
    env.chmod(0o600)
    running = safe(tmp_path, BIP_AUTH__MODE="disabled")  # but the service would run with auth off
    assert main(["--use-environment", "--env-file", str(env)], environ=running) == 1
    assert "FAIL  authentication" in capsys.readouterr().out
    assert main(["--env-file", str(env)], environ=running) == 0  # manual mode reads the file
