// Client-side validation mirroring bip/contracts (the backend re-validates every request).
// EVM: 0x + 40 hex characters. Solana: base58 that decodes to exactly 32 bytes.

const BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";

export const CHAINS = [
  { id: "ethereum", label: "Ethereum", family: "evm" },
  { id: "solana", label: "Solana", family: "solana" },
] as const;

export type ChainId = (typeof CHAINS)[number]["id"];

export function base58DecodedLength(value: string): number | null {
  if (!value) return null;
  let n = 0n;
  for (const ch of value) {
    const idx = BASE58.indexOf(ch);
    if (idx < 0) return null;
    n = n * 58n + BigInt(idx);
  }
  let bytes = 0;
  while (n > 0n) {
    n >>= 8n;
    bytes += 1;
  }
  const leadingOnes = value.length - value.replace(/^1+/, "").length;
  return bytes + leadingOnes;
}

/** Returns null when valid, otherwise a short explanation. */
export function validateAddress(chain: string, address: string): string | null {
  const value = address.trim();
  if (!value) return "Enter an address.";
  if (chain === "ethereum") {
    return /^0x[0-9a-fA-F]{40}$/.test(value)
      ? null
      : "Ethereum addresses are 0x followed by 40 hexadecimal characters.";
  }
  if (chain === "solana") {
    if (value.length < 32 || value.length > 44) return "Solana addresses are 32–44 base58 characters.";
    return base58DecodedLength(value) === 32
      ? null
      : "Not a valid Solana address (must be base58 that decodes to 32 bytes).";
  }
  return "Unknown chain.";
}

export function chainLabel(chain: string): string {
  return CHAINS.find((c) => c.id === chain)?.label ?? chain;
}
