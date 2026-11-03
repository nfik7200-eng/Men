from __future__ import annotations

from collections.abc import Iterable

import pytest

from bip.core.exceptions import UnsafeURLError
from bip.security.url_validation import (
    MAX_REDIRECTS,
    check_ip,
    validate_redirect,
    validate_url,
)


def fake_resolver(mapping: dict[str, list[str]]):
    def resolve(host: str, port: int) -> Iterable[str]:
        del port
        if host not in mapping:
            raise OSError("synthetic NXDOMAIN")
        return mapping[host]

    return resolve


def test_accepts_public_https_and_wss() -> None:
    v = validate_url("https://rpc.example-provider.com/v2/key?x=1")
    assert (v.scheme, v.host, v.port) == ("https", "rpc.example-provider.com", 443)
    assert validate_url("wss://ws.example-provider.com:8443/").port == 8443
    assert validate_url("https://8.8.8.8/").host == "8.8.8.8"
    assert validate_url("https://[2001:4860:4860::8888]/").host == "2001:4860:4860::8888"


@pytest.mark.parametrize(
    "url",
    [
        "http://rpc.example.com",
        "ftp://example.com/x",
        "file:///etc/passwd",
        "gopher://x.com",
        "javascript:alert(1)",
        "//example.com/x",
        "rpc.example.com",
    ],
)
def test_disallowed_schemes(url: str) -> None:
    with pytest.raises(UnsafeURLError, match="scheme"):
        validate_url(url)


def test_http_allowed_only_when_explicit() -> None:
    assert validate_url("http://rpc.example.com", allowed_schemes={"http"}).port == 80


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/",
        "https://10.0.0.5/",
        "https://172.16.3.4/",
        "https://192.168.1.1/",
        "https://169.254.169.254/latest/meta-data/",
        "https://100.64.0.1/",
        "https://0.0.0.0/",
        "https://224.0.0.1/",
        "https://255.255.255.255/",
        "https://198.18.0.1/",
        "https://[::1]/",
        "https://[::]/",
        "https://[fe80::1]/",
        "https://[fd00:ec2::254]/",
        "https://[fc00::1]/",
        "https://[::ffff:127.0.0.1]/",
        "https://[::ffff:169.254.169.254]/",
        "https://[2002:7f00:1::]/",
        "https://[64:ff9b::a00:1]/",
        "https://[ff02::1]/",
    ],
)
def test_rejects_non_public_ip_literals(url: str) -> None:
    with pytest.raises(UnsafeURLError):
        validate_url(url)


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "LOCALHOST",
        "api.localhost",
        "printer.local",
        "metadata.google.internal",
        "svc.cluster.internal",
        "router.lan",
        "nas.home.arpa",
        "intranet",
        "x.corp",
    ],
)
def test_rejects_internal_hostnames(host: str) -> None:
    with pytest.raises(UnsafeURLError):
        validate_url(f"https://{host}/")


@pytest.mark.parametrize("host", ["2130706433", "0x7f000001", "0x7f.1", "127.1", "017700000001"])
def test_rejects_ambiguous_numeric_hosts(host: str) -> None:
    with pytest.raises(UnsafeURLError):
        validate_url(f"https://{host}/")


@pytest.mark.parametrize(
    ("url", "match"),
    [
        ("", "empty"),
        ("https://user:pass@rpc.example.com/", "credentials"),
        ("https://rpc.example.com@evil.example.net/", "credentials"),
        ("https:///nohost", "no host"),
        ("https://rpc.example.com:99999/", "malformed|range"),
        ("https://rpc.example.com:0/", "range"),
        ("https://rpc.exa mple.com/", "whitespace"),
        ("https://rpc.example.com/\n", "whitespace"),
        ("https://rpc.example.com\\@evil.com/", "backslash"),
        ("https://[fe80::1%25eth0]/", "zone"),
        ("https://-bad-.example.com/", "valid DNS"),
        ("https://" + "a" * 2100 + ".com", "longer"),
        ("https://[not-an-ip]/", "malformed"),
    ],
)
def test_malformed_urls(url: str, match: str) -> None:
    with pytest.raises(UnsafeURLError, match=match):
        validate_url(url)


def test_idn_hostnames_are_punycoded() -> None:
    assert validate_url("https://bücher.de/").host == "xn--bcher-kva.de"


def test_allow_private_is_explicit_and_bounded() -> None:
    assert validate_url("https://127.0.0.1:8545/", allow_private=True).port == 8545
    assert validate_url("https://localhost:8545/", allow_private=True).host == "localhost"
    with pytest.raises(UnsafeURLError):
        validate_url("https://224.0.0.1/", allow_private=True)


def test_resolution_checks_every_address() -> None:
    ok = fake_resolver({"rpc.example.com": ["93.184.216.34", "2606:2800:220:1::1"]})
    v = validate_url("https://rpc.example.com/", resolve=True, resolver=ok)
    assert v.resolved_ips == ("93.184.216.34", "2606:2800:220:1::1")

    rebind = fake_resolver({"rpc.example.com": ["93.184.216.34", "10.0.0.7"]})
    with pytest.raises(UnsafeURLError, match="private"):
        validate_url("https://rpc.example.com/", resolve=True, resolver=rebind)

    with pytest.raises(UnsafeURLError, match="cannot resolve"):
        validate_url("https://missing.example.com/", resolve=True, resolver=fake_resolver({}))

    empty = fake_resolver({"rpc.example.com": []})
    with pytest.raises(UnsafeURLError, match="no addresses"):
        validate_url("https://rpc.example.com/", resolve=True, resolver=empty)


def test_no_resolution_by_default() -> None:
    def explode(host: str, port: int) -> Iterable[str]:
        raise AssertionError("resolver must not be called")

    assert validate_url("https://rpc.example.com/", resolver=explode).resolved_ips == ()


def test_redirect_validation() -> None:
    base = "https://rpc.example.com/a/b"
    assert validate_redirect(base, "/c", hop=1).url == "https://rpc.example.com/c"
    assert validate_redirect(base, "https://other.example.org/x", hop=2).host == "other.example.org"
    with pytest.raises(UnsafeURLError, match="too many"):
        validate_redirect(base, "/c", hop=MAX_REDIRECTS + 1)
    with pytest.raises(UnsafeURLError):
        validate_redirect(base, "http://rpc.example.com/c", hop=1)
    with pytest.raises(UnsafeURLError, match="downgrade"):
        validate_redirect(
            base, "http://rpc.example.com/c", hop=1, allowed_schemes={"https", "http"}
        )
    with pytest.raises(UnsafeURLError):
        validate_redirect(base, "https://169.254.169.254/", hop=1)


def test_check_ip_rejects_garbage() -> None:
    with pytest.raises(UnsafeURLError, match="not an IP"):
        check_ip("999.1.1.1")


@pytest.mark.parametrize(
    "url",
    [
        "https://%6c%6fcalhost/",
        "https://\uff11\uff12\uff17.\uff10.\uff10.\uff11/",
        "https://localhost./",
        "https://127.0.0.1.nip.local/",
    ],
)
def test_encoding_tricks_rejected(url: str) -> None:
    with pytest.raises(UnsafeURLError):
        validate_url(url)
