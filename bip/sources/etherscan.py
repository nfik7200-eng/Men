"""Etherscan API V2 (docs checked 2026-11-23: docs.etherscan.io v2-migration and endpoints).

``https://api.etherscan.io/v2/api`` with ``chainid`` and ``apikey`` query parameters. A body with
``status "0"`` and ``message "No transactions found"`` is a valid empty answer; any other
``status "0"`` is an error whose ``result`` text is reported. Free-tier output must carry the
attribution "Powered by Etherscan.io APIs" (registry), and the free tier is for personal and
research use only.
"""

from __future__ import annotations

import re
from typing import Any

from bip.sources.client import SourceClient, SourceError, SourceResponse, as_dict, as_list

PROVIDER = "etherscan"
BASE = "https://api.etherscan.io/v2/api"
_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
_EMPTY = {"No transactions found", "No records found", "No data found"}
KINDS = {"normal": "txlist", "internal": "txlistinternal"}


def _check_address(address: str) -> str:
    if not _ADDRESS.match(address):
        raise SourceError("invalid_input", "expected an 0x-prefixed 40-hex-digit address")
    return address.lower()


def _call(client: SourceClient, params: dict[str, str]) -> tuple[list[Any], SourceResponse]:
    key = client.require_key(PROVIDER)
    resp = client.get(
        PROVIDER,
        BASE,
        params={"chainid": str(client.cfg.etherscan_chain_id), **params, "apikey": key},
    )
    body = as_dict(resp.data)
    status, message, result = body.get("status"), str(body.get("message", "")), body.get("result")
    rows = as_list(result)
    if status == "1" and rows is not None:
        return rows, resp
    if status == "0" and message in _EMPTY:
        return [], resp
    detail = result if isinstance(result, str) else message or "unexpected answer"
    raise SourceError("provider_error", f"Etherscan: {detail[:160]}")


def ping(client: SourceClient) -> SourceResponse:
    """A cheap authenticated call: the ETH balance of the zero address."""
    key = client.require_key(PROVIDER)
    resp = client.get(
        PROVIDER,
        BASE,
        params={
            "chainid": str(client.cfg.etherscan_chain_id),
            "module": "account",
            "action": "balance",
            "address": "0x" + "0" * 40,
            "tag": "latest",
            "apikey": key,
        },
    )
    body = as_dict(resp.data)
    if body.get("status") != "1":
        raise SourceError(
            "provider_error", f"Etherscan: {str(body.get('result') or body.get('message'))[:160]}"
        )
    return resp


def contract(client: SourceClient, address: str) -> tuple[dict[str, Any], SourceResponse]:
    addr = _check_address(address)
    src, resp = _call(client, {"module": "contract", "action": "getsourcecode", "address": addr})
    created, _ = _call(
        client, {"module": "contract", "action": "getcontractcreation", "contractaddresses": addr}
    )
    s = as_dict(src[0]) if src else {}
    c = as_dict(created[0]) if created else {}
    abi = str(s.get("ABI", ""))
    verified = bool(s.get("SourceCode")) and "not verified" not in abi.lower()
    info = {
        "address": addr,
        "verified": verified,
        "name": s.get("ContractName") or None,
        "compiler": s.get("CompilerVersion") or None,
        "license": s.get("LicenseType") or None,
        "proxy": str(s.get("Proxy", "0")) not in ("", "0"),
        "implementation": (s.get("Implementation") or None),
        "creator": (str(c["contractCreator"]).lower() if c.get("contractCreator") else None),
        "creation_tx": c.get("txHash") or None,
        "creation_block": int(c["blockNumber"])
        if str(c.get("blockNumber", "")).isdigit()
        else None,
    }
    return info, resp


def transactions(
    client: SourceClient, address: str, kind: str, limit: int = 100
) -> tuple[list[dict[str, Any]], SourceResponse]:
    if kind not in KINDS:
        raise SourceError("invalid_input", "kind must be normal or internal")
    addr = _check_address(address)
    rows, resp = _call(
        client,
        {
            "module": "account",
            "action": KINDS[kind],
            "address": addr,
            "page": "1",
            "offset": str(max(1, min(limit, 1000))),
            "sort": "desc",
        },
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        d = as_dict(r)
        if not d:
            continue
        out.append(
            {
                "tx_id": d.get("hash"),
                "block_number": int(d["blockNumber"])
                if str(d.get("blockNumber", "")).isdigit()
                else None,
                "timestamp": int(d["timeStamp"]) if str(d.get("timeStamp", "")).isdigit() else None,
                "from": (str(d["from"]).lower() if d.get("from") else None),
                "to": (str(d["to"]).lower() if d.get("to") else None),
                "value_wei": str(d["value"]) if str(d.get("value", "")).isdigit() else None,
                "failed": str(d.get("isError", "0")) == "1",
                "type": d.get("type") or None,
            }
        )
    return out, resp
