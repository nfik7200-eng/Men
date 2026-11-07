from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from bip.contracts.blockchain import (
    KNOWN_CHAINS,
    AddressRef,
    BlockPosition,
    ChainFamily,
    SlotPosition,
    get_chain,
)
from bip.contracts.blocks import EvmBlock, SolanaBlock
from bip.contracts.contracts import ContractDeployment, DeploymentMethod
from bip.contracts.events import EvmLog, SolanaInstruction
from bip.contracts.identifiers import EvmTxRef, SolanaTxRef, b58decode
from bip.contracts.transactions import EvmTransaction, SolanaTransaction, TxStatus
from bip.contracts.transfers import TokenStandard, TokenTransfer, TransferClassification
from tests.conftest import (
    COLLECTED,
    EVM_ADDR_A,
    EVM_ADDR_B,
    EVM_BLOCK_HASH,
    EVM_PARENT_HASH,
    EVM_TOKEN,
    EVM_TX,
    SOL_BLOCKHASH,
    SOL_KEY_A,
    SOL_KEY_B,
    SOL_KEY_C,
    SOL_PREV_BLOCKHASH,
    SOL_SIGNATURE,
    evm_provenance,
    sol_provenance,
)

# ---------- chain identity and identifiers ----------


def test_chain_registry_is_family_aware() -> None:
    assert get_chain("ethereum").position_kind == "block"
    assert get_chain("solana").position_kind == "slot"
    assert {c.family for c in KNOWN_CHAINS.values()} == {ChainFamily.EVM, ChainFamily.SOLANA}
    with pytest.raises(ValueError, match="unknown chain"):
        get_chain("Ethereum")


def test_base58_decoding() -> None:
    assert b58decode(SOL_KEY_A) == bytes(32)
    assert len(b58decode(SOL_SIGNATURE)) == 64
    with pytest.raises(ValueError, match="invalid base58"):
        b58decode("0OIl")


@pytest.mark.parametrize(
    "bad",
    ["0x1234", "ab" * 32, "0x" + "zz" * 32, "0x" + "ab" * 33, ""],
)
def test_bad_evm_hashes(bad: str) -> None:
    with pytest.raises(ValidationError):
        EvmTxRef(tx_hash=bad)


@pytest.mark.parametrize("bad", [SOL_KEY_A, "0" * 88, SOL_SIGNATURE[:-5], EVM_TX])
def test_bad_solana_signatures(bad: str) -> None:
    with pytest.raises(ValidationError):
        SolanaTxRef(signature=bad)


def test_address_ref_validates_per_family() -> None:
    mixed = "0x" + "aB" * 20
    assert AddressRef(chain="ethereum", address=mixed).address == mixed.lower()
    assert AddressRef(chain="base", address=EVM_ADDR_A).address == EVM_ADDR_A
    assert AddressRef(chain="solana", address=SOL_KEY_B).address == SOL_KEY_B
    with pytest.raises(ValidationError, match="20-byte"):
        AddressRef(chain="ethereum", address=SOL_KEY_B)
    with pytest.raises(ValidationError, match="Solana address"):
        AddressRef(chain="solana", address=EVM_ADDR_A)
    with pytest.raises(ValidationError, match="32 bytes"):
        AddressRef(chain="solana", address="1111")


def test_address_ref_has_no_identity_field() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        AddressRef.model_validate({"chain": "ethereum", "address": EVM_ADDR_A, "owner": "Alice"})


def test_positions_are_not_interchangeable() -> None:
    with pytest.raises(ValidationError):
        BlockPosition.model_validate({"kind": "block", "slot": 5})
    with pytest.raises(ValidationError):
        SlotPosition(slot=-1)
    with pytest.raises(ValidationError):
        BlockPosition(block_number="12")  # type: ignore[arg-type]


# ---------- blocks ----------


def test_evm_block_valid_and_unknown_tx_count_stays_none() -> None:
    b = EvmBlock(
        chain="ethereum",
        number=100,
        hash=EVM_BLOCK_HASH,
        parent_hash=EVM_PARENT_HASH,
        timestamp=COLLECTED,
        provenance=evm_provenance(),
    )
    assert b.tx_count is None


