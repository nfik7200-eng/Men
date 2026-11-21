"""Synthetic, clearly artificial datasets for detector and rule tests (no network, no DB)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bip.intel.dataset import TransferRow, TxRow, WalletDataset

WALLET = "0x" + "aa" * 20
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def a(n: int) -> str:
    return "0x" + f"{n:040x}"


class Builder:
    def __init__(self, chain: str = "ethereum", wallet: str = WALLET) -> None:
        self.ds = WalletDataset(chain=chain, address=wallet, owned_accounts=frozenset(), runs=1)
        self._n = 0

    def tx(
        self,
        at: datetime,
        *,
        to: str | None = None,
        frm: str | None = None,
        verification: str = "unverified",
        programs: tuple[str, ...] = (),
        created: str | None = None,
    ) -> str:
        self._n += 1
        tx_id = f"0x{self._n:064x}"
        self.ds.transactions.append(
            TxRow(
                tx_id=tx_id,
                block_number=1000 + self._n,
                slot=None,
                block_hash=f"0x{self._n:064x}",
                block_time=at,
                from_address=frm or self.ds.address,
                to_address=to,
                status="success",
                parse_status="complete",
                finality="finalized",
                program_ids=programs,
                provider="fake",
                verification=verification,
                created_contract=created,
            )
        )
        return tx_id

    def transfer(
        self,
        at: datetime,
        *,
        direction: str,
        party: str,
        amount: int,
        asset: str = "native",
        decimals: int | None = 18,
        standard: str = "native",
        verification: str = "unverified",
    ) -> str:
        tx_id = self.tx(
            at,
            to=party if direction == "out" else None,
            frm=self.ds.address if direction == "out" else party,
            verification=verification,
        )
        frm, to = (self.ds.address, party) if direction == "out" else (party, self.ds.address)
        self.ds.transfers.append(
            TransferRow(
                tx_id=tx_id,
                event_key="native" if asset == "native" else "log:0",
                kind="native_tx_value",
                standard=standard,
                asset=asset,
                from_address=frm,
                to_address=to,
                amount_raw=amount,
                decimals=decimals,
                block_time=at,
                position=1000 + self._n,
                direction=direction,
                counterparty=party,
                verification=verification,
                provider="fake",
            )
        )
        return tx_id


def daily(builder: Builder, days: int, per_day: int, start: datetime = T0) -> None:
    for d in range(days):
        for i in range(per_day):
            builder.tx(start + timedelta(days=d, hours=i), to=a(500 + d))
