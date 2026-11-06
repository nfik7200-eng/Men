import { afterEach, describe, expect, it, vi } from "vitest";
import { clearRecent, recentWallets, rememberWallet } from "./recent";

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("recent wallets", () => {
  it("keeps the most recent first, without duplicates, at most 8", () => {
    for (let i = 0; i < 10; i++) rememberWallet("ethereum", `0x${String(i).repeat(40)}`);
    rememberWallet("ethereum", `0x${"3".repeat(40)}`);
    const list = recentWallets();
    expect(list).toHaveLength(8);
    expect(list[0]).toEqual({ chain: "ethereum", address: `0x${"3".repeat(40)}` });
    expect(new Set(list.map((w) => w.address)).size).toBe(8);
    clearRecent();
    expect(recentWallets()).toEqual([]);
  });

  it("never throws when storage is unavailable or corrupted", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("denied", "SecurityError");
    });
    expect(() => rememberWallet("solana", "x")).not.toThrow();
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue("{not json");
    expect(recentWallets()).toEqual([]);
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("denied", "SecurityError");
    });
    expect(recentWallets()).toEqual([]);
    expect(() => clearRecent()).not.toThrow();
  });
});
