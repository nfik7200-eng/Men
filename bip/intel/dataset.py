"""Loading one wallet's stored observations as an explicitly scoped, hashable dataset."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, or_, select

from bip.contracts.provenance import Provenance
from bip.db import schema
from bip.db.canonical import canonical_transaction, orphaned_tx_ids
from bip.db.repository import Repository, canonical_address

# Display names for stored verification states (Stage 3 terminology).
VERIFICATION_LABEL = {
    "unverified": "unverified",
    "single_source": "unverified",
    "cross_verified": "corroborated",
    "conflict": "conflicted",
    "invalidated": "invalidated",
}


@dataclass(frozen=True, slots=True)
class TxRow:
    tx_id: str
    block_number: int | None
    slot: int | None
    block_hash: str | None
    block_time: datetime | None
    from_address: str | None
    to_address: str | None
    status: str | None
    parse_status: str
    finality: str | None
    program_ids: tuple[str, ...]
    provider: str
    verification: str  # effective, after corroboration checks
    value_raw: int | None = None
    fee_raw: int | None = None
    created_contract: str | None = None
    instruction_count: int | None = None
    unknown_instruction_count: int | None = None

    @property
    def position(self) -> int | None:
        return self.block_number if self.block_number is not None else self.slot


@dataclass(frozen=True, slots=True)
class TransferRow:
    tx_id: str
    event_key: str
    kind: str
    standard: str
    asset: str  # "native" or token address / mint
    from_address: str | None
    to_address: str | None
    amount_raw: int
    decimals: int | None
    block_time: datetime | None
    position: int | None
    direction: str  # "in", "out", "self" or "other" relative to the wallet
    counterparty: str | None
    verification: str
    provider: str = ""
    counterparty_owner: str | None = None
    """For Solana token accounts: the owner observed in balance metadata (only if unambiguous)."""

    @property
    def party(self) -> str | None:
        """Counterparty as an owner address when known, else the raw (token) account."""
        return self.counterparty_owner or self.counterparty


@dataclass(frozen=True, slots=True)
class BalanceRow:
    tx_id: str
    account: str
    mint: str | None
    kind: str
    delta_raw: int | None
    decimals: int | None
    block_time: datetime | None


@dataclass
class WalletDataset:
    chain: str
    address: str
    owned_accounts: frozenset[str]
    transactions: list[TxRow] = field(default_factory=list[TxRow])
    transfers: list[TransferRow] = field(default_factory=list[TransferRow])
    balance_changes: list[BalanceRow] = field(default_factory=list[BalanceRow])
    truncated: bool = False
    runs: int = 0
    last_run_at: datetime | None = None
    cursor: dict[str, object] = field(default_factory=dict[str, object])

    @property
    def is_mine(self) -> frozenset[str]:
        return self.owned_accounts | {self.address}

    def input_hash(self, params: dict[str, object]) -> str:
        """Hash of every input record identity + verification state + params (reproducibility)."""
        h = hashlib.sha256()
        h.update(json.dumps(params, sort_keys=True, default=str).encode())
        for t in self.transactions:
            h.update(f"T|{t.tx_id}|{t.block_hash}|{t.verification}|{t.parse_status}".encode())
        for r in self.transfers:
            h.update(f"R|{r.tx_id}|{r.event_key}|{r.amount_raw}|{r.verification}".encode())
        for b in self.balance_changes:
            h.update(f"B|{b.tx_id}|{b.account}|{b.mint}|{b.kind}|{b.delta_raw}".encode())
        return h.hexdigest()


def effective_verification(repo: Repository, chain: str, tx_ids: list[str]) -> dict[str, str]:
    """Latest corroboration outcome per transaction (default: the stored provenance status)."""
    if not tx_ids:
        return {}
    v = schema.evidence_verifications
    out: dict[str, str] = {}
    with repo.transaction() as conn:
        for start in range(0, len(tx_ids), 500):
            chunk = tx_ids[start : start + 500]
            latest = (
                select(v.c.tx_id, func.max(v.c.id).label("mid"))
                .where(v.c.chain == chain, v.c.tx_id.in_(chunk))
                .group_by(v.c.tx_id)
                .subquery()
            )
            rows = conn.execute(
                select(v.c.tx_id, v.c.outcome).join(latest, v.c.id == latest.c.mid)
            ).all()
            out.update({r[0]: r[1] for r in rows})
    return out


def account_owners(repo: Repository, chain: str, accounts: set[str]) -> dict[str, str]:
    """Owner per token account, only where stored balance metadata names exactly one owner."""
    if not accounts:
        return {}
    bc = schema.balance_changes
    seen: dict[str, set[str]] = {}
    items = sorted(accounts)
    with repo.transaction() as conn:
        for start in range(0, len(items), 500):
            rows = conn.execute(
                select(bc.c.account, bc.c.owner)
                .where(
                    bc.c.chain == chain,
                    bc.c.account.in_(items[start : start + 500]),
                    bc.c.owner.is_not(None),
                    bc.c.account != bc.c.owner,
                    bc.c.tx_id.not_in(orphaned_tx_ids(chain)),
                )
                .distinct()
            ).all()
            for acct, owner in rows:
                seen.setdefault(acct, set()).add(owner)
    return {a: next(iter(o)) for a, o in seen.items() if len(o) == 1}


def load_wallet(repo: Repository, chain: str, address: str, max_records: int) -> WalletDataset:
    ref = canonical_address(chain, address)
    t, tr, bc, wa = (
        schema.transactions,
        schema.transfers,
        schema.balance_changes,
        schema.wallet_activity,
    )
    orphans = orphaned_tx_ids(ref.chain)
    with repo.transaction() as conn:
        owned = frozenset(
            r[0]
            for r in conn.execute(
                select(bc.c.account)
                .where(
                    bc.c.chain == ref.chain,
                    bc.c.owner == ref.address,
                    bc.c.account != ref.address,
                    bc.c.tx_id.not_in(orphans),
                )
                .distinct()
            ).all()
        )
        mine = list(owned | {ref.address})
        linked = select(wa.c.tx_id).where(wa.c.chain == ref.chain, wa.c.address == ref.address)
        involved = select(tr.c.tx_id).where(
            tr.c.chain == ref.chain, or_(tr.c.from_address.in_(mine), tr.c.to_address.in_(mine))
        )
        bal = select(bc.c.tx_id).where(
            bc.c.chain == ref.chain, or_(bc.c.account == ref.address, bc.c.owner == ref.address)
        )
        tx_cond = (
            (t.c.chain == ref.chain)
            & canonical_transaction()
            & or_(
                t.c.from_address == ref.address,
                t.c.to_address == ref.address,
                t.c.tx_id.in_(linked),
                t.c.tx_id.in_(involved),
                t.c.tx_id.in_(bal),
            )
        )
        tx_rows = (
            conn.execute(
                select(t)
                .where(tx_cond)
                .order_by(t.c.block_number.desc(), t.c.slot.desc(), t.c.id.desc())
                .limit(max_records + 1)
            )
            .mappings()
            .all()
        )
        tr_rows = (
            conn.execute(
                select(tr)
                .where(
                    tr.c.chain == ref.chain,
                    or_(tr.c.from_address.in_(mine), tr.c.to_address.in_(mine)),
                    tr.c.tx_id.not_in(orphans),
                )
                .order_by(tr.c.block_number.desc(), tr.c.slot.desc(), tr.c.id.desc())
                .limit(max_records + 1)
            )
            .mappings()
            .all()
        )
        bc_rows = (
            conn.execute(
                select(bc)
                .where(
                    bc.c.chain == ref.chain,
                    or_(bc.c.account == ref.address, bc.c.owner == ref.address),
                    bc.c.tx_id.not_in(orphans),
                )
                .order_by(bc.c.slot.desc(), bc.c.id.desc())
                .limit(max_records + 1)
            )
            .mappings()
            .all()
        )
        runs = conn.execute(
            select(func.count(), func.max(schema.ingestion_runs.c.started_at)).where(
                schema.ingestion_runs.c.chain == ref.chain,
                schema.ingestion_runs.c.address == ref.address,
            )
        ).one()
        cur = (
            conn.execute(
                select(schema.wallet_cursors).where(
                    schema.wallet_cursors.c.chain == ref.chain,
                    schema.wallet_cursors.c.address == ref.address,
                )
            )
            .mappings()
            .first()
        )
    truncated = (
        len(tx_rows) > max_records or len(tr_rows) > max_records or len(bc_rows) > max_records
    )
    tx_rows, tr_rows, bc_rows = tx_rows[:max_records], tr_rows[:max_records], bc_rows[:max_records]
    verification = effective_verification(repo, ref.chain, [r["tx_id"] for r in tx_rows])
    ds = WalletDataset(
        chain=ref.chain,
        address=ref.address,
        owned_accounts=owned,
        truncated=truncated,
        runs=int(runs[0]),
        last_run_at=runs[1],
        cursor=dict(cur) if cur else {},
    )
    tx_verif: dict[str, str] = {}
    for r in tx_rows:
        prov = Provenance.model_validate_json(r["provenance_json"])
        status = verification.get(r["tx_id"], prov.verification_status.value)
        tx_verif[r["tx_id"]] = status
        ds.transactions.append(
            TxRow(
                tx_id=r["tx_id"],
                block_number=r["block_number"],
                slot=r["slot"],
                block_hash=r["block_hash"],
                block_time=r["block_time"],
                from_address=r["from_address"],
                to_address=r["to_address"],
                status=r["status"],
                parse_status=r["parse_status"],
                finality=r["finality"],
                program_ids=tuple(json.loads(r["program_ids_json"]))
                if r["program_ids_json"]
                else (),
                provider=prov.source_provider,
                verification=status,
                value_raw=_int(r["value_raw"]),
                fee_raw=_int(r["fee_raw"]),
                created_contract=r["created_contract"],
                instruction_count=r["instruction_count"],
                unknown_instruction_count=r["unknown_instruction_count"],
            )
        )
    mine_set = set(mine)
    owners = account_owners(
        repo,
        ref.chain,
        {
            a
            for r in tr_rows
            for a in (r["from_address"], r["to_address"])
            if a and a not in mine_set
        },
    )
    for r in tr_rows:
        frm, to = r["from_address"], r["to_address"]
        f_mine, t_mine = frm in mine_set, to in mine_set
        direction = (
            "self" if f_mine and t_mine else "out" if f_mine else "in" if t_mine else "other"
        )
        counterparty = to if direction == "out" else frm if direction == "in" else None
        ds.transfers.append(
            TransferRow(
                tx_id=r["tx_id"],
                event_key=r["event_key"],
                kind=r["observation_kind"],
                standard=r["standard"],
                asset=r["token"] or "native",
                from_address=frm,
                to_address=to,
                amount_raw=int(r["amount_raw"]),
                decimals=r["decimals"],
                block_time=r["block_time"],
                position=r["block_number"] if r["block_number"] is not None else r["slot"],
                direction=direction,
                counterparty=counterparty,
                verification=tx_verif.get(r["tx_id"]) or verification.get(r["tx_id"], "unverified"),
                provider=Provenance.model_validate_json(r["provenance_json"]).source_provider,
                counterparty_owner=owners.get(counterparty) if counterparty else None,
            )
        )
    for r in bc_rows:
        ds.balance_changes.append(
            BalanceRow(
                tx_id=r["tx_id"],
                account=r["account"],
                mint=r["mint"],
                kind=r["observation_kind"],
                delta_raw=int(r["delta_raw"]) if r["delta_raw"] is not None else None,
                decimals=r["decimals"],
                block_time=r["block_time"],
            )
        )
    return ds


def _int(v: object) -> int | None:
    return int(v) if isinstance(v, str) and v.lstrip("-").isdigit() else None


def scale(raw: int, decimals: int | None) -> str | None:
    """Human amount from raw units; ``None`` when decimals are unknown (never assumed)."""
    if decimals is None:
        return None
    return format(Decimal(raw).scaleb(-decimals).normalize(), "f")
