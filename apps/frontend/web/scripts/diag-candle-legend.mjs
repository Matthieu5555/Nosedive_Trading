// Real-browser verification for the candlestick OHLC legend fix (the "what the fuck is this unit"
// screenshot). The SIE daily-OHLC legend used to render ordinary ~264 stock prices in scientific
// notation ("2.654 × 10²"). After routing the legend through referencePrice it must read plain
// grouped prices ("264.00"). Drives the LIVE dev server on :5173 (real BFF, real SX5E data).
// Run: node scripts/diag-candle-legend.mjs
import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";

const BASE = "http://127.0.0.1:5173";
const OUT = "scripts/diag-shots/candle-legend";
mkdirSync(OUT, { recursive: true });

const log = (...a) => console.log(...a);
let failures = 0;
const check = (name, cond) => {
  log(`${cond ? "PASS" : "FAIL"}  ${name}`);
  if (!cond) failures++;
};

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1600, height: 1400 } });
const page = await ctx.newPage();

await page.goto(BASE + "/", { waitUntil: "networkidle" });
await page.waitForTimeout(1500);

// Ensure SX5E (SIE is an SX5E member).
try {
  await page.locator('select[aria-label="Index"]').selectOption("SX5E");
  await page.waitForTimeout(1500);
} catch {
  // already SX5E or single option
}

// Try to select SIE in the constituents table so the per-member candlestick renders it.
// The table rows are clickable; fall back to whatever member auto-selects if SIE isn't found.
let pickedSIE = false;
try {
  const sieRow = page.getByText(/^SIE\b/).first();
  if ((await sieRow.count()) > 0) {
    await sieRow.scrollIntoViewIfNeeded();
    await sieRow.click();
    pickedSIE = true;
    await page.waitForTimeout(2000);
  }
} catch {
  // leave on the auto-selected heaviest member
}
log(`selected SIE explicitly: ${pickedSIE}`);

// Read every candlestick legend on the page (index-level + selected member).
async function readLegends() {
  return await page.evaluate(() =>
    Array.from(document.querySelectorAll(".candle-legend")).map((el) => el.textContent || ""),
  );
}

// Wait for at least one non-empty legend.
let legends = [];
for (let i = 0; i < 10; i++) {
  legends = (await readLegends()).filter((t) => t.trim().length > 0);
  if (legends.length > 0) break;
  await page.waitForTimeout(800);
}

check("at least one candlestick legend rendered", legends.length > 0);
for (const text of legends) {
  log(`legend: ${text}`);
}

// The core assertion: NO scientific notation anywhere in any legend.
const sciMarker = /×\s*10|e[+-]?\d/i;
const anySci = legends.some((t) => sciMarker.test(t));
check("no scientific notation in any candle legend (× 10 / e-notation absent)", !anySci);

// And the O/H/L/C tokens must each be followed by a plain grouped price like 264.00 or 1,624.00
// (optionally a currency symbol). This proves the price actually rendered, not just absence of sci.
const ohlcPlain = legends.every((t) => {
  const m = t.match(/O\s+(\S+)\s+H\s+(\S+)\s+L\s+(\S+)\s+C\s+(\S+)/);
  if (!m) return false;
  const priceLike = /^[€$£¥]?[\d,]+\.\d{2}$/;
  return [m[1], m[2], m[3], m[4]].every((p) => priceLike.test(p));
});
check("O/H/L/C each render a plain 2-decimal grouped price", ohlcPlain);

await page.screenshot({ path: `${OUT}/01-market-full.png`, fullPage: true });

// A tight crop of the selected-member panel for the legend close-up.
const memberPanel = page.locator('[aria-label^="Price history for"]').first();
if ((await memberPanel.count()) > 0) {
  await memberPanel.scrollIntoViewIfNeeded();
  await page.waitForTimeout(500);
  await memberPanel.screenshot({ path: `${OUT}/02-member-legend.png` });
}

await ctx.close();
await browser.close();
log(`\n=== ${failures === 0 ? "ALL CHECKS PASSED" : failures + " CHECK(S) FAILED"} ===`);
process.exit(failures === 0 ? 0 : 1);
