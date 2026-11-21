// Recently opened wallets, kept in this browser only (a convenience; addresses, never data).
// Every storage access is guarded: private mode or blocked storage must not break the UI.
const KEY = "bip.recentWallets";
const MAX = 8;

export type RecentWallet = { chain: string; address: string };

export function recentWallets(): RecentWallet[] {
  try {
    const raw = localStorage.getItem(KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((w): w is RecentWallet => typeof w === "object" && w !== null && typeof (w as RecentWallet).chain === "string" && typeof (w as RecentWallet).address === "string")
      .slice(0, MAX);
  } catch {
    return [];
  }
}

export function rememberWallet(chain: string, address: string): void {
  try {
    const next = [{ chain, address }, ...recentWallets().filter((w) => !(w.chain === chain && w.address === address))].slice(0, MAX);
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    /* storage unavailable: recent list is optional */
  }
}

export function clearRecent(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}
