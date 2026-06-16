import { expect, type Page, test } from "@playwright/test";

import { ROUTES } from "../src/routes";
import { expectNoCollisions, expectNoHorizontalOverflow, expectWithinViewport } from "./helpers";
import { mockBff } from "./mock-bff";

const VIEWPORTS = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "laptop", width: 1024, height: 768 },
  { name: "narrow", width: 390, height: 844 },
] as const;

test.beforeEach(async ({ page }) => {
  await mockBff(page);
});

async function navButtons(page: Page) {
  const links = page.getByRole("navigation", { name: "Main" }).getByRole("link");
  const count = await links.count();
  return Array.from({ length: count }, (_, i) => links.nth(i));
}

for (const viewport of VIEWPORTS) {
  for (const route of ROUTES) {
    test(`[${viewport.name}] ${route.label}: no element collisions or overflow`, async ({
      page,
    }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await page.goto(route.path);
      await expect(page.getByRole("heading", { level: 1, name: route.heading })).toBeVisible();

      // The page must not scroll sideways at any width.
      await expectNoHorizontalOverflow(page);

      // Top-bar regions never overlap each other.
      await expectNoCollisions([
        page.locator(".brand"),
        page.locator(".nav"),
        page.locator(".session-pill").first(),
      ]);

      // The nav buttons never overlap each other.
      const buttons = await navButtons(page);
      await expectNoCollisions(buttons);
      // Below 980px the CSS turns .nav into a horizontal scroller (overflow-x: auto) by design,
      // so buttons may extend past the viewport there — that's intended, not a clipped control.
      // Above the breakpoint every button must sit fully on-screen.
      if (viewport.width >= 980) {
        for (const button of buttons) {
          await expectWithinViewport(page, button);
        }
      }

      // The sticky top bar doesn't sit on top of the page content at the top of the page.
      await expectNoCollisions([page.locator(".topbar"), page.locator(".page").first()]);
    });
  }
}
