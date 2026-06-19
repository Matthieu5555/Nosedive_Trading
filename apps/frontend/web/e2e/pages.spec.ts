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

test("Simulate › Build a basket: the compose → stress → attribution tabs over a shared composer", async ({
  page,
}) => {
  await page.goto("/simulate");
  await expect(page.getByRole("heading", { level: 1, name: "Simulate" })).toBeVisible();

  // Default book source is "My book"; switch to the build-a-basket what-if.
  await page.getByRole("button", { name: "Build a basket" }).click();

  for (const name of [/Compose/, /Stress/, /Attribution/]) {
    await expect(page.getByRole("tab", { name })).toBeVisible();
  }
  // The retired "The Book" clone of Positions is gone.
  await expect(page.getByRole("tab", { name: /The Book/ })).toHaveCount(0);

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

test("Simulate › My book: the held-portfolio stress carries a portfolio picker", async ({
  page,
}) => {
  await page.goto("/simulate");
  // "My book" is the default book source: the named crises + persisted surface over a portfolio.
  await expect(page.getByLabel("Portfolio", { exact: true })).toBeVisible();
  await expect(page.getByText("Named historical scenarios", { exact: true })).toBeVisible();
});

test("Strategy: the folded backtest setup renders", async ({ page }) => {
  await page.goto("/strategy");
  await expect(page.getByRole("heading", { level: 1, name: "Strategy" })).toBeVisible();
  await expect(page.getByText("Backtest setup", { exact: true })).toBeVisible();
});

test("Positions: the broker reconciliation account input is present", async ({ page }) => {
  await page.goto("/positions");
  await expect(page.getByRole("heading", { level: 1, name: "Positions" })).toBeVisible();
  await expect(page.getByLabel("Broker account")).toBeVisible();
});

test("Simulate › Build a basket: the Attribution tab carries the by-Greek waterfall over a portfolio input", async ({
  page,
}) => {
  await page.goto("/simulate");
  await page.getByRole("button", { name: "Build a basket" }).click();
  await page.getByRole("tab", { name: /Attribution/ }).click();
  await expect(page.getByRole("button", { name: "P&L attribution" })).toBeVisible();
  await expect(page.getByLabel("portfolio", { exact: true })).toBeVisible();
});
