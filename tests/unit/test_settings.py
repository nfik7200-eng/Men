from __future__ import annotations

from pathlib import Path

import pytest

from bip.config import Environment, Settings, load_settings
from bip.config.settings import unknown_bip_variables
from bip.core.exceptions import ConfigurationError


def test_defaults_need_no_environment() -> None:
    s = load_settings()
    assert s.env is Environment.DEVELOPMENT
    assert s.enabled_chains == ("ethereum", "solana")
    assert s.postgres_dsn is None
    assert s.redis_url is None
    assert int(s.max_data_bytes) == 5 * 1024**3
    assert s.router.live_requests_enabled is False


def test_reads_prefixed_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BIP_ENV", "test")
    monkeypatch.setenv("BIP_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("BIP_MAX_DATA_BYTES", "2GiB")
    monkeypatch.setenv("BIP_ENABLED_CHAINS", "solana, ethereum")
    monkeypatch.setenv("BIP_ROUTER__CROSS_VERIFICATION_SAMPLE_RATE", "0.25")
    s = load_settings()
    assert s.env is Environment.TEST
    assert s.resolved_data_root == tmp_path.resolve()
    assert int(s.max_data_bytes) == 2 * 1024**3
    assert s.enabled_chains == ("solana", "ethereum")
    assert s.router.cross_verification_sample_rate == 0.25


def test_temp_dir_defaults_under_data_root(tmp_path: Path) -> None:
    s = load_settings(data_root=tmp_path)
    assert s.resolved_temp_dir == (tmp_path / "tmp").resolve()


@pytest.mark.parametrize(
    ("var", "value", "fragment"),
    [
        ("BIP_ENV", "staging", "BIP_ENV"),
        ("BIP_LOG_LEVEL", "VERBOSE", "BIP_LOG_LEVEL"),
        ("BIP_ENABLED_CHAINS", "ethereum,dogechain", "BIP_ENABLED_CHAINS"),
        ("BIP_ENABLED_CHAINS", "ethereum,ethereum", "duplicates"),
        ("BIP_ENABLED_CHAINS", " , ", "at least one chain"),
        ("BIP_MAX_DATA_BYTES", "lots", "BIP_MAX_DATA_BYTES"),
        ("BIP_MIN_FREE_RATIO", "0.9", "BIP_MIN_FREE_RATIO"),
        ("BIP_ROUTER__CROSS_VERIFICATION_SAMPLE_RATE", "2", "BIP_ROUTER__CROSS_VERIFICATION"),
    ],
)
def test_invalid_values_raise_readable_errors(
    monkeypatch: pytest.MonkeyPatch, var: str, value: str, fragment: str
) -> None:
    monkeypatch.setenv(var, value)
    with pytest.raises(ConfigurationError) as info:
        load_settings()
    assert fragment in str(info.value)
    assert str(info.value).startswith("invalid configuration:")


def test_production_requires_absolute_data_root() -> None:
    with pytest.raises(ConfigurationError, match="absolute"):
        load_settings(env="production", data_root=Path("relative/dir"))


def test_production_requires_json_logs(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="json"):
        load_settings(env="production", data_root=tmp_path, log_format="text")


def test_unknown_override_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="BIP_DATAROOT"):
        load_settings(dataroot="/tmp/x")


def test_secrets_hidden_in_repr_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    dsn = "postgresql://bip:hunter2-very-secret@db.example.com:5432/bip"
    monkeypatch.setenv("BIP_POSTGRES_DSN", dsn)
    s = load_settings()
    assert s.postgres_dsn is not None
    assert s.postgres_dsn.get_secret_value() == dsn
    for rendered in (repr(s), str(s), s.model_dump_json()):
        assert "hunter2" not in rendered

    bad = "mysql://root:topsecret-password@db.example.com/x"
    monkeypatch.setenv("BIP_POSTGRES_DSN", bad)
    with pytest.raises(ConfigurationError) as info:
        load_settings()
    assert "topsecret" not in str(info.value)
    assert "BIP_POSTGRES_DSN" in str(info.value)


