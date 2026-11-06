from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import Engine, create_engine

from bip.api.app import create_app
from bip.config import Settings, load_settings
from bip.contracts.findings import (
    Claim,
    ClaimType,
    EntityKind,
    EntityRef,
    EvidenceKind,
    EvidenceRef,
    Finding,
)
from bip.contracts.provenance import VerificationStatus
from bip.db.repository import Repository
from tests.api.conftest import roomy_guard
from tests.api.test_database import evm_tx
from tests.conftest import COLLECTED, EVM_ADDR_A, EVM_ADDR_B, SOL_KEY_B, evm_provenance

API = "/api/v1"


def error_code(resp: Response) -> str:
    return resp.json()["error"]["code"]


# ---------- system ----------


def test_health(client: TestClient) -> None:
    body = client.get(f"{API}/health").json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["environment"] == "test"
    assert body["version"] == "0.1.0"
    assert body["timestamp"].endswith("Z")


def test_health_reports_unavailable_database(settings: Settings, tmp_path: Path) -> None:
    broken = create_engine(f"sqlite+pysqlite:///{tmp_path}/no/such/dir/db.sqlite3")
    app = create_app(settings, engine=broken, disk_guard=roomy_guard(tmp_path))
    with TestClient(app) as c:
        assert c.get(f"{API}/health").json()["database"] == "unavailable"
        resp = c.get(f"{API}/watchlists")
        assert resp.status_code == 503
        assert error_code(resp) == "database_unavailable"
        assert "sqlite" not in resp.text.lower()
        assert str(tmp_path) not in resp.text


def test_security_headers_and_unknown_route(client: TestClient) -> None:
    resp = client.get(f"{API}/does-not-exist")
    assert resp.status_code == 404
    assert error_code(resp) == "route_not_found"  # ADR-031: distinct from a missing record
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["cache-control"] == "no-store"


