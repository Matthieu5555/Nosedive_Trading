import { expect, test } from "@playwright/test";

import { collectPageErrors, expectNoHorizontalOverflow, expectWithinViewport } from "./helpers";
import { mockBff } from "./mock-bff";

test.beforeEach(async ({ page }) => {
  await mockBff(page);
});

test("Signals: selectors render and the per-kind panels show value + plain caption", async ({
  page,
}) => {
  const { pageErrors } = collectPageErrors(page);
  await page.goto("/signals");

  await expect(page.getByRole("heading", { level: 1, name: "Signals" })).toBeVisible();

  const underlying = page.getByLabel("Underlying", { exact: true });
  await expect(underlying).toBeVisible();
  await expect(underlying).toBeEnabled();
  expect(await underlying.locator("option").count()).toBeGreaterThan(0);

  await expect(page.getByLabel("Trade date")).toBeVisible();

  await expect(page.getByRole("heading", { name: "IV rank" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Implied correlation ρ̄" })).toBeVisible();

  await expect(page.getByText("62.0%")).toBeVisible();
  await expect(page.getByText(/where today's implied vol sits/i)).toBeVisible();
  await expect(page.getByText(/average implied correlation/i)).toBeVisible();

  await expect(page.getByText("failed to render", { exact: false })).toHaveCount(0);
  expect(pageErrors, pageErrors.join("\n")).toEqual([]);
});

const VIEWPORTS = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "laptop", width: 1280, height: 800 },
  { name: "narrow", width: 768, height: 1024 },
] as const;

for (const vp of VIEWPORTS) {
  test(`Signals: no overflow and controls on-screen at ${vp.name}`, async ({ page }) => {
    await page.setViewportSize({ width: vp.width, height: vp.height });
    await page.goto("/signals");

    await expect(page.getByRole("heading", { name: "IV rank" })).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await expectWithinViewport(page, page.getByLabel("Underlying", { exact: true }));
    await expectWithinViewport(page, page.getByLabel("Trade date"));
  });
}

test("Signals: a failing underlyings list surfaces an error, not a dead screen", async ({
  page,
}) => {
  await page.route(
    (url) => url.pathname === "/api/signals/underlyings",
    (route) => route.fulfill({ status: 500, json: { detail: "signal store unreachable" } }),
  );
  await page.goto("/signals");

  await expect(page.getByRole("alert")).toContainText(/signal store unreachable|500/i);
});

test("Signals: a labelled-empty partition shows the no-signals state, not an error", async ({
  page,
}) => {
  await page.route(
    (url) => url.pathname === "/api/signals",
    (route) =>
      route.fulfill({
        json: {
          underlying: "SX5E",
          trade_date: "2026-05-30",
          snapshot_ts: null,
          n_signals: 0,
          kinds: [],
          by_kind: {},
          signals: [],
        },
      }),
  );
  await page.goto("/signals");

  await expect(page.getByText(/No signals recorded for SX5E/i)).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});
