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
