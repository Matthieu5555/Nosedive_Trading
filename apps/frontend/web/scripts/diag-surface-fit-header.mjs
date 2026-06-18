// Throwaway diagnostic for the 2026-06-18 vol-surface-header / InfoDot / constituents wave.
// Drives the LIVE dev server on :5173 (real BFF on :8077), picks the SX5E read that carries a
// surface fit number (2026-06-16), and screenshots all four work items. Not a test gate.
// Run: node scripts/diag-surface-fit-header.mjs
import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";

const BASE = "http://127.0.0.1:5173";
const OUT = "scripts/diag-shots/surface-fit-header";
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

await page.goto(BASE + "/", { waitUntil: "networkidle" });

// Pick the 2026-06-16 capture (the read whose front slice carries iv_rmse). Wait for the picker to
// be enabled with its options loaded (it lands disabled while recorded-dates loads).
const asOf = page.getByLabel("As-of fetch");
await page.waitForFunction(
  () => {
    const s = document.querySelector('[aria-label="As-of fetch"]');
    return s && !s.disabled && s.options.length > 1;
  },
  { timeout: 30000 },
);
// The option value is the run_id-or-date key; for this flat SX5E read it is the bare date.
await asOf.selectOption("2026-06-16");
await page.waitForLoadState("networkidle");
await page.waitForTimeout(2500);

// ITEM i + A: the vol-surface header (title + caption + fit pill + toggles).
const surface = page.getByLabel(/Volatility surface, SX5E/).first();
await surface.scrollIntoViewIfNeeded();
await page.waitForTimeout(400);
const heading = surface.locator(".panel-heading").first();
await heading.screenshot({ path: `${OUT}/item-i-A-header.png` });

// Report the fit pill text + whether the controls span the header width (no dead band).
const fitText = await page
  .locator(".surface-fit")
  .first()
  .textContent()
  .catch(() => null);
const band = await heading.evaluate((el) => {
  const controls = el.querySelector(".panel-heading__controls");
  const r = controls.getBoundingClientRect();
  const hr = el.getBoundingClientRect();
  return {
    controlsWidth: Math.round(r.width),
    headingWidth: Math.round(hr.width),
    fillRatio: +(r.width / hr.width).toFixed(2),
  };
});
console.log("ITEM A fit pill:", JSON.stringify(fitText));
console.log("ITEM i header band:", JSON.stringify(band));

// ITEM ii: open the SKEW 25Δ InfoDot, confirm the tooltip floats fully on top (not clipped).
const skew = page.getByLabel("Skew 25Δ").first();
await skew.scrollIntoViewIfNeeded();
const dot = skew.getByRole("button", { name: /what this is/i });
await dot.hover();
await page.waitForTimeout(300);
const tip = page.getByRole("tooltip").first();
const tipBox = await tip.boundingBox();
// Element at the tooltip's centre should be the tooltip itself (i.e. it is on top, not behind).
const onTop = await page.evaluate(
  ({ x, y }) => {
    const el = document.elementFromPoint(x, y);
    return el ? el.closest(".info-tooltip") !== null : false;
  },
  { x: tipBox.x + tipBox.width / 2, y: tipBox.y + tipBox.height / 2 },
);
const tipText = await tip.textContent();
console.log("ITEM ii tooltip onTop:", onTop, "box:", JSON.stringify(tipBox));
console.log("ITEM ii tooltip text:", JSON.stringify(tipText));
await page.screenshot({ path: `${OUT}/item-ii-tooltip-on-top.png` });

// ITEM iii: the constituents history-window card on one line, spanning two columns.
await dot.evaluate(() => {}).catch(() => {});
await page.mouse.move(0, 0);
const summary = page.getByLabel("Underlying history coverage").first();
await summary.scrollIntoViewIfNeeded();
// The constituent price-history batch is slow; wait for the real date range to land so the shot
// shows the range on one line, not the "loading" placeholder. Falls through if it stays slow.
await page
  .waitForFunction(
    () => {
      const c = document.querySelector(".underlying-data-summary__wide strong");
      return c && /\d{4}-\d{2}-\d{2}/.test(c.textContent || "");
    },
    { timeout: 45000 },
  )
  .catch(() => console.log("ITEM iii: batch slow, window still loading"));
await summary.screenshot({ path: `${OUT}/item-iii-history-window.png` });
const wide = await page.evaluate(() => {
  const card = document.querySelector(".underlying-data-summary__wide");
  if (!card) return null;
  const strong = card.querySelector("strong");
  const cs = getComputedStyle(card);
  return {
    text: strong ? strong.textContent : null,
    gridColumn: cs.gridColumnStart + " / " + cs.gridColumnEnd,
    lineCount: strong ? Math.round(strong.getBoundingClientRect().height / 20) : null,
    widthPx: Math.round(card.getBoundingClientRect().width),
  };
});
console.log("ITEM iii history-window:", JSON.stringify(wide));

await browser.close();
console.log("screenshots ->", OUT);
