import { describe, expect, it } from "vitest";
import { contentSecurityPolicy } from "./csp";

describe("content security policy", () => {
  it("is strict for production builds (no inline styles or scripts)", () => {
    const csp = contentSecurityPolicy("build");
    expect(csp).toContain("style-src 'self';");
    expect(csp).toContain("script-src 'self';");
    expect(csp).not.toContain("unsafe-inline");
    expect(csp).toContain("connect-src 'self'");
  });

  it("allows the inline <style> elements Vite injects in development", () => {
    const csp = contentSecurityPolicy("serve");
    expect(csp).toContain("style-src 'self' 'unsafe-inline';");
    // scripts stay restricted to same origin even in development
    expect(csp).toContain("script-src 'self';");
  });
});
