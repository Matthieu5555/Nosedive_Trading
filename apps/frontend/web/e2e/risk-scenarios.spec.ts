import { expect, test } from "@playwright/test";

import { mockBff } from "./mock-bff";

test.beforeEach(async ({ page }) => {
  await mockBff(page);
});

test("Risk Scenarios: the four sections all render with no broken panel", async ({ page }) => {
  await page.goto("/risk");
  await expect(page.getByRole("heading", { level: 1, name: "Risk Scenarios" })).toBeVisible();

  await expect(page.getByText("Named historical scenarios").first()).toBeVisible();
  await expect(page.getByText("Where the P&L came from").first()).toBeVisible();
  await expect(
    page.getByRole("heading", { name: /Does the broker agree with our book/i }),
  ).toBeVisible();
  await expect(page.getByText("Persisted scenario surface").first()).toBeVisible();

  await expect(page.getByText("failed to render", { exact: false })).toHaveCount(0);
});

test("Risk Scenarios: the reconciliation panel reads the agreeing broker snapshot", async ({
  page,
}) => {
  await page.goto("/risk");

  const recon = page.getByRole("article", { name: "Broker reconciliation" });
  await expect(recon).toBeVisible();
  await expect(recon.getByText("In agreement")).toBeVisible();
  await expect(recon.getByText(/Every broker position matches a book position/i)).toBeVisible();
});

test("Risk Scenarios: a configured rate sweep renders its own labelled panel", async ({
  page,
}) => {
  await page.route(
    (url) => url.pathname === "/api/risk/scenarios",
    (route) =>
    route.fulfill({
      json: {
        portfolio_id: null,
        n_cells: 0,
        surface: {
          spot_shock: [],
          vol_shock: [],
          scenario_pnl: [],
          scenario_version: null,
          unit: "$ (full-reprice PnL)",
          n_cells: 0,
          has_holes: false,
          n_holes: 0,
        },
        named: [],
        n_named: 0,
        rate: [
          {
            scenario_id: "rate_-0.0010",
            rate_shock: -0.001,
            bp: -10,
            scenario_pnl: -450,
            scenario_version: "v3",
            n_legs: 2,
            unit: "$ (full-reprice PnL)",
            bp_unit: "bp",
          },
          {
            scenario_id: "rate_+0.0010",
            rate_shock: 0.001,
            bp: 10,
            scenario_pnl: 480,
            scenario_version: "v3",
            n_legs: 2,
            unit: "$ (full-reprice PnL)",
            bp_unit: "bp",
          },
        ],
        n_rate: 2,
      },
    }),
  );

  await page.goto("/risk");
  await expect(page.getByRole("heading", { name: "Rate-shock sweep" })).toBeVisible();
  const sweep = page.getByRole("article", { name: "Rate-shock sweep" });
  await expect(sweep.getByRole("table", { name: /Rate-shock sweep/i })).toBeVisible();
  await expect(page.getByText("failed to render", { exact: false })).toHaveCount(0);
});

test("Risk Scenarios: a portfolio and a broker account selector are both present", async ({
  page,
}) => {
  await page.goto("/risk");

  const portfolio = page.getByLabel("Portfolio", { exact: true });
  await expect(portfolio).toBeVisible();
  await portfolio.selectOption("CORE-INDEX-OPTIONS");
  await expect(portfolio).toHaveValue("CORE-INDEX-OPTIONS");

  const account = page.getByLabel("Broker account", { exact: true });
  await expect(account).toBeVisible();
  await account.fill("DUQ574355");
  await expect(account).toHaveValue("DUQ574355");

  await expect(page.getByText("failed to render", { exact: false })).toHaveCount(0);
});