def test_evm_block_rejects_solana_chain_and_mismatches() -> None:
    with pytest.raises(ValidationError, match="not a evm chain"):
        EvmBlock(
            chain="solana",
            number=1,
            hash=EVM_BLOCK_HASH,
            parent_hash=EVM_PARENT_HASH,
            timestamp=COLLECTED,
            provenance=sol_provenance(),
        )
    with pytest.raises(ValidationError, match="position does not match"):
        EvmBlock(
            chain="ethereum",
            number=101,
            hash=EVM_BLOCK_HASH,
            parent_hash=EVM_PARENT_HASH,
            timestamp=COLLECTED,
            provenance=evm_provenance(),
        )
    with pytest.raises(ValidationError, match="parent_hash equals hash"):
        EvmBlock(
            chain="ethereum",
            number=100,
            hash=EVM_BLOCK_HASH,
            parent_hash=EVM_BLOCK_HASH,
            timestamp=COLLECTED,
            provenance=evm_provenance(),
        )
    with pytest.raises(ValidationError, match="does not match record chain"):
        EvmBlock(
            chain="base",
            number=100,
            hash=EVM_BLOCK_HASH,
            parent_hash=EVM_PARENT_HASH,
            timestamp=COLLECTED,
            provenance=evm_provenance(),
        )


def test_solana_block_allows_null_block_time_and_skipped_parents() -> None:
    b = SolanaBlock(
        chain="solana",
        slot=250_000_000,
        blockhash=SOL_BLOCKHASH,
        previous_blockhash=SOL_PREV_BLOCKHASH,
        parent_slot=249_999_997,
        provenance=sol_provenance(),
    )
    assert b.block_time is None
    with pytest.raises(ValidationError, match="parent_slot"):
        SolanaBlock(
            chain="solana",
            slot=250_000_000,
            blockhash=SOL_BLOCKHASH,
            previous_blockhash=SOL_PREV_BLOCKHASH,
            parent_slot=250_000_000,
            provenance=sol_provenance(),
        )


# ---------- transactions and events ----------


def evm_tx(**kw: object) -> EvmTransaction:
    data: dict[str, object] = {
        "chain": "ethereum",
        "tx_hash": EVM_TX,
        "block_number": 100,
        "block_hash": EVM_BLOCK_HASH,
        "tx_index": 0,
        "from_address": EVM_ADDR_A,
        "to_address": EVM_ADDR_B,
        "value_wei": 10**18,
        "nonce": 4,
        "provenance": evm_provenance(),
    }
    data.update(kw)
    return EvmTransaction.model_validate(data)


def test_evm_transaction_rules() -> None:
    assert evm_tx().status is None
    assert evm_tx(to_address=None, created_contract=EVM_TOKEN).created_contract == EVM_TOKEN
    assert evm_tx(value_wei=2**256 - 1).value_wei == 2**256 - 1
    with pytest.raises(ValidationError, match="created_contract"):
        evm_tx(created_contract=EVM_TOKEN)
    for bad in (-1, 2**256, 1.5, "1"):
        with pytest.raises(ValidationError):
            evm_tx(value_wei=bad)


def test_solana_transaction_rules() -> None:
    t = SolanaTransaction(
        chain="solana",
        signature=SOL_SIGNATURE,
        slot=250_000_000,
        fee_payer=SOL_KEY_B,
        version=0,
        provenance=sol_provenance(),
    )
    assert t.fee_lamports is None
    assert t.version == 0
    legacy = t.model_copy(update={"version": "legacy"})
    assert legacy.version == "legacy"
    with pytest.raises(ValidationError, match="error is only valid"):
        SolanaTransaction(
            chain="solana",
            signature=SOL_SIGNATURE,
            slot=1,
            fee_payer=SOL_KEY_B,
            status=TxStatus.SUCCESS,
            error="InstructionError",
            provenance=sol_provenance(),
        )
    with pytest.raises(ValidationError):
        SolanaTransaction(
            chain="solana",
            signature=EVM_TX,
            slot=1,
            fee_payer=SOL_KEY_B,
            provenance=sol_provenance(),
        )


def test_evm_log_natural_key_and_topic_limit() -> None:
    log = EvmLog(
        chain="ethereum",
        block_number=100,
        block_hash=EVM_BLOCK_HASH,
        tx_hash=EVM_TX,
        tx_index=0,
        log_index=3,
        address=EVM_TOKEN,
        topics=(EVM_TX,),
        data="0x",
        provenance=evm_provenance(),
    )
    assert log.natural_key == ("ethereum", EVM_BLOCK_HASH, 3)
    with pytest.raises(ValidationError):
        log.model_validate({**log.model_dump(), "topics": (EVM_TX,) * 5})
    with pytest.raises(ValidationError, match="even number"):
        log.model_validate({**log.model_dump(), "data": "0xabc"})


def test_solana_instruction_inner_index_optional() -> None:
    ix = SolanaInstruction(
        chain="solana",
        slot=1,
        signature=SOL_SIGNATURE,
        outer_index=2,
        program_id=SOL_KEY_B,
        accounts=(SOL_KEY_A, SOL_KEY_C),
        provenance=sol_provenance(),
    )
    assert ix.natural_key == ("solana", SOL_SIGNATURE, 2, None)


# ---------- transfers ----------


