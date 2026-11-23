"""Keyed REST integrations: status, connection tests, prices, Etherscan lookups, Dune results.

Every result stored here is labelled an observation of the provider's answer (``basis:
provider_reported``), not an independently verified fact. Writes are small and bounded: one
latest row per subject in ``source_records`` and one latest price per asset in ``token_prices``.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, insert, select

from bip.config.settings import SourcesSettings
from bip.db import schema
from bip.db.repository import Repository
from bip.sources import coingecko, dune, etherscan
from bip.sources.client import SourceClient, SourceError, SourceResponse

INTEGRATIONS = ("coingecko", "etherscan", "dune")
EVIDENCE = {"evidence_class": "verified_observation", "basis": "provider_reported"}
_MAX_PAYLOAD = 2_000_000


class SourcesService:
    def __init__(self, repo: Repository, cfg: SourcesSettings, client: SourceClient) -> None:
        self.repo = repo
        self.cfg = cfg
        self.client = client
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ status and tests

    def status(self) -> dict[str, Any]:
        usage = self.client.state.usage_today()
        states = self.client.state.states()
        items: list[dict[str, Any]] = []
        for pid in INTEGRATIONS:
            spec = self.client.registry.get(pid)
            key_set = self.client.key(pid) is not None
            keyless_ok = pid == "coingecko" and self.cfg.coingecko_plan != "pro"
            st = states.get(pid)
            items.append(
                {
                    "provider_id": pid,
                    "name": spec.name,
                    "key_set": key_set,
                    "configured": key_set or keyless_ok,
                    "requires_subscription": spec.requires_subscription,
                    "attribution": spec.attribution,
                    "docs_url": spec.docs_url,
                    "terms_url": spec.terms_url,
                    "rate_limit": spec.rate_limit,
                    "requests_today": usage.get(pid, 0),
                    "daily_cap": self.cfg.daily_request_cap,
                    "last_outcome": st.last_outcome if st else None,
                    "last_check_at": st.last_check_at if st else None,
                }
            )
        return {"items": items}

    def test(self, provider_id: str) -> dict[str, Any]:
        if provider_id not in INTEGRATIONS:
            raise SourceError("no_integration", f"{provider_id} has no integration")
        try:
            if provider_id == "coingecko":
                resp = coingecko.ping(self.client)
            elif provider_id == "etherscan":
                resp = etherscan.ping(self.client)
            else:
                ids = self.cfg.dune_query_ids
                if not ids:
                    self.client.require_key("dune")
                    return {
                        "ok": False,
                        "code": "no_queries",
                        "message": "Key present. Add a saved query id on the Setup page "
                        "to test it.",
                    }
                _payload, resp = dune.latest_result(self.client, ids[0])
        except SourceError as exc:
            return {"ok": False, "code": exc.code, "message": str(exc)}
        return {"ok": True, "code": "ok", "message": f"Connected ({resp.elapsed_ms} ms)."}

    # ------------------------------------------------------------------ storage helpers

    def _put_record(
        self, provider_id: str, kind: str, subject: str, payload: Any, resp: SourceResponse
    ) -> None:
        raw = json.dumps(payload, default=str)
        if len(raw) > _MAX_PAYLOAD:
            raise SourceError("too_large", f"{provider_id} {kind} answer is too large to store")
        t = schema.source_records
        with self.repo.transaction() as conn:
            conn.execute(
                delete(t).where(
                    t.c.provider_id == provider_id, t.c.kind == kind, t.c.subject == subject
                )
            )
            conn.execute(
                insert(t).values(
                    provider_id=provider_id,
                    kind=kind,
                    subject=subject,
                    payload_json=raw,
                    content_sha256=resp.content_sha256,
                    request_id=resp.request_id,
                    fetched_at=resp.fetched_at,
                )
            )

    def records(
        self,
        *,
        provider_id: str | None = None,
        kind: str | None = None,
        subject: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        t = schema.source_records
        where = [
            c
            for c in (
                t.c.provider_id == provider_id if provider_id else None,
                t.c.kind == kind if kind else None,
                t.c.subject == subject if subject else None,
            )
            if c is not None
        ]
        with self.repo.transaction() as conn:
            rows = (
                conn.execute(select(t).where(*where).order_by(t.c.fetched_at.desc()).limit(limit))
                .mappings()
                .all()
            )
        return {
            "items": [
                {
                    "provider_id": r["provider_id"],
                    "kind": r["kind"],
                    "subject": r["subject"],
                    "payload": json.loads(r["payload_json"]),
                    "content_sha256": r["content_sha256"],
                    "request_id": r["request_id"],
                    "fetched_at": r["fetched_at"],
                    "attribution": self.client.registry.get(r["provider_id"]).attribution,
                    **EVIDENCE,
                }
                for r in rows
            ]
        }

    # ------------------------------------------------------------------ CoinGecko

    def refresh_prices(self, chains: tuple[str, ...] = ("ethereum", "solana")) -> dict[str, Any]:
        if not self._lock.acquire(blocking=False):
            raise SourceError("busy", "a price refresh is already running")
        try:
            prices, missing, resp = coingecko.native_prices(self.client, list(chains))
            stored = self._store_prices(prices, resp)
            by_platform: dict[str, list[str]] = {}
            for item in self.cfg.price_tokens:
                platform, _, address = item.partition(":")
                by_platform.setdefault(platform, []).append(address)
            for platform, addresses in by_platform.items():
                for i in range(0, len(addresses), 100):
                    batch, gone, tresp = coingecko.token_prices(
                        self.client, platform, addresses[i : i + 100]
                    )
                    stored += self._store_prices(batch, tresp)
                    missing += gone
            return {"stored": stored, "missing": missing, "currency": self.cfg.price_currency}
        finally:
            self._lock.release()

    def _store_prices(self, prices: list[coingecko.Price], resp: SourceResponse) -> int:
        t = schema.token_prices
        with self.repo.transaction() as conn:
            for p in prices:
                conn.execute(
                    delete(t).where(
                        t.c.chain == p.chain,
                        t.c.asset == p.asset,
                        t.c.currency == p.currency,
                        t.c.provider_id == coingecko.PROVIDER,
                    )
                )
                conn.execute(
                    insert(t).values(
                        chain=p.chain,
                        asset=p.asset,
                        currency=p.currency,
                        provider_id=coingecko.PROVIDER,
                        price=p.price,
                        provider_updated_at=p.provider_updated_at,
                        fetched_at=resp.fetched_at,
                        request_id=resp.request_id,
                    )
                )
        return len(prices)

    def prices(self, *, chain: str | None = None) -> dict[str, Any]:
        t = schema.token_prices
        where = [t.c.chain == chain] if chain else []
        with self.repo.transaction() as conn:
            rows = (
                conn.execute(select(t).where(*where).order_by(t.c.chain, t.c.asset))
                .mappings()
                .all()
            )
        attribution = self.client.registry.get(coingecko.PROVIDER).attribution
        return {
            "items": [
                {
                    "chain": r["chain"],
                    "asset": r["asset"],
                    "currency": r["currency"],
                    "price": r["price"],
                    "provider_id": r["provider_id"],
                    "provider_updated_at": r["provider_updated_at"],
                    "fetched_at": r["fetched_at"],
                    "request_id": r["request_id"],
                    **EVIDENCE,
                }
                for r in rows
            ],
            "attribution": attribution,
        }

    # ------------------------------------------------------------------ Etherscan

    def etherscan_contract(self, address: str) -> dict[str, Any]:
        info, resp = etherscan.contract(self.client, address)
        self._put_record("etherscan", "contract", info["address"], info, resp)
        return {
            **info,
            "attribution": self.client.registry.get("etherscan").attribution,
            **EVIDENCE,
        }

    def etherscan_transactions(
        self, address: str, *, kind: str, limit: int = 100
    ) -> dict[str, Any]:
        rows, resp = etherscan.transactions(self.client, address, kind, limit)
        subject = f"{kind}:{address.lower()}"
        self._put_record("etherscan", "transactions", subject, rows, resp)
        return {
            "items": rows,
            "kind": kind,
            "address": address.lower(),
            "attribution": self.client.registry.get("etherscan").attribution,
            **EVIDENCE,
        }

    # ------------------------------------------------------------------ Dune

    def refresh_dune(self) -> dict[str, Any]:
        out: list[dict[str, Any]] = []
        for qid in self.cfg.dune_query_ids:
            try:
                payload, resp = dune.latest_result(self.client, qid)
            except SourceError as exc:
                out.append({"query_id": qid, "ok": False, "rows": 0, "error": str(exc)})
                if exc.code in ("not_configured", "cap_reached"):
                    break
                continue
            self._put_record("dune", "query_result", str(qid), payload, resp)
            out.append({"query_id": qid, "ok": True, "rows": len(payload["rows"]), "error": None})
        return {"queries": out}

    @staticmethod
    def now() -> datetime:
        return datetime.now(UTC)
