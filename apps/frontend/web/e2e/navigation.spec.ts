import { expect, test } from "@playwright/test";

import { collectPageErrors } from "./helpers";
import { mockBff } from "./mock-bff";

// The six top-level tabs, in workflow order (see src/routes.ts). All English; Basket and Risk
// Scenarios folded into Simulate, the standalone Orders page and the French 3-tab consolidation
// (Données/Risque/Ordres) are retired.
const TABS = [
  { button: "Market", heading: "Market", path: "/" },
  { button: "Simulate", heading: "Simulate", path: "/simulate" },
  { button: "Signals", heading: "Signals", path: "/signals" },
  { button: "Strategy", heading: "Strategy", path: "/strategy" },
  { button: "Positions", heading: "Positions", path: "/positions" },
  { button: "Operations", heading: "Operations", path: "/operations" },
] as const;

// Labels that must never appear in the nav: the retired Orders page and any leftover French tabs.
const DROPPED = ["Orders", "Données", "Risque", "Ordres"] as const;

test.beforeEach(async ({ page }) => {
  await mockBff(page);
});

test("the nav is exactly the six tabs and Market is active on load", async ({ page }) => {
  await page.goto("/");
  const nav = page.getByRole("navigation", { name: "Main" });
  for (const tab of TABS) {
    await expect(nav.getByRole("link", { name: tab.button, exact: true })).toBeVisible();
  }
  await expect(nav.getByRole("link")).toHaveCount(TABS.length);
  for (const gone of DROPPED) {
    await expect(nav.getByRole("link", { name: gone, exact: true })).toHaveCount(0);
  }

  await expect(nav.getByRole("link", { name: "Market", exact: true })).toHaveAttribute(
    "aria-current",
    "page",
  );
});

for (const tab of TABS) {
  test(`clicking "${tab.button}" routes to ${tab.path} and shows its heading`, async ({ page }) => {
    const { pageErrors } = collectPageErrors(page);
    await page.goto("/");

    await page
      .getByRole("navigation", { name: "Main" })
      .getByRole("link", { name: tab.button, exact: true })
      .click();

    await expect(page).toHaveURL(new RegExp(`${tab.path.replace("/", "\\/")}$`));
    await expect(page.getByRole("heading", { level: 1, name: tab.heading })).toBeVisible();
    await expect(
      page
        .getByRole("navigation", { name: "Main" })
        .getByRole("link", { name: tab.button, exact: true }),
    ).toHaveAttribute("aria-current", "page");
    await expect(page.getByText("failed to render", { exact: false })).toHaveCount(0);
    expect(pageErrors, pageErrors.join("\n")).toEqual([]);
  });
}

// Legacy paths from the retired 3-tab consolidation (and the old Orders page) forward to their
// 7-tab homes so any open bookmark still lands somewhere sensible.
const REDIRECTS = [
  { from: "/market", to: "/", heading: "Market" },
  { from: "/basket", to: "/simulate", heading: "Simulate" },
  { from: "/risk", to: "/simulate", heading: "Simulate" },
  { from: "/risque", to: "/simulate", heading: "Simulate" },
  { from: "/ordres", to: "/strategy", heading: "Strategy" },
  { from: "/orders", to: "/strategy", heading: "Strategy" },
  { from: "/does-not-exist", to: "/", heading: "Market" },
] as const;

for (const r of REDIRECTS) {
  test(`legacy ${r.from} redirects to ${r.to}`, async ({ page }) => {
    await page.goto(r.from);
    await expect(page).toHaveURL(new RegExp(`${r.to === "/" ? "\\/" : r.to.replace("/", "\\/")}$`));
    await expect(page.getByRole("heading", { level: 1, name: r.heading })).toBeVisible();
  });
}
