"""Cross-provider corroboration of stored transactions.

For one stored transaction, BIP asks up to two *other* providers whose infrastructure group
differs from the original source (and from each other) for the same transaction, normalizes
what each returns and compares it with the stored record.

Every provider answer is kept in ``provider_observations``; the comparison outcome is appended
to ``evidence_verifications`` (latest row = effective status). Stored source records are never
modified. Outcomes:

* ``cross_verified`` (shown as *corroborated*): at least two independent infrastructure groups
  (the original source counts when its group is known) report identical normalized fields;
* ``conflict`` (*conflicted*): an independent provider reports different fields;
* ``invalidated``: independent providers agree on a *different* block hash (reorg), or at
  least two independent providers report the transaction as not found;
* ``unverified``: not enough independent agreement (e.g. only one other group answered).

If no other provider could be asked at all, nothing is recorded: the result is
``could_not_run`` and the evidence keeps its previous status.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from sqlalchemy import insert, select

from bip.chains.common import ParseError, SourceInfo, arr, obj
from bip.chains.ethereum import parse as eth
from bip.chains.ethereum.adapter import EthereumAdapter
from bip.chains.solana import parse as sol
from bip.chains.solana.adapter import SolanaAdapter
from bip.contracts.provenance import Finality, Provenance
from bip.db import schema
from bip.db.errors import RecordNotFoundError
from bip.db.observations import ObservationStore
from bip.db.repository import Repository
from bip.db.types import utcnow
from bip.intel.common import J, iso, label
from bip.providers.registry import Registry, independent
from bip.router.router import (
    BudgetExhaustedError,
    CallBudget,
    NoEligibleProviderError,
    ProviderRouter,
    RoutingFailedError,
)
from bip.rpc.jsonrpc import RpcError
from bip.rpc.transport import TransportError

MAX_OTHER_PROVIDERS = 2
CALL_BUDGET = 12
DEADLINE_S = 30.0
COMPARED_FIELDS = (
    "block_number",
    "slot",
    "block_hash",
    "status",
    "from",
    "to",
    "value_raw",
    "fee_raw",
    "block_time",
    "created_contract",
)


def stored_observation(repo: Repository, chain: str, tx_id: str) -> tuple[J, Mapping[str, Any]]:
    t, tr = schema.transactions, schema.transfers
    with repo.transaction() as conn:
        row = (
            conn.execute(select(t).where(t.c.chain == chain, t.c.tx_id == tx_id)).mappings().first()
        )
        if row is None:
            raise RecordNotFoundError(f"{chain} transaction {tx_id} is not stored")
        transfers = (
            conn.execute(select(tr).where(tr.c.chain == chain, tr.c.tx_id == tx_id))
            .mappings()
            .all()
        )
    fields: J = {
        "block_number": row["block_number"],
        "slot": row["slot"],
        "block_hash": row["block_hash"],
        "status": row["status"],
        "from": row["from_address"],
        "to": row["to_address"] if chain == "ethereum" else None,
        "value_raw": row["value_raw"],
        "fee_raw": row["fee_raw"] if chain == "solana" else None,
        "block_time": iso(row["block_time"]),
        "created_contract": row["created_contract"],
        "transfers": {
            r["event_key"]: [r["token"], r["from_address"], r["to_address"], r["amount_raw"]]
            for r in transfers
        },
    }
    return fields, dict(row)


def _eth_fields(raw_tx: object, raw_receipt: object, src: SourceInfo) -> J:
    parsed = eth.parse_transaction(
        raw_tx, raw_receipt, block_time=None, src=src, finality=Finality.OBSERVED
    )
    tx = parsed.tx
    transfers: dict[str, list[Any]] = {}
    if tx.status is not None and tx.status.value != "failed":
        native = eth.native_transfer(parsed)
        if native is not None:
            transfers["native"] = [None, tx.from_address, tx.to_address, str(parsed.value_wei)]
        logs = obj(raw_receipt, "receipt").get("logs") if raw_receipt is not None else None
        for raw_log in arr(logs or [], "receipt.logs", max_len=5000):
            try:
                lg = eth.parse_log(raw_log, src, block_time=None, finality=Finality.OBSERVED)
            except ParseError:
                continue
            transfer, _ = eth.decode_transfer_log(lg)
            if transfer is not None:
                transfers[f"log:{lg.log_index}"] = [
                    transfer.token.address if transfer.token else None,
                    transfer.from_address.address if transfer.from_address else None,
                    transfer.to_address.address if transfer.to_address else None,
                    str(transfer.amount_raw),
                ]
    return {
        "block_number": tx.block_number,
        "slot": None,
        "block_hash": tx.block_hash,
        "status": tx.status.value if tx.status else None,
        "from": tx.from_address,
        "to": tx.to_address,
        "value_raw": str(parsed.value_wei),
        "fee_raw": None,
        "block_time": None,
        "created_contract": tx.created_contract,
        "transfers": transfers,
    }


def _sol_fields(raw: object, src: SourceInfo, wallet: str) -> J:
    parsed = sol.parse_transaction(raw, wallet=wallet, src=src, commitment="finalized")
    tx = parsed.tx
    return {
        "block_number": None,
        "slot": tx.slot,
        "block_hash": None,
        "status": tx.status.value if tx.status else None,
        "from": tx.fee_payer,
        "to": None,
        "value_raw": None,
        "fee_raw": str(tx.fee_lamports) if tx.fee_lamports is not None else None,
        "block_time": iso(tx.block_time),
        "created_contract": None,
        "transfers": {
            "ix:" + ".".join(str(i) for i in t.instruction_path or ()): [
                t.token.address if t.token else None,
                t.from_address.address if t.from_address else None,
                t.to_address.address if t.to_address else None,
                str(t.amount_raw),
            ]
            for t, _ in parsed.transfers
        },
    }


def differences(stored: J, fetched: J) -> list[J]:
    """Fields where both sides have a value and the values differ."""
    out: list[J] = []
    for f in COMPARED_FIELDS:
        a, b = stored.get(f), fetched.get(f)
        if a is not None and b is not None and str(a) != str(b):
            out.append({"field": f, "stored": a, "provider": b})
    fetched_tr: dict[str, list[Any]] = fetched.get("transfers") or {}
    stored_tr: dict[str, list[Any]] = stored.get("transfers") or {}
    for key, value in stored_tr.items():
        other = fetched_tr.get(key)
        if other is None:
            out.append({"field": f"transfer {key}", "stored": value, "provider": None})
        elif [str(x) for x in other] != [str(x) for x in value]:
            out.append({"field": f"transfer {key}", "stored": value, "provider": other})
    return out


class Corroborator:
    def __init__(self, repo: Repository, registry: Registry, router: ProviderRouter) -> None:
        self._repo = repo
        self._registry = registry
        self._router = router
        self._store = ObservationStore(repo)

    def _others(self, chain: str, source_id: str) -> tuple[list[str], J]:
        method = "eth_getTransactionByHash" if chain == "ethereum" else "getTransaction"
        views, reasons = self._router.eligible(chain, method)
        try:
            source = self._registry.get(source_id)
        except Exception:  # noqa: BLE001 - provider no longer registered
            source = None
        chosen: list[str] = []
        groups: set[str] = {source.infrastructure} if source and source.infrastructure else set()
        skipped: J = dict(reasons)
        for v in views:
            spec = v.spec
            if chain == "ethereum" and "eth_getTransactionReceipt" not in spec.methods:
                skipped[spec.id] = "eth_getTransactionReceipt not supported"
                continue
            if spec.infrastructure is None or spec.infrastructure in groups:
                skipped[spec.id] = "shares infrastructure with an already selected source"
                continue
            if source is not None and not independent(spec, source):
                skipped[spec.id] = "not independent of the original source"
                continue
            chosen.append(spec.id)
            groups.add(spec.infrastructure)
            if len(chosen) >= MAX_OTHER_PROVIDERS:
                break
        return chosen, skipped

    def verify(self, chain: str, tx_id: str) -> J:
        stored, row = stored_observation(self._repo, chain, tx_id)
        prov = Provenance.model_validate_json(row["provenance_json"])
        source_id = prov.source_provider
        source_spec = next((s for s in self._registry.all() if s.id == source_id), None)
        others, skipped = self._others(chain, source_id)
        result: J = {
            "chain": chain,
            "tx_id": tx_id,
            "source_provider": source_id,
            "source_infrastructure": source_spec.infrastructure if source_spec else None,
            "stored": stored,
            "skipped_providers": skipped,
        }
        if not others:
            return {
                **result,
                "status": "could_not_run",
                "outcome": None,
                "reason": "No provider on independent infrastructure is eligible. "
                "Configure a second, independent provider to corroborate evidence.",
                "observations": [],
            }
        budget = CallBudget.create(CALL_BUDGET, DEADLINE_S)
        observations: list[J] = []
        for pid in others:
            spec = self._registry.get(pid)
            obs: J = {
                "provider_id": pid,
                "infrastructure": spec.infrastructure,
                "fetched_at": utcnow(),
                "found": False,
                "fields": None,
                "error": None,
                "raw_ref": None,
            }
            try:
                if chain == "ethereum":
                    a = EthereumAdapter(
                        self._router, raw_sink=self._store, budget=budget, provider_id=pid
                    )
                    raw_tx, src = a.transaction(tx_id)
                    obs["raw_ref"] = src.raw_sha256
                    if raw_tx is not None:
                        raw_receipt, _ = a.receipt(tx_id)
                        obs["found"] = True
                        obs["fields"] = _eth_fields(raw_tx, raw_receipt, src)
                else:
                    s = SolanaAdapter(
                        self._router, raw_sink=self._store, budget=budget, provider_id=pid
                    )
                    raw, src = s.transaction(tx_id, "finalized")
                    obs["raw_ref"] = src.raw_sha256
                    if raw is not None:
                        obs["found"] = True
                        obs["fields"] = _sol_fields(raw, src, stored["from"] or "")
            except ParseError as exc:
                # An answer BIP cannot parse is a failed lookup, not evidence of disagreement.
                obs["found"] = False
                obs["fields"] = None
                obs["error"] = f"parse_error ({exc.field[:60]})"
            except (
                RpcError,
                TransportError,
                RoutingFailedError,
                NoEligibleProviderError,
                BudgetExhaustedError,
            ) as exc:
                # Only BIP-defined classifications are kept: provider-supplied message text could
                # echo request URLs or keys, so it is never stored or returned.
                kind = getattr(exc, "error_class", None) or getattr(exc, "kind", None)
                obs["error"] = f"{type(exc).__name__}" + (f" ({kind})" if kind else "")
            observations.append(obs)
        answered = [o for o in observations if o["error"] is None]
        if not answered:
            return {
                **result,
                "status": "could_not_run",
                "outcome": None,
                "reason": "No independent provider answered.",
                "observations": [_public(o) for o in observations],
            }
        outcome, note, diffs, n_independent = _decide(
            stored, answered, source_spec is not None and source_spec.infrastructure is not None
        )
        with self._repo.transaction() as conn:
            vid = conn.execute(
                insert(schema.evidence_verifications).values(
                    chain=chain,
                    tx_id=tx_id,
                    checked_at=utcnow(),
                    outcome=outcome,
                    independent_providers=n_independent,
                    providers_json=json.dumps([source_id, *others]),
                    differences_json=json.dumps(diffs, default=str),
                    note=note[:300],
                )
            ).inserted_primary_key
            assert vid is not None  # noqa: S101
            for o in observations:
                conn.execute(
                    insert(schema.provider_observations).values(
                        chain=chain,
                        tx_id=tx_id,
                        provider_id=o["provider_id"],
                        infrastructure=o["infrastructure"],
                        fetched_at=o["fetched_at"],
                        found=o["found"],
                        fields_json=json.dumps(
                            o["fields"]
                            if o["fields"] is not None
                            else {"lookup_failed": o["error"] is not None, "error": o["error"]},
                            default=str,
                        ),
                        raw_ref=o["raw_ref"],
                        verification_id=int(vid[0]),
                    )
                )
        return {
            **result,
            "status": "checked",
            "outcome": outcome,
            "label": label(outcome),
            "note": note,
            "differences": diffs,
            "independent_providers": n_independent,
            "observations": [_public(o) for o in observations],
        }


def _public(o: J) -> J:
    return {**o, "fetched_at": iso(o["fetched_at"])}


def _decide(stored: J, answered: list[J], source_counts: bool) -> tuple[str, str, list[J], int]:
    found = [o for o in answered if o["found"]]
    missing = [o for o in answered if not o["found"]]
    if not found:
        # Not invalidated: provider history depth is not established by any capability record
        # (CLAUDE.md rule 3), so "not found" may simply be outside their history.
        return (
            "unverified",
            f"{len(missing)} independent provider(s) did not return the transaction; this "
            "can be a history limit, so it is neither a conflict nor an invalidation.",
            [],
            0,
        )
    diffs: list[J] = []
    agree: list[J] = []
    for o in found:
        d = (
            differences(stored, o["fields"])
            if o["fields"] is not None
            else [{"field": "parse", "stored": None, "provider": o["error"]}]
        )
        if d:
            diffs.append({"provider_id": o["provider_id"], "differences": d})
        else:
            agree.append(o)
    if diffs:
        hashes = {str(o["fields"].get("block_hash")) for o in found if o["fields"]}
        if (
            not agree
            and len(found) >= 2
            and len(hashes) == 1
            and stored.get("block_hash") not in hashes
            and "None" not in hashes
        ):
            return (
                "invalidated",
                "Independent providers agree on a different block hash: the "
                "stored block was likely reorganized away.",
                diffs,
                len(found),
            )
        return (
            "conflict",
            "An independent provider reported different data. Both versions are preserved.",
            diffs,
            len(found),
        )
    n = len({o["infrastructure"] for o in agree}) + (1 if source_counts else 0)
    if n >= 2:
        return (
            "cross_verified",
            f"{n} independent infrastructure groups report identical normalized fields.",
            [],
            n,
        )
    return ("unverified", "Only one infrastructure group could be compared.", [], n)
