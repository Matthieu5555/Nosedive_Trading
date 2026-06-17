import { expect, type Page,test } from "@playwright/test";

import { ANALYTICS_QUOTED } from "../src/test/fixtures";
import { collectPageErrors } from "./helpers";
import { mockBff } from "./mock-bff";

// The Market READ flow, end to end in a real browser: pick the underlying → the 3D nappe renders →
// select a tenor → smile + Greeks table + Greeks shape curves + the price-structure block (bid /
// ask / volume columns, the seam that broke this wave) → the coverage panel expands.
// Assertions are on user-visible text/rows, not internal state; numeric checks carry tolerances.
//
// The shared mock-bff serves ANALYTICS_AAA on /api/analytics, which carries no per-strike quotes.
// This flow needs the bid/ask/volume block populated, so it overrides /api/analytics (and a richer
// /api/coverage) AFTER mockBff — Playwright runs route handlers most-recent-first, so the override
// wins. SPX is the default index and ANALYTICS_QUOTED is keyed to it, so no extra selection needed.

// Hand-derived from ANALYTICS_QUOTED (src/test/fixtures.ts): a single 3m tenor with three strikes.
//   strike 100 (atm): bid 4.1, ask 4.5, volume 1234  -> spread 0.40
//   strike 120 (30dc): bid 2.2, ask 2.6, volume 87    -> spread 0.40
//   strike  80 (30dp): no quote (null/null/null)      -> rendered as "—"
const COVERAGE_SPX = {
  underlying: "SPX",
  trade_date: "2026-05-29",
  n_expiries: 1,
  expiries: [
    {
      expiry: "2026-08-28",
      tenor: "3m",
      n_strikes: 3,
      n_calls: 2,
      n_puts: 1,
      strike_min: 80,
      strike_max: 120,
    },
  ],
  tenors: [
    { tenor: "10d", measured: null, floor: null, status: "pass" },
    { tenor: "1m", measured: 0, floor: 5, status: "fail" },
    { tenor: "3m", measured: 0.95, floor: 0.8, status: "pass" },
    { tenor: "6m", measured: null, floor: null, status: "pass" },
    { tenor: "12m", measured: null, floor: null, status: "pass" },
    { tenor: "18m", measured: null, floor: null, status: "pass" },
    { tenor: "2y", measured: null, floor: null, status: "pass" },
    { tenor: "3y", measured: null, floor: null, status: "pass" },
  ],
  qc_status: "pass",
  delta_band_status: "pass",
};

async function mockMarketRead(page: Page): Promise<void> {
  await mockBff(page);
  await page.route("**/api/analytics**", (route) => route.fulfill({ json: ANALYTICS_QUOTED }));
  await page.route("**/api/coverage**", (route) => route.fulfill({ json: COVERAGE_SPX }));
}

test("Market read flow: underlying → nappe → tenor → smile/greeks/price-structure → coverage", async ({
  page,
}) => {
  const errors = collectPageErrors(page);
  await mockMarketRead(page);

  // 1. Pick the underlying. SPX is the default; the picker is present, enabled, and on SPX.
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "Market" })).toBeVisible();
  const index = page.getByLabel("Index", { exact: true });
  await expect(index).toBeVisible();
  await expect(index).toHaveValue("SPX");

  // 2. The 3D vol nappe renders (the surface-grid fallback path off the maturities).
  await expect(
    page.getByRole("figure", {
      name: "Implied-volatility surface (vol vs log-moneyness vs maturity)",
    }),
  ).toBeVisible();

  // 3. Select a tenor. The panel opens on 3m (the captured tenor in the fixture).
  const tenorPanel = page.getByRole("article", { name: "Tenor view" });
  await expect(tenorPanel.getByRole("heading", { name: "Smile & Greeks" })).toBeVisible();
  const tenor = page.getByLabel("Tenor", { exact: true });
  await expect(tenor).toBeVisible();
  await tenor.selectOption("3m");
  await expect(tenor).toHaveValue("3m");

  // 4a. The smile renders for the selected tenor (per-tenor IV vs log-moneyness).
  await expect(page.getByRole("figure", { name: /^Smile — 3m \(0\.250y\)/ })).toBeVisible();

  // 4b. The Greeks TABLE for the tenor (deltas × greeks).
  await expect(page.getByRole("table", { name: "Dollar Greeks — 3m (0.250y)" })).toBeVisible();

  // 4c. The Greeks SHAPE CURVES (gamma/vega bell, delta S-curve) — complementary to the table.
  await expect(page.getByRole("figure", { name: /^Greek profiles —/ })).toBeVisible();

  // 4d. The price-structure block: bid / ask / volume COLUMNS visible (NOT a synthetic mid). This
  // is the exact seam that broke — quote.bid / quote.ask / quote.volume per strike.
  const priceStructure = page.getByRole("table", { name: "Price structure — 3m (0.250y)" });
  await expect(priceStructure).toBeVisible();
  await expect(priceStructure.getByRole("columnheader", { name: /^bid/ })).toBeVisible();
  await expect(priceStructure.getByRole("columnheader", { name: /^ask/ })).toBeVisible();
  await expect(priceStructure.getByRole("columnheader", { name: /^volume/ })).toBeVisible();

  // One row per strike, sorted ascending: 80, 100, 120. The 100 (atm) row carries its quote; the
  // 80 (30dp) row has no quote and reads "—". Assert the populated row's bid/ask/volume show.
  const rows = priceStructure.locator("tbody tr");
  await expect(rows).toHaveCount(3);
  const atmRow = rows.filter({ hasText: "atm" });
  // Values render as plain readable numbers (the currency lives in the column header): bid 4.1,
  // ask 4.5, volume thousands-separated (1234 → "1,234").
  await expect(atmRow).toContainText("4.1"); // bid
  await expect(atmRow).toContainText("4.5"); // ask
  await expect(atmRow).toContainText("1,234"); // volume = 1234 → "1,234"
  // The unquoted put strike reads as an honest gap, never a fabricated mid.
  await expect(rows.filter({ hasText: "30dp" })).toContainText("—");

  // 5. The coverage panel expands on demand and shows the captured expiry + whole pinned grid.
  const coverage = page.getByRole("article", { name: "Capture coverage" });
  await coverage.getByRole("button", { name: "Show" }).click();
  await expect(page.getByRole("table", { name: "Captured expiries" })).toBeVisible();
  const perTenor = page.getByRole("table", { name: "Per-tenor coverage" });
  await expect(perTenor).toBeVisible();
  // Every pinned tenor is a row — an empty tenor is labelled, never omitted (8 grid rows).
  await expect(perTenor.locator("tbody tr")).toHaveCount(8);

  // No uncaught page crash anywhere in the flow.
  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});
