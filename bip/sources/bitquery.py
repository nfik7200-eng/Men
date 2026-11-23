"""Bitquery GraphQL API (docs checked 2026-11-23: docs.bitquery.io).

``streaming.bitquery.io/graphql`` with header ``Authorization: Bearer <key>``.
"""

from __future__ import annotations

from typing import Any

from bip.sources.client import SourceClient, SourceResponse, as_dict

PROVIDER = "bitquery"
BASE = "https://streaming.bitquery.io/graphql"


def ping(client: SourceClient) -> SourceResponse:
    key = client.require_key(PROVIDER)
    query = {"query": "{ EIP20 { Transacts(limit: 1) { Transaction { Hash } } } }"}
    return client.post_json(
        PROVIDER,
        BASE,
        query,
        headers={"Authorization": f"Bearer {key}"},
    )


def query_graphql(
    client: SourceClient, graphql_query: str, variables: dict[str, Any] | None = None
) -> tuple[dict[str, Any], SourceResponse]:
    key = client.require_key(PROVIDER)
    payload: dict[str, Any] = {"query": graphql_query}
    if variables:
        payload["variables"] = variables
    resp = client.post_json(
        PROVIDER,
        BASE,
        payload,
        headers={"Authorization": f"Bearer {key}"},
    )
    body = as_dict(resp.data)
    return body, resp