def test_redis_url_scheme_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIP_REDIS_URL", "http://cache.example.com")
    with pytest.raises(ConfigurationError, match="BIP_REDIS_URL"):
        load_settings()


def test_settings_are_immutable() -> None:
    s = Settings()
    with pytest.raises(ValueError, match="frozen"):
        s.log_level = "DEBUG"  # type: ignore[misc]


def test_api_and_database_defaults_are_safe() -> None:
    s = load_settings()
    assert s.api.host == "127.0.0.1"
    assert s.api.cors_origins == ("http://localhost:5173", "http://127.0.0.1:5173")
    assert s.api.max_request_bytes == 64 * 1024
    assert s.database.filename == "bip.sqlite3"


def test_cors_origins_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIP_API__CORS_ORIGINS", "https://bip.example.org, http://localhost:3000")
    assert load_settings().api.cors_origins == ("https://bip.example.org", "http://localhost:3000")


@pytest.mark.parametrize(
    "origins",
    ["*", "https://*.example.org", "http://evil.example.org", "https://bip.example.org/"],
)
def test_unsafe_cors_origins_rejected(monkeypatch: pytest.MonkeyPatch, origins: str) -> None:
    monkeypatch.setenv("BIP_API__CORS_ORIGINS", origins)
    with pytest.raises(ConfigurationError, match="BIP_API__CORS_ORIGINS"):
        load_settings()


@pytest.mark.parametrize(
    "filename", ["../escape.sqlite3", "/abs/bip.sqlite3", "bip.db", "a/b.sqlite3"]
)
def test_database_filename_cannot_be_a_path(monkeypatch: pytest.MonkeyPatch, filename: str) -> None:
    monkeypatch.setenv("BIP_DATABASE__FILENAME", filename)
    with pytest.raises(ConfigurationError, match="BIP_DATABASE__FILENAME"):
        load_settings()


def test_misspelled_top_level_variable_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIP_DATAROOT", "/tmp/x")
    with pytest.raises(ConfigurationError, match="BIP_DATAROOT"):
        load_settings()


def test_single_underscore_typo_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIP_AUTH_MODE", "required")
    with pytest.raises(ConfigurationError, match="BIP_AUTH_MODE"):
        load_settings()


def test_unknown_nested_key_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIP_AUTH__MODEX", "required")
    with pytest.raises(ConfigurationError):
        load_settings()


def test_error_never_contains_the_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIP_DATAROOT", "s3cr3t-value")
    with pytest.raises(ConfigurationError) as exc:
        load_settings()
    assert "s3cr3t-value" not in str(exc.value)


def test_tool_variables_are_not_refused() -> None:

    env = {
        "BIP_PROVIDER_ALCHEMY_ETH_URL": "x",
        "BIP_LIVE_RPC": "1",
        "BIP_NEW_PASSWORD_FILE": "f",
        "BIP_AI_API_KEY": "k",
        "BIP_DATA_ROOT": "/d",
        "BIP_AUTH__MODE": "disabled",
        "PATH": "/bin",
    }
    assert unknown_bip_variables(env, "BIP_AI_API_KEY") == []


def test_frontend_and_secret_file_variables_are_not_refused() -> None:
    env = {
        "BIP_API_URL": "http://127.0.0.1:8000",
        "BIP_E2E_KEEP": "1",
        "BIP_E2E_REAL_NETWORK": "1",
        "BIP_AI_API_KEY_FILE": "/run/secrets/k",
        "BIP_POSTGRES_DSN_FILE": "/run/secrets/dsn",
    }
    assert unknown_bip_variables(env, "BIP_AI_API_KEY") == []


def test_file_suffix_does_not_excuse_an_unknown_stem() -> None:
    assert unknown_bip_variables({"BIP_DATAROOT_FILE": "/x"}, "BIP_AI_API_KEY") == [
        "BIP_DATAROOT_FILE"
    ]
