import { expect, type Page, type Route, test } from "@playwright/test";

import { collectPageErrors } from "./helpers";
import { mockBff } from "./mock-bff";

const POSITIONS = {
  source: "fills",
  source_ts: "2026-06-15T18:00:00+00:00",
  n_lines: 1,
  lines: [
    {
      contract_key: "SPX|OPT|USD|CBOE|100|d|2026-09-18|5200|P",
      underlying: "SPX",
      strike: 5200,
      expiry: "2026-09-18",
      option_right: "P",
      multiplier: 100,
      quantity: 2,
      broker_contract_id: "222",
      mark_price: 12.5,
      market_value: 2500,
      greeks: {
        delta: { raw: -0.3, position: -60, dollar: -585, unit: "$ per $1 of underlying" },
        gamma: { raw: 0.02, position: 4, dollar: 76, unit: "$ per 1% move" },
        vega: { raw: 0.31, position: 62, dollar: 31, unit: "$ per 1 vol point" },
        theta: { raw: -0.05, position: -10, dollar: -4.1, unit: "$ per calendar day" },
        rho: { raw: 0.04, position: 8, dollar: 5, unit: "$ per 1% rate" },
      },
    },
  ],
  book: {
    delta: { dollar: -585, unit: "$ per $1 of underlying" },
    gamma: { dollar: 76, unit: "$ per 1% move" },
    vega: { dollar: 31, unit: "$ per 1 vol point" },
    theta: { dollar: -4.1, unit: "$ per calendar day" },
    rho: { dollar: 5, unit: "$ per 1% rate" },
    market_value: 2500,
  },
  priced_contract_keys: 1,
  unpriced_contract_keys: ["SPX|OPT|USD|CBOE|100|d|2026-12-18|4800|C"],
};

const FILLS = {
  trade_date: null,
  underlying: "SPX",
  n_fills: 1,
  fills: [
    {
      fill_id: "f-1",
      booking_id: "bk-9",
      source_basket_id: "basket-SPX",
      trade_date: "2026-06-15",
      underlying: "SPX",
      contract_key: "SPX|OPT|USD|CBOE|100|d|2026-09-18|5200|P",
      signed_qty: "2",
      price: 12.5,
      fill_ts: "2026-06-15T17:30:01+00:00",
      mode: "paper",
      broker_contract_id: "222",
    },
  ],
};

async function mockPositions(page: Page) {
  await page.route("**/api/positions/fills**", (route: Route) => route.fulfill({ json: FILLS }));
  await page.route("**/api/positions**", (route: Route) => {
    if (route.request().url().includes("/positions/fills")) {
      return route.fulfill({ json: FILLS });
    }
    return route.fulfill({ json: POSITIONS });
  });
}

test.beforeEach(async ({ page }) => {
  await mockBff(page);
  await mockPositions(page);
});

test("the Positions page shows the book summary, positions table and fills ledger", async ({
  page,
}) => {
  const { pageErrors } = collectPageErrors(page);
  await page.goto("/positions");

  await expect(page.getByRole("heading", { level: 1, name: "Positions" })).toBeVisible();

  const summary = page.getByRole("table", { name: /Book dollar Greeks/i });
  await expect(summary).toBeVisible();
  await expect(summary.getByText("2.5 × 10³")).toBeVisible();

  const positions = page.getByRole("table", { name: /Open positions/i });
  await expect(positions.getByText("SPX P 5.2 × 10³ 2026-09-18")).toBeVisible();

  const ledger = page.getByRole("table", { name: /Fills ledger/i });
  await expect(ledger.getByText("2026-06-15T17:30:01+00:00")).toBeVisible();

  expect(pageErrors, pageErrors.join("\n")).toEqual([]);
});

test("the booked-but-unpriced legs are labelled, not hidden", async ({ page }) => {
  await page.goto("/positions");
  const notice = page.getByRole("alert", { name: /unpriced legs/i });
  await expect(notice.getByText(/Booked but unpriced legs \(1\)/)).toBeVisible();
  await expect(notice.getByText("SPX|OPT|USD|CBOE|100|d|2026-12-18|4800|C")).toBeVisible();
});

test("the underlying and trade-date selectors are present", async ({ page }) => {
  await page.goto("/positions");
  await expect(page.getByLabel("Underlying")).toBeVisible();
  await expect(page.getByLabel("Trade date")).toBeVisible();
});
