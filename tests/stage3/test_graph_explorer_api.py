"""Graph explorer API over the offline fake-RPC ingestion (no live network)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.stage3.test_intel_api import INTEL, Env, client, env, ingest  # noqa: F401  # pyright: ignore[reportUnusedImport]


def test_explorer_endpoint_filters_and_trail(env: Env, client: TestClient) -> None:  # noqa: F811
    w = env.eth.wallet
    ingest(client, "ethereum", w)
    url = f"{INTEL}/graph-explorer/ethereum/{w}"
    g = client.get(url, params={"depth": 2}).json()
    assert g["explorer_version"]
    assert g["edges"]
    for e in g["edges"]:
        for x in e["evidence"]:
            assert x["provider_response"]["provider"]
            assert x["verification_status"]
    assert g["timeline"]["start"] is not None
    # fixture data is single-source: a verified-only view is empty but keeps the centre
    v = client.get(url, params={"status": "verified"}).json()
    assert v["edges"] == []
    assert [n["id"] for n in v["nodes"]] == [w]
    assert v["filtered_out"]["by_status"] > 0
    both = client.get(url, params={"status": ["single_source", "hypothesis"]}).json()
    assert len(both["edges"]) == len(client.get(url).json()["edges"])
    # a window before the first observation hides everything
    early = client.get(url, params={"until": "2000-01-01T00:00:00Z"}).json()
    assert early["edges"] == []
    # node trail
    n = client.get(f"{url}/nodes/{w}").json()
    assert n["transaction_count"] >= 1
    assert n["block_range"]["min"] is not None
    assert n["transactions"][0]["tx_id"]
    assert n["providers"]


def test_explorer_validation(env: Env, client: TestClient) -> None:  # noqa: F811
    w = env.eth.wallet
    url = f"{INTEL}/graph-explorer/ethereum/{w}"
    assert client.get(url, params={"status": "bogus"}).status_code == 422
    assert client.get(url, params={"since": "2026-01-01T00:00:00"}).status_code == 422
    assert (
        client.get(
            url, params={"since": "2026-02-01T00:00:00Z", "until": "2026-01-01T00:00:00Z"}
        ).status_code
        == 422
    )
    assert client.get(f"{url}/nodes/0xnotinthegraph").status_code == 404
