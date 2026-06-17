import { expect, test } from "@playwright/test";

import { mockBff } from "./mock-bff";

test.beforeEach(async ({ page }) => {
  await mockBff(page);
});

test("Market: index and as-of selectors are present and switchable", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "Market" })).toBeVisible();

  const index = page.getByLabel("Index", { exact: true });
  await expect(index).toBeVisible();
  await expect(index).toBeEnabled();
  expect(await index.locator("option").count()).toBeGreaterThan(0);

  await expect(page.getByLabel("As-of fetch")).toBeVisible();

  const values = await index
    .locator("option")
    .evaluateAll((opts) => opts.map((o) => (o as HTMLOptionElement).value));
  if (values.length > 1) {
    await index.selectOption(values[1]);
    await expect(page.getByRole("heading", { level: 1, name: "Market" })).toBeVisible();
  }
  await expect(page.getByText("failed to render", { exact: false })).toHaveCount(0);
});

test("Basket: the compose → book → stress → attribution tabs over a shared composer", async ({
  page,
}) => {
  await page.goto("/basket");
  await expect(page.getByRole("heading", { level: 1, name: "Basket Builder" })).toBeVisible();

  for (const name of [/Compose/, /The Book/, /Stress/, /Attribution/]) {
    await expect(page.getByRole("tab", { name })).toBeVisible();
  }

  // The shared composer is above the tabs.
  await expect(page.getByLabel("underlying", { exact: true })).toBeVisible();
  await expect(page.getByLabel("trade date", { exact: true })).toBeVisible();
  await expect(page.getByLabel("tenor", { exact: true })).toBeVisible();

  const price = page.getByRole("button", { name: "Price basket" });
  await expect(price).toBeDisabled();

  await page.getByRole("button", { name: "template straddle" }).click();
  const legGrid = page.getByRole("table", { name: "composed legs" });
  await expect(legGrid.locator("tbody tr")).toHaveCount(2);
  await expect(price).toBeEnabled();

  await page.getByRole("tab", { name: /Stress/ }).click();
  await expect(page.getByRole("button", { name: "Stress basket" })).toBeVisible();
});

test("Basket › The Book folds in the booked book (the former Positions page)", async ({ page }) => {
  await page.goto("/basket");
  await page.getByRole("tab", { name: /The Book/ }).click();
  await expect(page.getByText("Book summary", { exact: true })).toBeVisible();
  await expect(page.getByText("Fills ledger", { exact: true })).toBeVisible();
});

test("Strategy: the folded backtest setup renders", async ({ page }) => {
  await page.goto("/strategy");
  await expect(page.getByRole("heading", { level: 1, name: "Strategy" })).toBeVisible();
  await expect(page.getByText("Backtest setup", { exact: true })).toBeVisible();
});

test("Risk Scenarios: broker reconciliation account input is present", async ({ page }) => {
  await page.goto("/risk");
  await expect(page.getByRole("heading", { level: 1, name: "Risk Scenarios" })).toBeVisible();
  await expect(page.getByLabel("Broker account")).toBeVisible();
});

test("Basket: the Attribution tab carries the by-Greek waterfall over a portfolio input", async ({
  page,
}) => {
  await page.goto("/basket");
  await page.getByRole("tab", { name: /Attribution/ }).click();
  await expect(page.getByRole("button", { name: "P&L attribution" })).toBeVisible();
  await expect(page.getByLabel("portfolio", { exact: true })).toBeVisible();
});
