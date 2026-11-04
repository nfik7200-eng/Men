"""Provider capability catalogue: what the prober can test, with which request, in what order.

Each capability maps to one read-only method that BIP's adapters actually use (or to a property
derived from another capability's response, at no extra request cost). Order is priority: when a
per-provider request cap is lower than the catalogue length, the lowest-priority requestable
capabilities are recorded as ``not_tested`` with the reason.

Statuses (every observation carries an explanation):

* ``supported``: the method returned a response that passed validation for the tested request;
* ``unsupported``: the provider explicitly reported the method as not available (JSON-RPC
  -32601). Nothing else is ever converted into "unsupported";
* ``inconclusive``: the request failed in a way that establishes neither support nor
  non-support (network, timeout, rate limit, auth, invalid parameters, missing data,
  malformed or contradictory response);
* ``not_tested``: deliberately skipped (budget, missing safe sample, stopped run, not selected).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Chain = Literal["ethereum", "solana"]
CapabilityStatus = Literal["supported", "unsupported", "inconclusive", "not_tested"]
STATUSES: tuple[CapabilityStatus, ...] = ("supported", "unsupported", "inconclusive", "not_tested")

# Public, non-personal protocol addresses used for balance / code / history reads.
ETH_PROBE_ADDRESS = "0x0000000000000000000000000000000000000000"
SOL_PROBE_ADDRESS = "11111111111111111111111111111111"  # System Program


@dataclass(frozen=True, slots=True)
class Capability:
    id: str
    chain: Chain
    label: str
    method: str | None
    """``None``: derived from ``derived_from``'s response (no request of its own)."""
    description: str
    requires: str | None = None
    """What safe sample the request needs (explains ``not_tested`` when it is missing)."""
    derived_from: str | None = None


ETHEREUM: tuple[Capability, ...] = (
    Capability(
        "chain_id",
        "ethereum",
        "Chain identification",
        "eth_chainId",
        "eth_chainId must return 0x1 (Ethereum mainnet).",
    ),
    Capability(
        "latest_block",
        "ethereum",
        "Latest block",
        "eth_getBlockByNumber",
        "Header of the 'latest' block (number, hash, parent hash, timestamp).",
    ),
    Capability(
        "finalized_block",
        "ethereum",
        "Finalized block",
        "eth_getBlockByNumber",
        "Header of the 'finalized' block tag.",
    ),
    Capability(
        "historical_block",
        "ethereum",
        "Historical block sample",
        "eth_getBlockByNumber",
        "One block a fixed distance below the head (rounded down to a multiple of 1,000). "
        "One sample is not proof of full archive access.",
        requires="the head block number from 'latest_block'",
    ),
    Capability(
        "transaction_lookup",
        "ethereum",
        "Transaction lookup",
        "eth_getTransactionByHash",
        "Look up a transaction hash taken from the latest block this provider returned.",
        requires="a transaction hash listed in the 'latest_block' response",
    ),
    Capability(
        "transaction_receipt",
        "ethereum",
        "Transaction receipt",
        "eth_getTransactionReceipt",
        "Receipt for the same transaction hash.",
        requires="a transaction hash listed in the 'latest_block' response",
    ),
    Capability(
        "event_logs",
        "ethereum",
        "Event log retrieval",
        "eth_getLogs",
        "eth_getLogs over exactly one block (the latest observed), filtered to the "
        "ERC-20/721 Transfer topic, response capped by the probe size limit.",
        requires="the head block number from 'latest_block'",
    ),
    Capability(
        "erc20_transfer_logs",
        "ethereum",
        "ERC-20 Transfer logs",
        None,
        "Transfer logs with 3 topics (ERC-20 shape) decodable by BIP.",
        derived_from="event_logs",
    ),
    Capability(
        "erc721_transfer_logs",
        "ethereum",
        "ERC-721 Transfer logs",
        None,
        "Transfer logs with 4 topics (ERC-721 shape) decodable by BIP.",
        derived_from="event_logs",
    ),
    Capability(
        "native_balance",
        "ethereum",
        "ETH balance read",
        "eth_getBalance",
        f"Balance of the zero address {ETH_PROBE_ADDRESS} at 'latest'.",
    ),
    Capability(
        "block_number",
        "ethereum",
        "Head block number",
        "eth_blockNumber",
        "eth_blockNumber (used by ingestion to bound log scans).",
    ),
    Capability(
        "contract_code",
        "ethereum",
        "Contract bytecode",
        "eth_getCode",
        f"eth_getCode for {ETH_PROBE_ADDRESS} at 'latest' (a valid empty-code answer "
        "still shows the method works).",
    ),
)

