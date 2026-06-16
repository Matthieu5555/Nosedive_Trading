import { fileURLToPath, URL } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { configDefaults, defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    // Backend the dev server proxies to. Defaults to the shared :8000 process, but is overridable
    // via BFF_TARGET so an operator running their own backend (e.g. when the shared :8000 is broken)
    // can point the front at it: `BFF_TARGET=http://127.0.0.1:8001 npm run dev`. Without this, a set
    // BFF_TARGET is silently ignored and the front talks to :8000 regardless.
    proxy: {
      "/api": process.env.BFF_TARGET ?? "http://127.0.0.1:8000",
      "/healthz": process.env.BFF_TARGET ?? "http://127.0.0.1:8000",
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    // The e2e/ specs are Playwright tests (real browser) run by `npm run e2e`, not Vitest. They
    // import @playwright/test, whose test.beforeEach() throws under Vitest — so keep them out of
    // the Vitest glob, which otherwise matches every **/*.spec.ts.
    exclude: [...configDefaults.exclude, "e2e/**"],
  },
});
