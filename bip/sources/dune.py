"""Dune saved-query results (docs checked 2026-11-23: docs.dune.com get-query-result).

``GET https://api.dune.com/api/v1/query/{query_id}/results`` with header ``X-Dune-Api-Key``
returns the latest result of a query the key's owner can read. Dune has no free API tier: the
owner starts a trial or plan. Rows are kept as Dune returned them, bounded by ``dune_max_rows``.
"""

from __future__ import annotations

from typing import Any

from bip.sources.client import SourceClient, SourceError, SourceResponse, as_dict, as_list

PROVIDER = "dune"
BASE = "https://api.dune.com/api/v1"


def latest_result(client: SourceClient, query_id: int) -> tuple[dict[str, Any], SourceResponse]:
    if query_id < 1:
        raise SourceError("invalid_input", "query id must be positive")
    key = client.require_key(PROVIDER)
    resp = client.get(
        PROVIDER,
        f"{BASE}/query/{int(query_id)}/results",
        params={"limit": str(client.cfg.dune_max_rows)},
        headers={"X-Dune-Api-Key": key},
    )
    body = as_dict(resp.data)
    result = as_dict(body.get("result"))
    rows = as_list(result.get("rows"))
    if rows is None:
        raise SourceError("invalid_response", f"Dune query {query_id}: no rows in the answer")
    meta = as_dict(result.get("metadata"))
    columns = as_list(meta.get("column_names"))
    payload: dict[str, Any] = {
        "query_id": query_id,
        "execution_id": body.get("execution_id"),
        "state": body.get("state"),
        "execution_ended_at": body.get("execution_ended_at"),
        "columns": [str(c) for c in columns] if columns is not None else None,
        "rows": rows[: client.cfg.dune_max_rows],
        "total_row_count": meta.get("total_row_count"),
    }
    return payload, resp
