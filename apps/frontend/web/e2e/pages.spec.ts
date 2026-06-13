// Per-page smoke: each route's primary controls render, are visible and clickable, and a real
// interaction does what a user expects. Deliberately shallow on page internals (those move, and
// other agents own them) — it asserts the operator-facing controls work, not their wiring.

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
  // The registry-driven picker offers at least the enabled index — never an empty list.
  expect(await index.locator("option").count()).toBeGreaterThan(0);

  await expect(page.getByLabel("As-of date")).toBeVisible();

  // Switching the index must not crash the page (heading survives, no error tile).
  const values = await index.locator("option").evaluateAll((opts) =>
    opts.map((o) => (o as HTMLOptionElement).value),
  );
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

test("Orders: the ticket renders and Submit is disabled (read-only sketch)", async ({ page }) => {
  await page.goto("/orders");
  await expect(page.getByRole("heading", { level: 1, name: "Orders" })).toBeVisible();

  const submit = page.getByRole("button", { name: /Submit/ });
  await expect(submit).toBeVisible();
  // Execution is out of scope — the button must be disabled and self-label why.
  await expect(submit).toBeDisabled();
});

test("Basket: a template button composes legs and enables pricing", async ({ page }) => {
  await page.goto("/basket");
  await expect(page.getByRole("heading", { level: 1, name: "Basket Builder" })).toBeVisible();

  // Primary controls are present.
  await expect(page.getByLabel("underlying", { exact: true })).toBeVisible();
  await expect(page.getByLabel("trade date", { exact: true })).toBeVisible();
  await expect(page.getByLabel("tenor", { exact: true })).toBeVisible();

  // Pricing is gated until there are legs.
  const price = page.getByRole("button", { name: "Price basket" });
  await expect(price).toBeDisabled();

  // The composed-legs grid starts empty.
  const legGrid = page.getByRole("table", { name: "composed legs" });
  await expect(page.getByText("No legs yet", { exact: false })).toBeVisible();

  // Clicking the straddle template button builds legs (a two-leg structure).
  await page.getByRole("button", { name: "template straddle" }).click();

  await expect(page.getByText("No legs yet", { exact: false })).toHaveCount(0);
  // The straddle is a call + a put: two body rows in the grid.
  await expect(legGrid.locator("tbody tr")).toHaveCount(2);
  // With legs composed, pricing is now enabled.
  await expect(price).toBeEnabled();
});
