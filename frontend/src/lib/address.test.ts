import { describe, expect, it } from "vitest";
import { base58DecodedLength, validateAddress } from "./address";

describe("validateAddress", () => {
  it("accepts well-formed EVM addresses in any case", () => {
    expect(validateAddress("ethereum", "0x" + "ab".repeat(20))).toBeNull();
    expect(validateAddress("ethereum", "0x" + "AB".repeat(20))).toBeNull();
  });

  it.each(["", "0x1234", "ab".repeat(20), "0x" + "zz".repeat(20), "0x" + "ab".repeat(21)])(
    "rejects bad EVM address %s",
    (value) => {
      expect(validateAddress("ethereum", value)).not.toBeNull();
    },
  );

  it("accepts Solana keys that decode to 32 bytes", () => {
    expect(validateAddress("solana", "11111111111111111111111111111111")).toBeNull();
    expect(validateAddress("solana", "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")).toBeNull();
  });

  it.each(["0x" + "ab".repeat(20), "0OIl0OIl0OIl0OIl0OIl0OIl0OIl0OIl", "1111", "z".repeat(44)])(
    "rejects bad Solana address %s",
    (value) => {
      expect(validateAddress("solana", value)).not.toBeNull();
    },
  );

  it("rejects unknown chains", () => {
    expect(validateAddress("dogecoin", "abc")).toBe("Unknown chain.");
  });

  it("measures base58 byte length including leading zeros", () => {
    expect(base58DecodedLength("11111111111111111111111111111111")).toBe(32);
    expect(base58DecodedLength("0")).toBeNull();
  });
});
