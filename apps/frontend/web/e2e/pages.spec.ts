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
  // The booking chain has one home (frontend-orders-booking-reconcile, ruling (b)): the order
  // ticket on Basket. There is no Orders sketch — execution stays gated here, not on a dead tab.
  await page.goto("/basket");
  await expect(page.getByRole("heading", { level: 1, name: "Basket Builder" })).toBeVisible();

  // Compose legs so the real ticket panel mounts (it is gated on legs.length > 0).
  await page.getByRole("button", { name: "template straddle" }).click();
  const ticket = page.getByRole("region", { name: /order ticket/i });
  await expect(ticket).toBeVisible();
  // Self-labels as the real, preview-only Execution ticket — not an indicative sketch.
  await expect(ticket.getByText(/preview only/i)).toBeVisible();

  // Build the ticket off the BFF preview, then the send path must stay disabled + 3B-gated.
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
