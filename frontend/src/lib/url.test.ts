import { describe, expect, it } from "vitest";
import { safeHttpUrl } from "./url";

describe("safeHttpUrl", () => {
  it("accepts http and https addresses", () => {
    expect(safeHttpUrl("https://example.org/a?b=1")).toBe("https://example.org/a?b=1");
    expect(safeHttpUrl("http://example.org")).toBe("http://example.org/");
  });
  it("rejects every other scheme, relative paths and text", () => {
    for (const bad of ["javascript:alert(1)", "JAVASCRIPT:alert(1)", "data:text/html,<b>x</b>", "ftp://example.org", "//example.org", "/path", "https://exa mple.org", "see https://example.org", ""])
      expect(safeHttpUrl(bad), bad).toBeNull();
  });
});