def test_cors_allows_only_configured_origins(client: TestClient) -> None:
    ok = client.options(
        f"{API}/health",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = client.options(
        f"{API}/health",
        headers={"Origin": "https://evil.example.net", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in bad.headers


def test_request_size_limit(client: TestClient) -> None:
    resp = client.post(
        f"{API}/watchlists",
        content=b"{" + b" " * 70_000 + b"}",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 413
    assert error_code(resp) == "payload_too_large"


def test_settings_and_responses_expose_no_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BIP_POSTGRES_DSN", "postgresql://bip:Sup3rS3cretPw@db.example.com/bip")
    fake_key = "alk_live_" + "9f8e7d6c5b4a"  # synthetic; assembled so scanners do not flag it
    monkeypatch.setenv(
        "BIP_PROVIDER_ALCHEMY_ETH_URL", f"https://eth-mainnet.g.alchemy.com/v2/{fake_key}"
    )
    settings = load_settings(data_root=tmp_path / "secretroot", env="test")
    app = create_app(settings, disk_guard=roomy_guard(tmp_path))
    with TestClient(app) as c:
        paths = (
            "health",
            "settings",
            "chains",
            "dashboard/summary",
            "providers/status",
            "providers",
            "providers/alchemy-eth",
            "wallets",
        )
        bodies = [c.get(f"{API}/{p}").text for p in paths]
        alchemy = next(p for p in c.get(f"{API}/providers").json() if p["id"] == "alchemy-eth")
    blob = "\n".join(bodies)
    for leaked in ("Sup3rS3cretPw", fake_key, "secretroot", str(tmp_path)):
        assert leaked not in blob
    assert alchemy["configuration_status"] == "configured"
    assert alchemy["lifecycle"] == "untested"
    assert alchemy["endpoint_host"] is None  # credentialed URLs are never exposed
    assert alchemy["last_success_at"] is None


def test_public_settings_shape(client: TestClient) -> None:
    body = client.get(f"{API}/settings").json()
    assert body["authentication"] == "not_implemented"
    assert body["database_engine"] == "sqlite"
    assert body["enabled_chains"] == ["ethereum", "solana"]
    assert body["storage"]["writes_allowed"] is True


# ---------- chains, providers, dashboard ----------


def test_chains_have_adapters_but_are_not_connected_without_requests(client: TestClient) -> None:
    chains = {c["chain"]: c for c in client.get(f"{API}/chains").json()}
    assert set(chains) == {"ethereum", "solana"}
    for c in chains.values():
        assert c["configured"] is True
        assert c["adapter_implemented"] is True
        assert c["connected"] is False  # no successful request has been made
        assert c["data_available"] is False
        assert c["latest_stored_block"] is None
        assert c["latest_stored_slot"] is None
        assert c["last_successful_retrieval"] is None
        assert c["latest_observation"] is None
    assert chains["solana"]["position_kind"] == "slot"
    assert chains["ethereum"]["usable_providers"] == ["publicnode-eth"]
    assert chains["solana"]["usable_providers"] == ["solana-public"]


def test_chain_data_available_reflects_stored_rows(client: TestClient, repo: Repository) -> None:
    repo.store_transaction(evm_tx())
    eth = next(c for c in client.get(f"{API}/chains").json() if c["chain"] == "ethereum")
    assert eth["data_available"] is True
    assert eth["latest_stored_block"] == 100
    assert eth["connected"] is False


def test_no_provider_is_healthy_without_a_recorded_request(client: TestClient) -> None:
    rows = client.get(f"{API}/providers").json()
    assert len(rows) >= 10
    assert {r["lifecycle"] for r in rows} <= {
        "registered",
        "configured",
        "adapter_available",
        "untested",
        "disabled",
    }
    assert all(r["last_success_at"] is None for r in rows)
    untested = {r["id"]: r for r in rows if r["lifecycle"] == "untested"}
    assert set(untested) == {"publicnode-eth", "solana-public"}
    assert untested["publicnode-eth"]["endpoint_host"] == "ethereum-rpc.publicnode.com"
    assert all(m.startswith(("eth_", "get")) for r in rows for m in r["methods"])


def test_dashboard_empty_state(client: TestClient) -> None:
    body = client.get(f"{API}/dashboard/summary").json()
    for key in (
        "wallets_stored",
        "transactions_stored",
        "transfers_stored",
        "watchlists",
        "open_investigations",
        "findings_stored",
        "watchlisted_addresses",
    ):
        assert body[key] == 0
    assert body["recent_findings"] == []
    assert body["recent_activity"] == []
    assert body["providers"]["healthy"] == 0
    assert body["providers"]["untested"] == 2
    assert body["ingestion"]["runs_total"] == 0


def test_dashboard_counts_follow_real_records(client: TestClient) -> None:
    wid = client.post(f"{API}/watchlists", json={"name": "Funds"}).json()["id"]
    client.post(
        f"{API}/watchlists/{wid}/entries", json={"chain": "ethereum", "address": EVM_ADDR_A}
    )
    client.post(f"{API}/investigations", json={"title": "Check funder"})
    body = client.get(f"{API}/dashboard/summary").json()
    assert body["watchlists"] == 1
    assert body["watchlisted_addresses"] == 1
    assert body["open_investigations"] == 1
    assert body["transactions_stored"] == 0
    assert [a["event_type"] for a in body["recent_activity"]][:1] == ["investigation.created"]


# ---------- wallets ----------


def test_wallet_lookup(client: TestClient, repo: Repository) -> None:
    assert client.get(f"{API}/wallets").json()["total"] == 0
    resp = client.get(f"{API}/wallets/ethereum/{EVM_ADDR_A}")
    assert resp.status_code == 404
    assert "no data is stored" in resp.json()["error"]["message"]
    repo.store_transaction(evm_tx())
    profile = client.get(f"{API}/wallets/ethereum/{EVM_ADDR_A.upper().replace('0X', '0x')}").json()
    assert profile["address"] == EVM_ADDR_A
    assert profile["transaction_count"] == 1
    assert profile["first_seen_verification"] == "unverified"
    assert "owner" not in profile
    assert "label" not in profile
    assert client.get(f"{API}/wallets").json()["total"] == 2


@pytest.mark.parametrize(
    ("chain", "address", "match"),
    [
        ("dogecoin", EVM_ADDR_A, "unknown chain"),
        ("ethereum", "0x1234", "20-byte"),
        ("ethereum", SOL_KEY_B, "20-byte"),
        ("solana", EVM_ADDR_A, "Solana address"),
    ],
)
def test_wallet_lookup_validation(client: TestClient, chain: str, address: str, match: str) -> None:
    resp = client.get(f"{API}/wallets/{chain}/{address}")
    assert resp.status_code == 422
    assert error_code(resp) == "invalid_input"
    assert match in resp.json()["error"]["message"]


def test_wallet_list_pagination_bounds(client: TestClient) -> None:
    assert client.get(f"{API}/wallets?limit=500").status_code == 422
    assert client.get(f"{API}/wallets?offset=-1").status_code == 422


# ---------- watchlists ----------


def test_watchlist_lifecycle(client: TestClient) -> None:
    created = client.post(f"{API}/watchlists", json={"name": "Whales", "description": "Large"})
    assert created.status_code == 201
    wid = created.json()["id"]
    e1 = client.post(
        f"{API}/watchlists/{wid}/entries",
        json={"chain": "ethereum", "address": EVM_ADDR_A.upper().replace("0X", "0x")},
    )
    assert e1.status_code == 201
    assert e1.json()["address"] == EVM_ADDR_A
    e2 = client.post(
        f"{API}/watchlists/{wid}/entries", json={"chain": "solana", "address": SOL_KEY_B}
    )
    assert e2.status_code == 201

    dup = client.post(
        f"{API}/watchlists/{wid}/entries", json={"chain": "ethereum", "address": EVM_ADDR_A}
    )
    assert dup.status_code == 409

    got = client.get(f"{API}/watchlists/{wid}").json()
    assert got["entry_count"] == 2
    assert [(e["chain"], e["address"]) for e in got["entries"]] == [
        ("ethereum", EVM_ADDR_A),
        ("solana", SOL_KEY_B),
    ]

    assert client.delete(f"{API}/watchlists/{wid}/entries/{e1.json()['id']}").status_code == 204
    assert client.get(f"{API}/watchlists/{wid}").json()["entry_count"] == 1
    assert client.delete(f"{API}/watchlists/{wid}/entries/999").status_code == 404

    assert client.delete(f"{API}/watchlists/{wid}").status_code == 204
    assert client.get(f"{API}/watchlists/{wid}").status_code == 404
    assert client.get(f"{API}/watchlists").json() == []


@pytest.mark.parametrize(
    "body",
    [{}, {"name": ""}, {"name": " padded "}, {"name": "x" * 101}, {"name": "ok", "extra": 1}],
)
def test_watchlist_create_validation(client: TestClient, body: dict[str, object]) -> None:
    resp = client.post(f"{API}/watchlists", json=body)
    assert resp.status_code == 422
    assert error_code(resp) == "validation_error"


def test_watchlist_duplicate_name_and_bad_entries(client: TestClient) -> None:
    wid = client.post(f"{API}/watchlists", json={"name": "Dup"}).json()["id"]
    assert client.post(f"{API}/watchlists", json={"name": "Dup"}).status_code == 409
    bad = client.post(
        f"{API}/watchlists/{wid}/entries", json={"chain": "solana", "address": "0xabc"}
    )
    assert bad.status_code == 422
    missing = client.post(
        f"{API}/watchlists/999/entries", json={"chain": "ethereum", "address": EVM_ADDR_B}
    )
    assert missing.status_code == 404


def test_writes_refused_when_disk_guard_blocks(settings: Settings, engine: Engine) -> None:
    tight = roomy_guard(settings.resolved_data_root, free=0)
    with TestClient(create_app(settings, engine=engine, disk_guard=tight)) as c:
        resp = c.post(f"{API}/watchlists", json={"name": "Blocked"})
        assert resp.status_code == 507
        assert error_code(resp) == "storage_limit"
        assert c.get(f"{API}/watchlists").status_code == 200
        assert c.get(f"{API}/health").json()["storage_writes"] == "refused"


# ---------- investigations ----------


def make_finding(finding_id: str = "f-1") -> Finding:
    return Finding(
        finding_id=finding_id,
        finding_type="large-transfer",
        subject=EntityRef(kind=EntityKind.ADDRESS, identifier=EVM_ADDR_A, chain="ethereum"),
        claims=(
            Claim(
                statement="Sent 5,000 ETH.",
                claim_type=ClaimType.VERIFIED_FACT,
                evidence_ids=("ev-1",),
            ),
        ),
        evidence=(
            EvidenceRef(
                evidence_id="ev-1",
                kind=EvidenceKind.CHAIN_RECORD,
                locator="x",
                provenance=evm_provenance(verification_status=VerificationStatus.CROSS_VERIFIED),
            ),
        ),
        detected_at=COLLECTED,
        explanation="Synthetic test finding.",
        produced_by="test",
    )


def test_investigation_lifecycle(client: TestClient, repo: Repository) -> None:
    repo.store_transaction(evm_tx())  # the finding's verified evidence must be a stored record
    created = client.post(
        f"{API}/investigations",
        json={
            "title": "Trace funder",
            "description": "Follow inflows",
            "subject_chain": "ethereum",
            "subject_address": EVM_ADDR_A.upper().replace("0X", "0x"),
        },
    )
    assert created.status_code == 201
    inv = created.json()
    assert inv["status"] == "open"
    assert inv["subject_address"] == EVM_ADDR_A
    iid = inv["id"]

    patched = client.patch(f"{API}/investigations/{iid}", json={"status": "in_progress"})
    assert patched.json()["status"] == "in_progress"
    assert client.patch(f"{API}/investigations/{iid}", json={"status": "done"}).status_code == 422
    assert client.patch(f"{API}/investigations/999", json={"status": "closed"}).status_code == 404

    finding = json.loads(make_finding().model_dump_json())
    added = client.post(f"{API}/investigations/{iid}/findings", json=finding)
    assert added.status_code == 201
    assert added.json()["evidence_count"] == 1
    assert client.post(f"{API}/investigations/{iid}/findings", json=finding).status_code == 409

    got = client.get(f"{API}/investigations/{iid}").json()
    assert got["finding_count"] == 1
    assert got["findings"][0]["finding_id"] == "f-1"
    assert [i["id"] for i in client.get(f"{API}/investigations?status=in_progress").json()] == [iid]
    assert client.get(f"{API}/investigations?status=closed").json() == []
    assert client.get(f"{API}/dashboard/summary").json()["findings_stored"] == 1


def test_findings_must_satisfy_evidence_rules(client: TestClient) -> None:
    iid = client.post(f"{API}/investigations", json={"title": "Rules"}).json()["id"]
    finding = json.loads(make_finding().model_dump_json())
    finding["claims"][0]["evidence_ids"] = ["ev-missing"]
    resp = client.post(f"{API}/investigations/{iid}/findings", json=finding)
    assert resp.status_code == 422
    assert any("unknown evidence" in d["message"] for d in resp.json()["error"]["details"])


@pytest.mark.parametrize(
    "body",
    [
        {"title": ""},
        {"title": "x", "subject_chain": "ethereum"},
        {"title": "x", "subject_chain": "ethereum", "subject_address": "0x12"},
        {"title": "x", "status": "closed"},
    ],
)
def test_investigation_create_validation(client: TestClient, body: dict[str, object]) -> None:
    assert client.post(f"{API}/investigations", json=body).status_code == 422


def test_validation_errors_do_not_echo_input(client: TestClient) -> None:
    secretish = "sk_live_should_not_echo_123456"
    resp = client.post(f"{API}/watchlists", json={"name": secretish * 10})
    assert resp.status_code == 422
    assert secretish not in resp.text


def test_chunked_oversize_body_is_413(client: TestClient) -> None:
    def chunks():
        yield b'{"name": "'
        for _ in range(80):
            yield b"x" * 1024
        yield b'"}'

    resp = client.post(
        f"{API}/watchlists", content=chunks(), headers={"content-type": "application/json"}
    )
    assert resp.status_code == 413
    assert error_code(resp) == "payload_too_large"


def test_api_findings_cannot_claim_verification_for_unstored_records(
    client: TestClient, repo: Repository
) -> None:
    iid = client.post(f"{API}/investigations", json={"title": "Forgery check"}).json()["id"]
    finding = json.loads(make_finding("f-forged").model_dump_json())
    resp = client.post(f"{API}/investigations/{iid}/findings", json=finding)
    assert resp.status_code == 422
    assert "stored chain record" in resp.json()["error"]["message"]
    assert client.get(f"{API}/dashboard/summary").json()["findings_stored"] == 0

    repo.store_transaction(evm_tx())
    assert client.post(f"{API}/investigations/{iid}/findings", json=finding).status_code == 201


def test_api_findings_with_unverified_evidence_are_accepted(client: TestClient) -> None:
    iid = client.post(f"{API}/investigations", json={"title": "Hypothesis"}).json()["id"]
    finding = Finding(
        finding_id="f-hyp",
        finding_type="possible-cluster",
        subject=EntityRef(kind=EntityKind.ADDRESS, identifier=EVM_ADDR_A, chain="ethereum"),
        claims=(
            Claim(
                statement="May share a funder.",
                claim_type=ClaimType.HYPOTHESIS,
                evidence_ids=("ev-1",),
            ),
        ),
        evidence=(
            EvidenceRef(
                evidence_id="ev-1",
                kind=EvidenceKind.CHAIN_RECORD,
                locator="x",
                provenance=evm_provenance(),
            ),
        ),
        detected_at=COLLECTED,
        explanation="Analyst note.",
        produced_by="analyst",
        limitations=("Not verified.",),
    )
    body = json.loads(finding.model_dump_json())
    assert client.post(f"{API}/investigations/{iid}/findings", json=body).status_code == 201
