import { expect, test } from "@playwright/test";

import { collectPageErrors, expectNoHorizontalOverflow } from "./helpers";
import { mockBff } from "./mock-bff";

const VIEWPORTS = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "laptop", width: 1280, height: 800 },
  { name: "narrow", width: 768, height: 1024 },
] as const;

test.beforeEach(async ({ page }) => {
  await mockBff(page);
});

test("the three operator layers render without a crash", async ({ page }) => {
  const { pageErrors } = collectPageErrors(page);
  await page.goto("/operations");

  await expect(page.getByRole("heading", { level: 1, name: "Operations" })).toBeVisible();
  await expect(page.getByText("System health", { exact: true })).toBeVisible();
  await expect(page.getByText("Run control", { exact: true })).toBeVisible();
  await expect(page.getByText("Risk & analytics freshness", { exact: true })).toBeVisible();

  // Layer 1: the headline status resolves from the mocked /api/health.
  await expect(page.getByText("Healthy", { exact: true })).toBeVisible();
  // Layer 3: the freshness panel reports when risk last computed.
  await expect(page.getByText("Risk last computed for")).toBeVisible();

  await expect(page.getByText("failed to render", { exact: false })).toHaveCount(0);
  expect(pageErrors, pageErrors.join("\n")).toEqual([]);
});

test("an operator can launch a run and watch the job list", async ({ page }) => {
  await page.goto("/operations");

  const launch = page.getByRole("button", { name: "Launch run" });
  await expect(launch).toBeEnabled();
  await launch.click();

  // The mocked /api/jobs carries a SAMPLE run; the jobs table shows it.
  await expect(page.getByRole("cell", { name: "SAMPLE" }).first()).toBeVisible();
});

for (const vp of VIEWPORTS) {
  test(`Operations stays on-screen with no horizontal overflow at ${vp.name}`, async ({ page }) => {
    await page.setViewportSize({ width: vp.width, height: vp.height });
    await page.goto("/operations");
    await expect(page.getByRole("heading", { level: 1, name: "Operations" })).toBeVisible();
    await expect(page.getByText("Healthy", { exact: true })).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });
}