SOLANA: tuple[Capability, ...] = (
    Capability(
        "network_identity",
        "solana",
        "Network identification",
        "getGenesisHash",
        "getGenesisHash must return the mainnet-beta genesis hash.",
    ),
    Capability(
        "current_slot", "solana", "Current slot", "getSlot", "getSlot at 'finalized' commitment."
    ),
    Capability(
        "finalized_block",
        "solana",
        "Finalized block",
        "getBlock",
        "getBlock for the finalized slot (no transaction details, no rewards).",
        requires="the finalized slot from 'current_slot'",
    ),
    Capability(
        "historical_block",
        "solana",
        "Historical block sample",
        "getBlock",
        "getBlock a fixed distance below the finalized slot (rounded down to 1,000). "
        "A skipped slot is not a failure; one sample is not proof of archive access.",
        requires="the finalized slot from 'current_slot'",
    ),
    Capability(
        "signature_history",
        "solana",
        "Signature history",
        "getSignaturesForAddress",
        f"getSignaturesForAddress for the System Program ({SOL_PROBE_ADDRESS}), limit 1.",
    ),
    Capability(
        "transaction_lookup",
        "solana",
        "Transaction retrieval",
        "getTransaction",
        "getTransaction for the signature returned by 'signature_history'.",
        requires="a signature returned live by 'signature_history'",
    ),
    Capability(
        "native_transfer_data",
        "solana",
        "Native SOL transfer data",
        None,
        "System-program transfer instructions BIP can decode in the sample transaction.",
        derived_from="transaction_lookup",
    ),
    Capability(
        "spl_transfer_data",
        "solana",
        "SPL token transfer data",
        None,
        "SPL Token transfer instructions BIP can decode in the sample transaction.",
        derived_from="transaction_lookup",
    ),
    Capability(
        "token_balance_changes",
        "solana",
        "Token balance changes",
        None,
        "pre/postTokenBalances metadata present in the sample transaction.",
        derived_from="transaction_lookup",
    ),
    Capability(
        "native_balance",
        "solana",
        "Native balance read",
        "getBalance",
        f"getBalance for the System Program ({SOL_PROBE_ADDRESS}).",
    ),
    Capability("health", "solana", "Node health", "getHealth", "getHealth."),
    Capability("version", "solana", "Node version", "getVersion", "getVersion."),
    Capability(
        "block_height",
        "solana",
        "Block height",
        "getBlockHeight",
        "getBlockHeight at 'finalized' commitment.",
    ),
    Capability(
        "latest_blockhash",
        "solana",
        "Latest blockhash",
        "getLatestBlockhash",
        "getLatestBlockhash at 'finalized' commitment.",
    ),
)

CATALOG: dict[str, tuple[Capability, ...]] = {"ethereum": ETHEREUM, "solana": SOLANA}


def catalog(chain: str) -> tuple[Capability, ...]:
    return CATALOG.get(chain, ())


def capability_ids(chain: str) -> set[str]:
    return {c.id for c in catalog(chain)}


def requestable(chain: str) -> tuple[Capability, ...]:
    return tuple(c for c in catalog(chain) if c.method is not None)


def derived(chain: str, parent: str) -> tuple[Capability, ...]:
    return tuple(c for c in catalog(chain) if c.derived_from == parent)
