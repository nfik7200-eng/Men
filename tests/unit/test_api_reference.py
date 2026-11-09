"""The committed API reference must match what the code generates."""

from __future__ import annotations

from bip.api.access import required_role
from bip.tools.api_contract import TARGET, build_reference, main


def test_reference_is_up_to_date() -> None:
    assert TARGET.exists(), "run: python -m bip.tools.api_contract"
    assert main(["--check"]) == 0, (
        "docs/API_REFERENCE.md is stale: python -m bip.tools.api_contract"
    )


def test_every_documented_route_has_a_role_and_mutations_are_never_public() -> None:
    text = build_reference()
    for line in text.splitlines():
        if line.startswith("| ") and line.split("|")[1].strip() in {
            "POST",
            "PATCH",
            "DELETE",
            "PUT",
        }:
            method, path, role = (c.strip(" `") for c in line.split("|")[1:4])
            if role == "public":
                assert path.removeprefix("/api/v1") in {"/auth/login"}, f"{method} {path} is public"
            assert role in {"public", "viewer", "analyst", "admin"}
            assert role == (required_role(method, path.removeprefix("/api/v1")) or "public")
