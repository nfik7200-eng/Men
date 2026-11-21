from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import Engine, insert

from bip.db import schema
from bip.db.types import utcnow

API = "/api/v1"
ADDR = "0x" + "ab" * 20
NOW = "2026-01-01T00:00:00+00:00"


def mk_case(client: TestClient, **kw: object) -> int:
    body = {"title": "Case", "case_type": "rugpull", **kw}
    r = client.post(f"{API}/cases", json=body)
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


def ev_body(**kw: object) -> dict[str, object]:
    return {
        "kind": "verified_observation",
        "subject_type": "address",
        "subject_ref": ADDR,
        "description": "seen",
        "source_kind": "manual",
        "collected_at": NOW,
        **kw,
    }


def test_case_crud(client: TestClient) -> None:
    cid = mk_case(client, chain="ethereum")
    assert client.get(f"{API}/cases").json()[0]["id"] == cid
    assert client.get(f"{API}/cases?status=closed").json() == []
    r = client.patch(f"{API}/cases/{cid}", json={"status": "closed", "title": "New"})
    assert r.json()["status"] == "closed"
    assert r.json()["closed_at"] is not None
    assert client.get(f"{API}/cases/{cid}").json()["case"]["title"] == "New"
    assert client.delete(f"{API}/cases/{cid}").status_code == 204
    assert client.get(f"{API}/cases/{cid}").status_code == 404


def test_case_validation(client: TestClient) -> None:
    assert client.post(f"{API}/cases", json={"title": "x", "case_type": "nope"}).status_code == 422
    r = client.post(f"{API}/cases", json={"title": "x", "case_type": "rugpull", "chain": "zzz"})
    assert r.status_code == 422
    assert client.get(f"{API}/cases/999").status_code == 404


def test_templates(client: TestClient) -> None:
    assert len(client.get(f"{API}/cases/templates").json()) == 11
    assert client.get(f"{API}/cases/templates/flash_loan").json()["case_type"] == "flash_loan"


def test_evidence_lifecycle(client: TestClient) -> None:
    cid = mk_case(client)
    r = client.post(f"{API}/cases/{cid}/evidence", json=ev_body(payload={"a": 1}))
    assert r.status_code == 201
    eid = r.json()["id"]
    assert r.json()["payload_json"] == '{"a": 1}'
    assert client.get(f"{API}/cases/{cid}/evidence?kind=hypothesis").json() == []
    assert len(client.get(f"{API}/cases/{cid}/evidence").json()) == 1
    assert client.get(f"{API}/cases/{cid}/evidence/{eid}").status_code == 200
    # provenance is required; naive timestamps are rejected
    bad = ev_body()
    del bad["source_kind"]
    assert client.post(f"{API}/cases/{cid}/evidence", json=bad).status_code == 422
    r = client.post(f"{API}/cases/{cid}/evidence", json=ev_body(collected_at="2026-01-01T00:00:00"))
    assert r.status_code == 422
    assert client.delete(f"{API}/cases/{cid}/evidence/{eid}").status_code == 204
    assert client.post(f"{API}/cases/999/evidence", json=ev_body()).status_code == 404


def test_hypothesis_rules(client: TestClient) -> None:
    cid = mk_case(client)
    other = mk_case(client)
    eid = client.post(f"{API}/cases/{cid}/evidence", json=ev_body()).json()["id"]
    url = f"{API}/cases/{cid}/hypotheses"
    assert client.post(url, json={"statement": "s", "status": "supported"}).status_code == 422
    assert client.post(url, json={"statement": "s", "evidence_ids": [999]}).status_code == 422
    h = client.post(url, json={"statement": "s"})
    assert h.status_code == 201
    assert h.json()["status"] == "open"
    hid = h.json()["id"]
    assert client.patch(f"{url}/{hid}", json={"status": "supported"}).status_code == 422
    r = client.patch(f"{url}/{hid}", json={"status": "supported", "evidence_ids": [eid]})
    assert r.status_code == 200
    assert r.json()["evidence_ids"] == [eid]
    # cited evidence cannot be deleted; evidence of another case cannot be cited
    assert client.delete(f"{API}/cases/{cid}/evidence/{eid}").status_code == 422
    assert (
        client.post(
            f"{API}/cases/{other}/hypotheses", json={"statement": "s", "evidence_ids": [eid]}
        )
    ).status_code == 422
    assert client.patch(f"{url}/999", json={"status": "open"}).status_code == 404
    assert len(client.get(url).json()) == 1


def test_leads(client: TestClient) -> None:
    cid = mk_case(client)
    url = f"{API}/cases/{cid}/leads"
    assert client.post(url, json={"title": "t", "priority": 9}).status_code == 422
    assert client.post(url, json={"title": "t", "source_evidence_id": 5}).status_code == 404
    lid = client.post(url, json={"title": "t", "priority": 2}).json()["id"]
    r = client.patch(f"{url}/{lid}", json={"status": "investigating", "priority": 1})
    assert r.json()["status"] == "investigating"
    assert r.json()["priority"] == 1
    assert client.get(f"{url}?status=new").json() == []
    assert client.patch(f"{url}/999", json={"status": "new"}).status_code == 404


def test_run_address_without_data_reports_gap(client: TestClient) -> None:
    r = client.post(
        f"{API}/investigations/run",
        json={
            "case_type": "rugpull",
            "chain": "ethereum",
            "target": ADDR.upper().replace("0X", "0x"),
        },
    )
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["evidence"] == []
    assert b["coverage_gaps"]
    assert b["analysis_run_id"] is not None
    assert len(b["hypotheses"]) == 2
    assert all(h["evidence_ids"] == [] for h in b["hypotheses"])
    assert all(h["status"] == "open" for h in b["hypotheses"])
    assert len(b["leads"]) == 2
    assert b["required_evidence_unconfirmed"] == b["template"]["required_evidence"]
    cid = b["case"]["id"]
    assert len(client.get(f"{API}/cases/{cid}").json()["leads"]) == 2


def test_run_tx_found_and_missing(client: TestClient, engine: Engine) -> None:
    tx = "0x" + "cd" * 32
    body = {"case_type": "flash_loan", "chain": "ethereum", "target_type": "tx", "target": tx}
    r = client.post(f"{API}/investigations/run", json=body)
    assert r.status_code == 201
    assert r.json()["coverage_gaps"]
    assert r.json()["evidence"] == []
    with engine.begin() as conn:
        conn.execute(
            insert(schema.transactions).values(
                chain="ethereum",
                tx_id=tx,
                block_number=1,
                provenance_json='{"provider":"fixture"}',
                stored_at=utcnow(),
            )
        )
    r = client.post(f"{API}/investigations/run", json=body)
    ev = r.json()["evidence"]
    assert len(ev) == 1
    assert ev[0]["kind"] == "verified_observation"
    assert ev[0]["source_ref"] == f"tx:{tx}"
    assert r.json()["coverage_gaps"] == []


def test_run_validation(client: TestClient) -> None:
    base = {"case_type": "rugpull", "chain": "ethereum", "target": "nonsense"}
    assert client.post(f"{API}/investigations/run", json=base).status_code == 422
    assert (
        client.post(f"{API}/investigations/run", json={**base, "chain": "zzz"}).status_code == 422
    )
    assert (
        client.post(f"{API}/investigations/run", json={**base, "case_type": "x"}).status_code == 422
    )
    assert client.get(f"{API}/cases").json() == []  # failed runs leave no partial case
