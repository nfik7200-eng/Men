/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import type { Plugin } from "vite";
import { contentSecurityPolicy } from "./src/csp";

declare const process: { env: Record<string, string | undefined> };

// The dev server and preview server proxy /api to the local BIP backend, so the browser talks
// to a single origin. BIP_API_URL overrides the backend address (default http://127.0.0.1:8000).
const backend = process.env.BIP_API_URL ?? "http://127.0.0.1:8000";

// In a GitHub Codespace the forwarded URL reaches Vite with Host "<codespace>-5173.<domain>", which
// Vite's host check refuses. Allow exactly that host, and only when running in a codespace.
const codespace = process.env.CODESPACE_NAME;
const forwardingDomain = process.env.GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN ?? "app.github.dev";
const allowedHosts = codespace ? [`${codespace}-5173.${forwardingDomain}`] : [];

// The CSP meta tag is injected per command: strict for builds, inline styles allowed for `vite` dev.
function csp(): Plugin {
  let command: "build" | "serve" = "serve";
  return {
    name: "bip-csp",
    configResolved(cfg) {
      command = cfg.command;
    },
    transformIndexHtml() {
      return [
        {
          tag: "meta",
          attrs: { "http-equiv": "Content-Security-Policy", content: contentSecurityPolicy(command) },
          injectTo: "head", // after the inline scripts Vite injects in dev
        },
      ];
    },
  };
}

export default defineConfig({
  plugins: [react(), csp()],
  server: { host: "127.0.0.1", port: 5173, strictPort: true, allowedHosts, proxy: { "/api": backend } },
  preview: { host: "127.0.0.1", port: 4173, strictPort: true, proxy: { "/api": backend } },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