def erc20(**kw: object) -> TokenTransfer:
    data: dict[str, object] = {
        "chain": "ethereum",
        "tx": EvmTxRef(tx_hash=EVM_TX),
        "position": BlockPosition(block_number=100),
        "log_index": 3,
        "standard": TokenStandard.ERC20,
        "token": AddressRef(chain="ethereum", address=EVM_TOKEN),
        "from_address": AddressRef(chain="ethereum", address=EVM_ADDR_A),
        "to_address": AddressRef(chain="ethereum", address=EVM_ADDR_B),
        "amount_raw": 1_500_000,
        "provenance": evm_provenance(),
    }
    data.update(kw)
    return TokenTransfer.model_validate(data)


def test_transfer_defaults_unclassified_and_unknown_decimals() -> None:
    t = erc20()
    assert t.classification is TransferClassification.UNCLASSIFIED
    assert t.decimals is None
    assert t.amount_decimal() is None
    assert erc20(decimals=6).amount_decimal() == Decimal("1.5")


@pytest.mark.parametrize(
    ("kw", "match"),
    [
        ({"standard": TokenStandard.SPL_TOKEN}, "not valid on"),
        ({"token": None}, "must reference"),
        ({"log_index": None}, "require log_index"),
        ({"instruction_path": (1,)}, "Solana-only"),
        ({"token_id": 5}, "only valid for NFT"),
        ({"standard": TokenStandard.ERC721, "token_id": 5}, "exactly one"),
        ({"standard": TokenStandard.ERC1155}, "require token_id"),
        ({"from_address": None, "to_address": None}, "sender, a recipient"),
        (
            {"to_address": AddressRef(chain="base", address=EVM_ADDR_B)},
            "same chain|transfer's chain",
        ),
        ({"tx": SolanaTxRef(signature=SOL_SIGNATURE)}, "evm_tx_hash"),
        ({"position": SlotPosition(slot=1)}, "block positions"),
        ({"decimals": 256}, "less than or equal"),
    ],
)
def test_invalid_transfers(kw: dict[str, object], match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        erc20(**kw)


def test_native_and_mint_transfers() -> None:
    native = erc20(standard=TokenStandard.NATIVE, token=None, log_index=None)
    assert native.token is None
    mint = erc20(from_address=None, classification=TransferClassification.MINT)
    assert mint.from_address is None
    with pytest.raises(ValidationError, match="must not reference"):
        erc20(standard=TokenStandard.NATIVE)


def test_solana_spl_transfer() -> None:
    t = TokenTransfer(
        chain="solana",
        tx=SolanaTxRef(signature=SOL_SIGNATURE),
        position=SlotPosition(slot=9),
        instruction_path=(1, 0),
        standard=TokenStandard.SPL_TOKEN,
        token=AddressRef(chain="solana", address=SOL_KEY_C),
        from_address=AddressRef(chain="solana", address=SOL_KEY_A),
        to_address=AddressRef(chain="solana", address=SOL_KEY_B),
        amount_raw=42,
        decimals=9,
        provenance=sol_provenance(),
    )
    assert t.amount_decimal() == Decimal("4.2E-8")
    with pytest.raises(ValidationError, match="require instruction_path"):
        t.model_validate({**t.model_dump(), "instruction_path": None})


# ---------- deployments ----------


def test_evm_deployment() -> None:
    d = ContractDeployment(
        chain="ethereum",
        contract=AddressRef(chain="ethereum", address=EVM_TOKEN),
        deployer=AddressRef(chain="ethereum", address=EVM_ADDR_A),
        creation_tx=EvmTxRef(tx_hash=EVM_TX),
        position=BlockPosition(block_number=100),
        method=DeploymentMethod.EVM_TOP_LEVEL_CREATE,
        provenance=evm_provenance(),
    )
    assert d.is_proxy is None
    with pytest.raises(ValidationError, match="factory is only valid"):
        d.model_validate(
            {**d.model_dump(), "factory": {"chain": "ethereum", "address": EVM_ADDR_B}}
        )
    with pytest.raises(ValidationError, match="not valid on"):
        d.model_validate({**d.model_dump(), "method": "solana_program_deploy"})
    with pytest.raises(ValidationError, match="Solana-only"):
        d.model_validate(
            {**d.model_dump(), "upgrade_authority": {"chain": "ethereum", "address": EVM_ADDR_B}}
        )


def test_solana_deployment_deployer_may_be_unknown() -> None:
    d = ContractDeployment(
        chain="solana",
        contract=AddressRef(chain="solana", address=SOL_KEY_C),
        deployer=None,
        creation_tx=SolanaTxRef(signature=SOL_SIGNATURE),
        position=SlotPosition(slot=1),
        method=DeploymentMethod.SOLANA_PROGRAM_DEPLOY,
        upgrade_authority=AddressRef(chain="solana", address=SOL_KEY_B),
        provenance=sol_provenance(),
    )
    assert d.deployer is None
