import { expect, test } from "@playwright/test";

import { collectPageErrors } from "./helpers";
import { mockBff } from "./mock-bff";

const TABS = [
  { button: "Market", heading: "Market", path: "/" },
  { button: "Basket", heading: "Basket Builder", path: "/basket" },
  { button: "Risk Scenarios", heading: "Risk Scenarios", path: "/risk" },
] as const;

test.beforeEach(async ({ page }) => {
  await mockBff(page);
});

test("the nav buttons are present and Market is active on load", async ({ page }) => {
  await page.goto("/");
  const nav = page.getByRole("navigation", { name: "Main" });
  for (const tab of TABS) {
    await expect(nav.getByRole("link", { name: tab.button })).toBeVisible();
  }

  await expect(nav.getByRole("link", { name: "Orders" })).toHaveCount(0);

  await expect(nav.getByRole("link", { name: "Market" })).toHaveAttribute("aria-current", "page");
});

for (const tab of TABS) {
  test(`clicking "${tab.button}" routes to ${tab.path} and shows its heading`, async ({ page }) => {
    const { pageErrors } = collectPageErrors(page);
    await page.goto("/");

    await page
      .getByRole("navigation", { name: "Main" })
      .getByRole("link", { name: tab.button })
      .click();

    await expect(page).toHaveURL(new RegExp(`${tab.path.replace("/", "\\/")}$`));
    await expect(page.getByRole("heading", { level: 1, name: tab.heading })).toBeVisible();
    // The clicked tab is now the active one.
    await expect(
      page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: tab.button }),
    ).toHaveAttribute("aria-current", "page");
    // No ErrorBoundary fallback rendered on this tab.
    await expect(page.getByText("failed to render", { exact: false })).toHaveCount(0);
    // No uncaught exceptions (a real crash) on mount.
    expect(pageErrors, pageErrors.join("\n")).toEqual([]);
  });
}

test("an unknown route redirects to Market", async ({ page }) => {
  await page.goto("/does-not-exist");
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { level: 1, name: "Market" })).toBeVisible();
});

test("/market legacy path redirects to /", async ({ page }) => {
  await page.goto("/market");
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { level: 1, name: "Market" })).toBeVisible();
});

test("/orders legacy path redirects to the Basket booking home (retired sketch)", async ({
  page,
}) => {
  await page.goto("/orders");
  await expect(page).toHaveURL(/\/basket$/);
  await expect(page.getByRole("heading", { level: 1, name: "Basket Builder" })).toBeVisible();
});
