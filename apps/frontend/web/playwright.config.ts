// Real-browser (Playwright) end-to-end tests for the operator console.
//
// These complement — they do not replace — the Vitest component tests under src/. Vitest runs
// in jsdom, which has NO layout engine: it cannot tell whether two elements visually overlap,
// whether a control sits off-screen, or whether the page overflows its viewport. Playwright
// drives a real Chromium, so geometry (boundingBox) is meaningful and the collision/overflow
// checks in e2e/layout.spec.ts are possible at all.
//
// Every test mocks the BFF at the network layer (see e2e/mock-bff.ts) with the same contract
// fixtures the component tests use, so the suite is deterministic and never touches a live BFF
// or the canonical data store. The Vite dev server below is started by Playwright itself; its
// /api proxy to :8000 is never exercised because page.route intercepts first.
//
// This suite is deliberately NOT part of `npm test` (the AGENTS verification gate). It needs a
// browser binary and a running dev server; run it explicitly with `npm run e2e`.

import { defineConfig, devices } from "@playwright/test";

// Port is env-overridable so the suite can run alongside another worktree's dev server on this
// shared host (each agent/worktree may hold its own Vite on the default 5173); E2E_PORT picks a
// free one without disturbing them. Defaults to Vite's 5173 for the ordinary single-tree run.
const PORT = Number(process.env.E2E_PORT) || 5173;
const BASE_URL = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: "./e2e",
  // Fail a `.only` left in source on CI; keep local runs ergonomic.
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  // On CI: GitHub inline annotations + an HTML report uploaded as an artifact on failure
  // (see .github/workflows/gate.yml). Locally: the plain list reporter.
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `npm run dev -- --port ${PORT}`,
    url: BASE_URL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
