import { base58DecodedLength } from "./address";

export type SearchChain = "ethereum" | "solana";
export type QueryKind =
  | { kind: "wallet"; chain: SearchChain; address: string }
  | { kind: "transaction"; chain: SearchChain }
  | { kind: "invalid"; message: string }
  | { kind: "empty" };

const BASE58 = /^[1-9A-HJ-NP-Za-km-z]+$/;

/**
 * Recognise what a pasted search string is, by format only (no network call).
 * Ethereum addresses are lowercased; BIP does not verify EIP-55 checksums.
 */
export function classifyQuery(input: string): QueryKind {
  const value = input.trim();
  if (!value) return { kind: "empty" };
  if (/^0x[0-9a-fA-F]{40}$/.test(value)) return { kind: "wallet", chain: "ethereum", address: value.toLowerCase() };
  if (/^0x[0-9a-fA-F]{64}$/.test(value)) return { kind: "transaction", chain: "ethereum" };
  if (BASE58.test(value)) {
    const bytes = base58DecodedLength(value);
    if (value.length >= 32 && value.length <= 44 && bytes === 32) return { kind: "wallet", chain: "solana", address: value };
    if (value.length >= 86 && value.length <= 88 && bytes === 64) return { kind: "transaction", chain: "solana" };
  }
  return {
    kind: "invalid",
    message: "Enter an Ethereum address (0x + 40 hex characters) or a Solana address (base58, 32 bytes).",
  };
}
