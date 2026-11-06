import { describe, expect, it } from "vitest";
import { classifyQuery } from "./search";

const SOL = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";
// 64 bytes of 0x01..0x40 in base58: a Solana transaction signature shape
const SIG = "2Ana1pUpv2ZbMVkwF5FXapYeBEjdxDatLn7nvJkhgTSXbs59SyZSx866bXirPgj8QQVB57uxHJBG1YFvkRbFj4T";

describe("classifyQuery", () => {
  it("recognises an Ethereum address, trimming whitespace and lowercasing", () => {
    expect(classifyQuery("  0xABCDEF0123456789ABCDEF0123456789ABCDEF01 \n")).toEqual({
      kind: "wallet",
      chain: "ethereum",
      address: "0xabcdef0123456789abcdef0123456789abcdef01",
    });
  });

  it("recognises a Solana public key and keeps its case", () => {
    expect(classifyQuery(` ${SOL} `)).toEqual({ kind: "wallet", chain: "solana", address: SOL });
  });

  it("recognises transaction hashes and signatures as transactions, not wallets", () => {
    expect(classifyQuery("0x" + "ab".repeat(32))).toEqual({ kind: "transaction", chain: "ethereum" });
    expect(classifyQuery(SIG)).toEqual({ kind: "transaction", chain: "solana" });
  });

  it("rejects other input with an explanation and treats blank input as empty", () => {
    const r = classifyQuery("hello");
    expect(r.kind).toBe("invalid");
    expect(r.kind === "invalid" && r.message).toMatch(/Ethereum .*Solana/);
    expect(classifyQuery("0x1234").kind).toBe("invalid");
    expect(classifyQuery("   ")).toEqual({ kind: "empty" });
  });
});
