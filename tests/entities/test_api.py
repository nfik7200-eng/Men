"""The entity endpoints over HTTP: validation, status codes, roles and attribution."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.discovery.seed import E18, NOW, Seed, eth_addr, eth_tx
from tests.security.helpers import API, PASSWORD, Rig


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    r = Rig(tmp_path, mode="disabled")
    yield r
    r.dispose()


def seed_funding(rig: Rig) -> None:
    seed = Seed(rig.engine)
    seed.tx(eth_tx(1), eth_addr(0xD1), NOW - timedelta(hours=5), created=eth_addr(0xC1))
    for i in range(3):
        when = NOW - timedelta(hours=4 - i)
        seed.tx(eth_tx(10 + i), eth_addr(0xA1), when, to=eth_addr(0xB1 + i))
        seed.transfer(eth_tx(10 + i), eth_addr(0xA1), eth_addr(0xB1 + i), E18, when)


def test_search_validation_and_results(rig: Rig) -> None:
    seed_funding(rig)
    with TestClient(rig.app) as c:
        assert c.get(f"{API}/search", params={"q": "ab"}).status_code == 422
        assert c.get(f"{API}/search", params={"q": "x" * 101}).status_code == 422
        assert (
            c.get(f"{API}/search", params={"q": eth_addr(0xC1)}).json()["items"][0]["kind"]
            == "contract"
        )
        assert c.get(f"{API}/search", params={"q": eth_tx(10), "limit": 0}).status_code == 422
        miss = c.get(f"{API}/search", params={"q": "nothing-here"}).json()
        assert miss["items"] == []
        assert "does not mean" in miss["scope"]


def test_detail_endpoints_and_their_errors(rig: Rig) -> None:
    seed_funding(rig)
    with TestClient(rig.app) as c:
        tx = c.get(f"{API}/transactions/ethereum/{eth_tx(1)}")
        assert tx.status_code == 200
        assert tx.json()["created_contract"] == eth_addr(0xC1)
        assert c.get(f"{API}/transactions/ethereum/{eth_tx(99)}").status_code == 404
        assert c.get(f"{API}/transactions/nochain/{eth_tx(1)}").status_code in (404, 422)
        assert c.get(f"{API}/contracts/ethereum/{eth_addr(0xC1)}").json()["created"][
            "deployer"
        ] == eth_addr(0xD1)
        assert c.get(f"{API}/contracts/ethereum/{eth_addr(0x4242)}").status_code == 404
        assert c.get(f"{API}/tokens/ethereum/{eth_addr(0x5555)}").status_code == 404
        assert c.get(f"{API}/tokens/ethereum/not-an-address").status_code == 422
        assert c.get(f"{API}/transactions/ethereum/ab").status_code == 422


def test_relationship_rebuild_and_queries(rig: Rig) -> None:
    seed_funding(rig)
    with TestClient(rig.app) as c:
        assert c.get(f"{API}/relationships/summary").json() == {"counts": {}}
        built = c.post(f"{API}/relationships/rebuild", params={"chain": "ethereum"})
        assert built.status_code == 201
        assert built.json()["chains"]["ethereum"]["funded_by"]["created"] == 3
        rels = c.get(f"{API}/entities/ethereum/{eth_addr(0xB1)}/relationships").json()
        assert [r["kind"] for r in rels["items"]] == ["funded_by"]
        assert rels["derived"]["basis"] == "hypothesis"
        assert (
            c.get(
                f"{API}/entities/ethereum/{eth_addr(0xA1)}/relationships",
                params={"direction": "in"},
            ).json()["total"]
            == 3
        )
        outgoing = c.get(
            f"{API}/entities/ethereum/{eth_addr(0xA1)}/relationships", params={"direction": "out"}
        ).json()
        assert outgoing["total"] == 0  # funding is recorded from the recipient's side
        for bad in ({"kind": "same_owner"}, {"direction": "up"}, {"status": "maybe"}, {"limit": 0}):
            assert (
                c.get(
                    f"{API}/entities/ethereum/{eth_addr(0xB1)}/relationships", params=bad
                ).status_code
                == 422
            )
        assert c.post(f"{API}/relationships/rebuild", params={"chain": "nochain"}).status_code in (
            404,
            422,
        )
        summary = c.get(f"{API}/relationships/summary").json()["counts"]
        assert summary["funded_by"] == {"active": 3}
        assert summary["deployed_by"] == {"active": 1}


def test_notes_and_timeline_workflow(rig: Rig) -> None:
    with TestClient(rig.app) as c:
        case = c.post(f"{API}/investigations", json={"title": "Funding review"}).json()
        base = f"{API}/investigations/{case['id']}"
        made = c.post(f"{base}/notes", json={"kind": "hypothesis", "body": "Perhaps one operator."})
        assert made.status_code == 201
        assert (made.json()["author"], made.json()["evidenced"]) == ("local", False)
        c.post(f"{base}/notes", json={"body": "Checked an explorer."})
        assert c.get(f"{base}/notes").json()["total"] == 2
        assert [
            n["kind"] for n in c.get(f"{base}/notes", params={"kind": "hypothesis"}).json()["items"]
        ] == ["hypothesis"]
        types = [e["type"] for e in c.get(f"{base}/timeline").json()["items"]]
        assert types[0] == "case_opened"
        assert set(types) == {"case_opened", "hypothesis", "note"}
        assert c.post(f"{base}/notes", json={"kind": "fact", "body": "x"}).status_code == 422
        assert c.post(f"{base}/notes", json={"body": ""}).status_code == 422
        assert c.post(f"{base}/notes", json={"body": "x", "extra": 1}).status_code == 422
        assert c.post(f"{API}/investigations/999/notes", json={"body": "x"}).status_code == 404
        assert c.get(f"{API}/investigations/999/timeline").status_code == 404


def test_roles_apply_to_notes_and_rebuilds(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.user("ana", "analyst")
    rig.user("vic", "viewer")
    try:
        with TestClient(rig.app) as c:
            r = c.post(f"{API}/auth/login", json={"username": "vic", "password": PASSWORD})
            viewer = {"X-CSRF-Token": r.json()["csrf_token"]}
            assert c.get(f"{API}/relationships/summary").status_code == 200
            assert c.post(f"{API}/relationships/rebuild", headers=viewer).status_code == 403
            c.post(f"{API}/auth/logout", headers=viewer)
            r = c.post(f"{API}/auth/login", json={"username": "ana", "password": PASSWORD})
            analyst = {"X-CSRF-Token": r.json()["csrf_token"]}
            assert c.post(f"{API}/relationships/rebuild", headers=analyst).status_code == 201
            case = c.post(f"{API}/investigations", json={"title": "T"}, headers=analyst).json()
            note = c.post(
                f"{API}/investigations/{case['id']}/notes", json={"body": "hi"}, headers=analyst
            )
            assert note.status_code == 201
            assert note.json()["author"] == "ana"  # taken from the session, not the request
            forged = c.post(
                f"{API}/investigations/{case['id']}/notes",
                json={"body": "hi", "author": "root"},
                headers=analyst,
            )
            assert forged.status_code == 422
    finally:
        rig.dispose()


def test_openapi_lists_the_entity_routes(rig: Rig) -> None:
    with TestClient(rig.app) as c:
        paths = c.get(f"{API}/openapi.json").json()["paths"]
    for p in (
        "/search",
        "/transactions/{chain}/{tx_id}",
        "/tokens/{chain}/{address}",
        "/contracts/{chain}/{address}",
        "/entities/{chain}/{ref}/relationships",
        "/relationships/rebuild",
        "/investigations/{investigation_id}/notes",
        "/investigations/{investigation_id}/timeline",
    ):
        assert f"{API}{p}" in paths, p
