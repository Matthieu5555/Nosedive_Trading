import { expect, test } from "@playwright/test";

import { collectPageErrors } from "./helpers";
import { mockBff } from "./mock-bff";

// The three top-level onglets. Operations is a secondary utility (tested separately), not a tab.
const TABS = [
  { button: "Données", heading: "Données", path: "/" },
  { button: "Risque", heading: "Risque", path: "/risque" },
  { button: "Ordres", heading: "Ordres", path: "/ordres" },
] as const;

const DROPPED = ["Market", "Basket", "Risk Scenarios", "Signals", "Strategy", "Positions"] as const;

test.beforeEach(async ({ page }) => {
  await mockBff(page);
});

test("the nav is exactly the three onglets and Données is active on load", async ({ page }) => {
  await page.goto("/");
  const nav = page.getByRole("navigation", { name: "Main" });
  for (const tab of TABS) {
    await expect(nav.getByRole("link", { name: tab.button })).toBeVisible();
  }
  await expect(nav.getByRole("link")).toHaveCount(TABS.length);
  for (const gone of DROPPED) {
    await expect(nav.getByRole("link", { name: gone })).toHaveCount(0);
  }
  await expect(nav.getByRole("link", { name: "Orders" })).toHaveCount(0);

  await expect(nav.getByRole("link", { name: "Données" })).toHaveAttribute("aria-current", "page");
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
    await expect(
      page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: tab.button }),
    ).toHaveAttribute("aria-current", "page");
    await expect(page.getByText("failed to render", { exact: false })).toHaveCount(0);
    expect(pageErrors, pageErrors.join("\n")).toEqual([]);
  });
}

test("Operations is a secondary utility link, not a top-level onglet", async ({ page }) => {
  await page.goto("/");
  const nav = page.getByRole("navigation", { name: "Main" });
  await expect(nav.getByRole("link", { name: "Operations" })).toHaveCount(0);

  // Still reachable via the quiet utility link.
  await page.getByRole("link", { name: "Operations" }).click();
  await expect(page).toHaveURL(/\/operations$/);
  await expect(page.getByRole("heading", { level: 1, name: "Operations" })).toBeVisible();
});

const REDIRECTS = [
  { from: "/market", to: "/", heading: "Données" },
  { from: "/basket", to: "/risque", heading: "Risque" },
  { from: "/risk", to: "/risque", heading: "Risque" },
  { from: "/positions", to: "/risque", heading: "Risque" },
  { from: "/orders", to: "/ordres", heading: "Ordres" },
  { from: "/strategy", to: "/ordres", heading: "Ordres" },
  { from: "/signals", to: "/", heading: "Données" },
  { from: "/does-not-exist", to: "/", heading: "Données" },
] as const;

for (const r of REDIRECTS) {
  test(`legacy ${r.from} redirects to ${r.to}`, async ({ page }) => {
    await page.goto(r.from);
    await expect(page).toHaveURL(new RegExp(`${r.to === "/" ? "\\/" : r.to.replace("/", "\\/")}$`));
    await expect(page.getByRole("heading", { level: 1, name: r.heading })).toBeVisible();
  });
}
