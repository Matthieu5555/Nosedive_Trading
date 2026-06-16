import { expect, test } from "@playwright/test";

import {
  collectPageErrors,
  expectNoCollisions,
  expectNoHorizontalOverflow,
} from "./helpers";
import { mockBff } from "./mock-bff";

test.beforeEach(async ({ page }) => {
  await mockBff(page);
});

async function runBacktest(page: import("@playwright/test").Page) {
  await page.goto("/strategy");
  await page.getByLabel("backtest index").selectOption("SX5E");
  await page.getByLabel("start date").fill("2026-03-01");
  await page.getByLabel("end date").fill("2026-03-31");
  await page.getByRole("button", { name: /run backtest/i }).click();
}

test("the Strategy page lands on a configurable backtest form, not a stub", async ({ page }) => {
  const { pageErrors } = collectPageErrors(page);
  await page.goto("/strategy");

  await expect(page.getByRole("heading", { level: 1, name: "Strategy" })).toBeVisible();
  await expect(page.getByRole("button", { name: /run backtest/i })).toBeVisible();
  await expect(page.getByText("No data yet", { exact: true })).toHaveCount(0);
  await expect(page.getByText(/No backtest run yet/i)).toBeVisible();

  expect(pageErrors, pageErrors.join("\n")).toEqual([]);
});

test("the run button is disabled until both dates are filled", async ({ page }) => {
  await page.goto("/strategy");
  const run = page.getByRole("button", { name: /run backtest/i });
  await expect(run).toBeDisabled();

  await page.getByLabel("start date").fill("2026-03-01");
  await page.getByLabel("end date").fill("2026-03-31");
  await expect(run).toBeEnabled();
});

test("running a backtest renders the summary, equity curve and which-Greek-paid panels", async ({
  page,
}) => {
  const { pageErrors } = collectPageErrors(page);
  await runBacktest(page);

  await expect(page.getByRole("heading", { name: /How the line did/i })).toBeVisible();
  await expect(page.getByRole("article", { name: "Cumulative P&L" })).toBeVisible();
  await expect(page.getByRole("article", { name: /Which Greek paid/i })).toBeVisible();
  await expect(page.getByRole("article", { name: /Exposure Greeks over time/i })).toBeVisible();
  await expect(page.getByText(/Theta paid most/i)).toBeVisible();

  expect(pageErrors, pageErrors.join("\n")).toEqual([]);
});

test("the results layout does not overflow or collide at desktop width", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await runBacktest(page);

  await expect(page.getByRole("heading", { name: /How the line did/i })).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await expectNoCollisions([
    page.getByRole("article", { name: "Backtest summary" }),
    page.getByRole("article", { name: "Cumulative P&L" }),
    page.getByRole("article", { name: "Which Greek paid" }),
  ]);
});
