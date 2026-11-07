// Explorer links are built only from identifiers that pass strict format checks, so API data can
// never inject a URL. Etherscan and the official Solana explorer use these documented paths.
const EVM_HASH = /^0x[0-9a-f]{64}$/;
const EVM_ADDR = /^0x[0-9a-f]{40}$/;
const B58 = /^[1-9A-HJ-NP-Za-km-z]{32,88}$/;

export function txUrl(chain: string, id: string): string | null {
  if (chain === "ethereum" && EVM_HASH.test(id)) return `https://etherscan.io/tx/${id}`;
  if (chain === "solana" && B58.test(id)) return `https://explorer.solana.com/tx/${id}`;
  return null;
}

export function addressUrl(chain: string, id: string): string | null {
  if (chain === "ethereum" && EVM_ADDR.test(id)) return `https://etherscan.io/address/${id}`;
  if (chain === "solana" && B58.test(id)) return `https://explorer.solana.com/address/${id}`;
  return null;
}
