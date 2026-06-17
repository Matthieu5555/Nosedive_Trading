import { expect, test } from "@playwright/test";

import { mockBff } from "./mock-bff";

test.beforeEach(async ({ page }) => {
  await mockBff(page);
});

test("Données: index and as-of selectors are present and switchable", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "Données" })).toBeVisible();

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
    await expect(page.getByRole("heading", { level: 1, name: "Données" })).toBeVisible();
  }
  await expect(page.getByText("failed to render", { exact: false })).toHaveCount(0);
});

test("Risque: the compose→book→shock→explain tabs over a shared composer", async ({ page }) => {
  await page.goto("/risque");
  await expect(page.getByRole("heading", { level: 1, name: "Risque" })).toBeVisible();

  for (const name of [/Composer/, /Le book/, /Choquer/, /Attribution/]) {
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

  await page.getByRole("tab", { name: /Choquer/ }).click();
  await expect(page.getByRole("button", { name: "Stress basket" })).toBeVisible();
});

test("Risque › Le book folds in the booked book (the former Positions page)", async ({ page }) => {
  await page.goto("/risque");
  await page.getByRole("tab", { name: /Le book/ }).click();
  await expect(page.getByText("Book summary", { exact: true })).toBeVisible();
  await expect(page.getByText("Fills ledger", { exact: true })).toBeVisible();
});

test("Ordres: ticket builds, send stays 3B-gated, recon + backtest folded in", async ({ page }) => {
  await page.goto("/ordres");
  await expect(page.getByRole("heading", { level: 1, name: "Ordres" })).toBeVisible();

  await page.getByRole("button", { name: "template straddle" }).click();
  const ticket = page.getByRole("region", { name: /order ticket/i });
  await expect(ticket).toBeVisible();
  await expect(ticket.getByText(/preview only/i)).toBeVisible();

  await ticket.getByRole("button", { name: "Build ticket" }).click();
  await expect(ticket.getByRole("table", { name: /order ticket legs/i })).toBeVisible();
  const send = ticket.getByRole("button", { name: "Sign and send order" });
  await expect(send).toBeVisible();
  await expect(send).toBeDisabled();
  await expect(ticket.getByText(/3B — gated/)).toBeVisible();

  // Reconciliation (moved here from Risk) and the folded backtest both render on the page.
  await expect(page.getByLabel("Broker account")).toBeVisible();
  await expect(page.getByText("Backtest setup", { exact: true })).toBeVisible();
});

test("Risque: the Attribution tab carries the by-Greek waterfall over a portfolio input", async ({
  page,
}) => {
  await page.goto("/risque");
  await page.getByRole("tab", { name: /Attribution/ }).click();
  await expect(page.getByRole("button", { name: "P&L attribution" })).toBeVisible();
  await expect(page.getByLabel("portfolio", { exact: true })).toBeVisible();
});
