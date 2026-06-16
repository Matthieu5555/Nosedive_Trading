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
    // The BFF the dev server proxies to. Defaults to the conventional local backend on :8000;
    // override with BFF_TARGET to point the UI at a backend on another port (e.g. a second instance
    // on :8001 when :8000 is held by someone else's process on a shared box).
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
