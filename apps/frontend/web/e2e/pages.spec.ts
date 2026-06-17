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

test("Risk Scenarios: portfolio selector lists portfolios and is selectable", async ({ page }) => {
  await page.goto("/risk");
  await expect(page.getByRole("heading", { level: 1, name: "Risk Scenarios" })).toBeVisible();

  const portfolio = page.getByLabel("Portfolio");
  await expect(portfolio).toBeVisible();
  await expect(portfolio.getByRole("option", { name: "All portfolios" })).toBeAttached();
  await expect(portfolio.getByRole("option", { name: "CORE-INDEX-OPTIONS" })).toBeAttached();

  await portfolio.selectOption("CORE-INDEX-OPTIONS");
  await expect(portfolio).toHaveValue("CORE-INDEX-OPTIONS");
  await expect(page.getByText("failed to render", { exact: false })).toHaveCount(0);
});

test("Basket booking home: the real ticket builds and Sign & send stays 3B-gated", async ({
  page,
}) => {
  await page.goto("/basket");
  await expect(page.getByRole("heading", { level: 1, name: "Basket Builder" })).toBeVisible();

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
});

test("Basket: a template button composes legs and enables pricing", async ({ page }) => {
  await page.goto("/basket");
  await expect(page.getByRole("heading", { level: 1, name: "Basket Builder" })).toBeVisible();

  await expect(page.getByLabel("underlying", { exact: true })).toBeVisible();
  await expect(page.getByLabel("trade date", { exact: true })).toBeVisible();
  await expect(page.getByLabel("tenor", { exact: true })).toBeVisible();

  const price = page.getByRole("button", { name: "Price basket" });
  await expect(price).toBeDisabled();

  const legGrid = page.getByRole("table", { name: "composed legs" });
  await expect(page.getByText("No legs yet", { exact: false })).toBeVisible();

  await page.getByRole("button", { name: "template straddle" }).click();

  await expect(page.getByText("No legs yet", { exact: false })).toHaveCount(0);

  await expect(legGrid.locator("tbody tr")).toHaveCount(2);

  await expect(price).toBeEnabled();
});

test("Basket: the four Onglet-2 tabs (Composer / Le book / Choquer / Attribution) over a shared composer", async ({
  page,
}) => {
  await page.goto("/basket");
  await expect(page.getByRole("heading", { level: 1, name: "Basket Builder" })).toBeVisible();

  // The landed Onglet-2 layout: ① Composer / ② Le book / ③ Choquer / ④ Attribution, with the
  // price action living on the Composer tab and the stress / attribution actions on theirs.
  const composerTab = page.getByRole("tab", { name: /composer/i });
  const stressTab = page.getByRole("tab", { name: /choquer/i });
  const attributionTab = page.getByRole("tab", { name: /attribution/i });
  await expect(composerTab).toBeVisible();
  await expect(page.getByRole("tab", { name: /le book/i })).toBeVisible();
  await expect(stressTab).toBeVisible();
  await expect(attributionTab).toBeVisible();

  await expect(page.getByRole("button", { name: "Price basket" })).toBeVisible();

  await stressTab.click();
  await expect(page.getByRole("button", { name: "Stress basket" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Price basket" })).toHaveCount(0);

  await attributionTab.click();
  await expect(page.getByRole("button", { name: "P&L attribution" })).toBeVisible();
  await expect(page.getByLabel("portfolio", { exact: true })).toBeVisible();

  await expect(page.getByLabel("underlying", { exact: true })).toBeVisible();
});
