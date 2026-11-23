from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from bip.config.store import ConfigStore, LayeredEnviron, load_effective_settings
from bip.core.exceptions import ConfigurationError

TOKEN = "BIP_TELEGRAM_BOT_TOKEN"
URL = "BIP_PROVIDER_ALCHEMY_ETH_URL"
ALL_INTERFACES = "0.0.0.0"  # noqa: S104 - a stored value that must never be applied


@pytest.fixture
def store(tmp_path: Path) -> ConfigStore:
    return ConfigStore(tmp_path / "data" / "config")


def mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


# ---------- secrets ----------


def test_secrets_round_trip_with_private_permissions(store: ConfigStore) -> None:
    store.set_secret(URL, "https://eth-mainnet.g.alchemy.com/v2/abcdef123456", actor="ana")
    assert store.get_secret(URL) == "https://eth-mainnet.g.alchemy.com/v2/abcdef123456"
    meta = store.secret_meta(URL)
    assert meta is not None
    assert meta["updated_by"] == "ana"
    assert "value" not in meta
    assert mode(store.directory) == 0o700
    assert mode(store.directory / "secrets.json") == 0o600
    store.clear_secret(URL, actor="ana")
    assert store.get_secret(URL) is None
    assert store.secret_meta(URL) is None


def test_values_survive_a_new_store_instance(store: ConfigStore) -> None:
    store.set_secret(TOKEN, "123456:ABCdefGHIjklMNOpqrSTUvwxYZ0123456789", actor="ana")
    store.set_setting("alerts.telegram_chat_id", "-1001", actor="ana")
    again = ConfigStore(store.directory)
    assert again.get_secret(TOKEN) == "123456:ABCdefGHIjklMNOpqrSTUvwxYZ0123456789"
    assert again.overrides() == {"alerts": {"telegram_chat_id": "-1001"}}


def test_secret_names_are_validated(store: ConfigStore) -> None:
    with pytest.raises(ConfigurationError):
        store.set_secret("lower_case", "x" * 10, actor="ana")
    with pytest.raises(ConfigurationError, match="empty"):
        store.set_secret(URL, "  ", actor="ana")


def test_a_symlinked_file_is_refused(tmp_path: Path) -> None:
    directory = tmp_path / "config"
    directory.mkdir(mode=0o700)
    target = tmp_path / "elsewhere.json"
    target.write_text("{}")
    (directory / "secrets.json").symlink_to(target)
    store = ConfigStore(directory)
    with pytest.raises(ConfigurationError, match="symlink"):
        store.set_secret(URL, "https://x.example/v2/abcdef", actor="ana")
    assert target.read_text() == "{}"


def test_a_corrupt_file_is_an_error_not_an_empty_store(store: ConfigStore) -> None:
    store.set_secret(URL, "https://x.example/v2/abcdef", actor="ana")
    (store.directory / "secrets.json").write_text("{not json")
    with pytest.raises(ConfigurationError, match=r"secrets\.json"):
        ConfigStore(store.directory).get_secret(URL)


def test_no_temp_file_is_left_behind(store: ConfigStore) -> None:
    store.set_secret(URL, "https://x.example/v2/abcdef", actor="ana")
    store.set_setting("workers.enabled", True, actor="ana")
    assert sorted(p.name for p in store.directory.iterdir()) == ["secrets.json", "settings.json"]


# ---------- layered environment ----------


def test_environment_wins_and_the_store_fills_gaps(store: ConfigStore) -> None:
    store.set_secret(URL, "https://from-store.example/v2/abcdef", actor="ana")
    store.set_secret(TOKEN, "123456:storetokenstoretokenstoretoken", actor="ana")
    env = LayeredEnviron({TOKEN: "123456:envtokenenvtokenenvtoken", "OTHER": "1"}, store)
    assert env[TOKEN] == "123456:envtokenenvtokenenvtoken"
    assert env.get(URL) == "https://from-store.example/v2/abcdef"
    assert env.source(URL) == "setup"
    assert env.source(TOKEN) == "environment"
    assert env.source("BIP_PROVIDER_NOPE_URL") is None
    assert set(env) >= {TOKEN, URL, "OTHER"}


def test_a_file_reference_in_the_environment_hides_the_store(store: ConfigStore) -> None:
    store.set_secret(TOKEN, "123456:storetokenstoretokenstoretoken", actor="ana")
    env = LayeredEnviron({f"{TOKEN}_FILE": "/run/secrets/t"}, store)
    assert TOKEN not in env  # read_secret would otherwise see both and refuse
    assert env.source(TOKEN) == "environment"


def test_store_changes_are_seen_live(store: ConfigStore) -> None:
    env = LayeredEnviron({}, store)
    assert URL not in env
    store.set_secret(URL, "https://x.example/v2/abcdef", actor="ana")
    assert env[URL] == "https://x.example/v2/abcdef"


# ---------- effective settings ----------


def env(monkeypatch: pytest.MonkeyPatch, data: Path, **extra: str) -> None:
    monkeypatch.setenv("BIP_DATA_ROOT", str(data))
    monkeypatch.setenv("BIP_ENV", "test")
    for k, v in extra.items():
        monkeypatch.setenv(k, v)


def test_overrides_apply_but_the_environment_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "data"
    store = ConfigStore(data / "config")
    store.set_setting("workers.enabled", True, actor="ana")
    store.set_setting("alerts.max_attempts", 9, actor="ana")
    env(monkeypatch, data, BIP_ALERTS__MAX_ATTEMPTS="4")
    settings, effective = load_effective_settings()
    assert settings.workers.enabled is True
    assert settings.alerts.max_attempts == 4
    assert effective.applied == {"workers": {"enabled": True}}
    assert effective.overridden_by_environment == ["alerts.max_attempts"]


def test_an_invalid_stored_override_is_reported_not_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "data"
    store = ConfigStore(data / "config")
    store.set_setting("alerts.telegram_enabled", True, actor="ana")  # no chat id
    env(monkeypatch, data)
    settings, effective = load_effective_settings()
    assert settings.alerts.telegram_enabled is False
    assert effective.applied == {}
    assert effective.error is not None
    assert "telegram_chat_id" in effective.error


def test_unknown_stored_fields_are_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = tmp_path / "data"
    store = ConfigStore(data / "config")
    store.directory.mkdir(parents=True, exist_ok=True)
    path = store.directory / "settings.json"
    path.write_text(json.dumps({"overrides": {"api": {"host": ALL_INTERFACES}}, "meta": {}}))
    os.chmod(path, 0o600)
    env(monkeypatch, data)
    settings, effective = load_effective_settings()
    assert settings.api.host == "127.0.0.1"  # not an editable field: never applied
    assert effective.ignored == ["api.host"]


def test_validate_override_reports_the_first_problem(tmp_path: Path) -> None:
    from bip.config import load_settings
    from bip.config.store import validate_override

    base = load_settings(data_root=tmp_path / "data", env="test")
    assert validate_override(base, "workers.enabled", True) is None
    reason = validate_override(base, "alerts.telegram_chat_id", "not a chat")
    assert reason is not None
    assert "telegram_chat_id" in reason
