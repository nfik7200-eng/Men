from __future__ import annotations

import io
import json
import logging
import time
from pathlib import Path

import pytest
from pydantic import SecretStr

from bip.core.exceptions import SecretError
from bip.core.logging import configure_logging, get_logger
from bip.security.secrets import (
    MAX_REDACT_CHARS,
    REDACTED,
    is_sensitive_key,
    read_secret,
    redact_text,
    redact_value,
    register_secret,
)

_FAKE_KEY = "0123456789" + "abcdefghij"


@pytest.mark.parametrize(
    ("raw", "leaked"),
    [
        (
            "connecting to https://eth-mainnet.example.com/v2/Ab3dE9fGh1JkLmNoPqRs7Tu",
            "Ab3dE9fGh1JkLmNoPqRs7Tu",
        ),
        ("GET https://rpc.example.com/?api_key=sk_live_abc123XYZ&chain=1", "sk_live_abc123XYZ"),
        ("url=https://rpc.example.com/path?token=tok_998877aa", "tok_998877aa"),
        ("postgresql://bip:p4ssw0rd!@db.example.com/bip", "p4ssw0rd!"),
        ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig", "eyJhbGciOiJIUzI1NiJ9"),
        ('config {"password": "correct-horse-battery"}', "correct-horse-battery"),
        ("db_password=s3cr3t-value", "s3cr3t-value"),
        # Synthetic key assembled at runtime so secret scanners do not flag the fixture.
        ("X-API-KEY: " + _FAKE_KEY, _FAKE_KEY),
    ],
)
def test_patterns_redact_common_credential_shapes(raw: str, leaked: str) -> None:
    out = redact_text(raw)
    assert leaked not in out
    assert REDACTED in out


def test_blockchain_identifiers_stay_readable() -> None:
    tx = "0x" + "ab" * 32
    addr = "0x" + "11" * 20
    text = f"tx_hash={tx} token_address={addr} https://etherscan.io/tx/{tx}"
    assert redact_text(text) == text


def test_registered_exact_values_are_redacted_anywhere() -> None:
    register_secret(SecretStr("abc"))  # too short: ignored
    register_secret("plainsecretvalue42")
    assert redact_text("error near plainsecretvalue42 in body") == f"error near {REDACTED} in body"
    assert redact_text("abc stays") == "abc stays"


def test_redact_value_masks_sensitive_keys_recursively() -> None:
    data = {
        "provider": "drpc",
        "api_key": "whatever",
        "nested": {"db_password": "x", "token_address": "0x" + "11" * 20},
        "items": ["ok", SecretStr("hidden")],
    }
    out = redact_value(data)
    assert isinstance(out, dict)
    assert out["provider"] == "drpc"
    assert out["api_key"] == REDACTED
    assert out["nested"] == {"db_password": REDACTED, "token_address": "0x" + "11" * 20}
    assert out["items"] == ["ok", REDACTED]


@pytest.mark.parametrize(
    ("key", "sensitive"),
    [
        ("api_key", True),
        ("X-Api-Key", True),
        ("db_password", True),
        ("postgres_dsn", True),
        ("token_address", False),
        ("block_number", False),
        ("provider", False),
    ],
)
def test_sensitive_key_detection(key: str, sensitive: bool) -> None:
    assert is_sensitive_key(key) is sensitive


def test_read_secret_from_env_and_file(tmp_path: Path) -> None:
    secret_file = tmp_path / "key.txt"
    secret_file.write_text("file-secret-value\n", encoding="utf-8")
    assert read_secret("BIP_TEST_KEY", {}) is None
    direct = read_secret("BIP_TEST_KEY", {"BIP_TEST_KEY": "env-secret-value"})
    assert direct is not None
    assert direct.get_secret_value() == "env-secret-value"
    from_file = read_secret("BIP_TEST_KEY", {"BIP_TEST_KEY_FILE": str(secret_file)})
    assert from_file is not None
    assert from_file.get_secret_value() == "file-secret-value"
    assert "file-secret-value" not in redact_text("leak file-secret-value")


@pytest.mark.parametrize(
    ("env", "match"),
    [
        ({"BIP_K": "a1b2c3d4", "BIP_K_FILE": "/x"}, "only one"),
        ({"BIP_K": ""}, "empty"),
        ({"BIP_K_FILE": "/nonexistent/definitely/missing"}, "cannot read"),
    ],
)
def test_read_secret_errors_do_not_leak(env: dict[str, str], match: str) -> None:
    with pytest.raises(SecretError, match=match) as info:
        read_secret("BIP_K", env)
    assert "a1b2c3d4" not in str(info.value)


def test_invalid_secret_name() -> None:
    with pytest.raises(SecretError, match="UPPER_SNAKE_CASE"):
        read_secret("lower-case")


def test_logging_redacts_message_args_extra_and_exceptions() -> None:
    stream = io.StringIO()
    configure_logging("DEBUG", "json", stream=stream)
    register_secret("registered-secret-123")
    log = get_logger("test")
    log.info("calling %s", "https://rpc.example.com/v3/Zz9Yy8Xx7Ww6Vv5Uu4Tt3S")
    log.warning(
        "extra", extra={"api_key": "k-123456", "url": "https://x.example.com/?token=t0k3n123"}
    )
    try:
        raise RuntimeError("failed with registered-secret-123")
    except RuntimeError:
        log.exception("boom")

    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    blob = json.dumps(lines)
    for leaked in ("Zz9Yy8Xx7Ww6Vv5Uu4Tt3S", "k-123456", "t0k3n123", "registered-secret-123"):
        assert leaked not in blob
    assert lines[0]["logger"] == "bip.test"
    assert "exception" in lines[2]
    logging.getLogger("bip").handlers.clear()


@pytest.mark.parametrize(
    "hostile",
    [
        "https://h.example.com" + "/a1" * 5000 + " key=" + "x" * 50_000,
        "a://" * 20_000,
        "a-" * 50_000,
        "key-" * 30_000,
        "https://" + "@" * 50_000,
        "bearer " * 20_000,
        "x" * 1_000_000,
    ],
    ids=["mixed", "schemes", "hyphens", "key-hyphens", "at-signs", "bearer", "huge"],
)
def test_redaction_cost_is_bounded_on_hostile_input(hostile: str) -> None:
    start = time.perf_counter()
    out = redact_text(hostile)
    assert time.perf_counter() - start < 3.0
    assert len(out) <= MAX_REDACT_CHARS + len("[TRUNCATED]") + 64 * len(REDACTED)


@pytest.mark.parametrize(
    ("raw", "leaked"),
    [
        ("postgresql://user:p@ss@db.example.com/bip", "ss@db"),
        ("redis://:ab/cd@cache.example.com/0", "ab/cd"),
    ],
)
def test_userinfo_with_at_or_slash_in_password(raw: str, leaked: str) -> None:
    out = redact_text(raw)
    assert leaked not in out
    assert out.endswith(("@db.example.com/bip", "@cache.example.com/0"))


def test_logging_redacts_non_string_extra_values() -> None:
    stream = io.StringIO()
    configure_logging("INFO", "json", stream=stream)
    error = ValueError("GET https://rpc.example.com/v2/AbCd1234AbCd1234AbCd1234 failed")
    get_logger("t").error("fail", extra={"error": error, "count": 3, "ok": True})
    record = json.loads(stream.getvalue())
    assert "AbCd1234AbCd1234AbCd1234" not in record["error"]
    assert record["count"] == 3
    assert record["ok"] is True
    logging.getLogger("bip").handlers.clear()
