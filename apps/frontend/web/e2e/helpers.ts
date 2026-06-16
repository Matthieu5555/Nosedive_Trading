import { expect, type Locator, type Page } from "@playwright/test";

export interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

export async function boxOf(locator: Locator): Promise<Box> {
  const box = await locator.boundingBox();
  expect(box, "element has no rendered box (not visible / not laid out)").not.toBeNull();
  return box as Box;
}

export function overlaps(a: Box, b: Box, tolerance = 1): boolean {
  const xOverlap = Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x);
  const yOverlap = Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y);
  return xOverlap > tolerance && yOverlap > tolerance;
}

export async function expectNoCollisions(locators: Locator[], tolerance = 1): Promise<void> {
  const boxes: { box: Box; label: string }[] = [];
  for (const locator of locators) {
    const label = (await locator.getAttribute("aria-label")) ?? (await locator.innerText()).trim();
    boxes.push({ box: await boxOf(locator), label: label || "<element>" });
  }
  for (let i = 0; i < boxes.length; i += 1) {
    for (let j = i + 1; j < boxes.length; j += 1) {
      expect(
        overlaps(boxes[i].box, boxes[j].box, tolerance),
        `"${boxes[i].label}" overlaps "${boxes[j].label}"`,
      ).toBe(false);
    }
  }
}

export async function expectNoHorizontalOverflow(page: Page, slack = 2): Promise<void> {
  const { scrollWidth, clientWidth } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(
    scrollWidth - clientWidth,
    `horizontal overflow: content ${scrollWidth}px wider than viewport ${clientWidth}px`,
  ).toBeLessThanOrEqual(slack);
}

/** Assert a locator sits fully within the current viewport (no part clipped off any edge). */
export async function expectWithinViewport(page: Page, locator: Locator, slack = 1): Promise<void> {
  const viewport = page.viewportSize();
  expect(viewport, "viewport size is null").not.toBeNull();
  const { width, height } = viewport as { width: number; height: number };
  const box = await boxOf(locator);
  expect(box.x, "element clipped off the left edge").toBeGreaterThanOrEqual(-slack);
  expect(box.y, "element clipped off the top edge").toBeGreaterThanOrEqual(-slack);
  expect(box.x + box.width, "element clipped off the right edge").toBeLessThanOrEqual(
    width + slack,
  );
  expect(box.y + box.height, "element clipped off the bottom edge").toBeLessThanOrEqual(
    height + slack,
  );
}

export interface PageErrorLog {
  /** Uncaught exceptions — a real crash. Assert this is empty. */
  readonly pageErrors: string[];
  /** console.error output — useful context, but noisy (third-party libs), so don't hard-assert. */
  readonly consoleErrors: string[];
}

/**
 * Collect uncaught page errors (true crashes) separately from console.error output for the
 * lifetime of a test. Tests assert on pageErrors; consoleErrors is kept for diagnostics because
 * chart libraries (Plotly, Lightweight Charts) can log non-fatal console.error noise.
 */
export function collectPageErrors(page: Page): PageErrorLog {
  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];
  page.on("pageerror", (err) => pageErrors.push(err.message));
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  return { pageErrors, consoleErrors };
}
