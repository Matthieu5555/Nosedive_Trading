// Throwaway diagnostic: drive the LIVE dev server on :5173 (real BFF on :8077),
// screenshot the PM views (Positions, Risk, Reconciliation) and the realized
// attribution waterfall on the Basket ④ tab. Real-browser verification, not jsdom.
// Run: node scripts/diag-pm-views.mjs [tag]
import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";

const BASE = "http://127.0.0.1:5173";
const TAG = process.argv[2] ?? "pm-views";
const OUT = `scripts/diag-shots/${TAG}`;
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 1600 } });
const page = await ctx.newPage();
const errors = [];
page.on("console", (m) => {
  if (m.type() === "error") errors.push(m.text());
});

async function shot(name) {
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
  console.log(`shot ${name}`);
}

// Positions
await page.goto(`${BASE}/positions`, { waitUntil: "networkidle" }).catch(() => {});
await page.waitForTimeout(1200);
console.log("positions has 'Open positions' table rows:", await page.locator("table tbody tr").count());
await shot("positions");

// Risk Scenarios (includes reconciliation)
await page.goto(`${BASE}/risk`, { waitUntil: "networkidle" }).catch(() => {});
await page.waitForTimeout(1200);
// Pick the demo portfolio so the attribution + risk aggregate scope to it.
const sel = page.getByLabel("Portfolio");
if (await sel.count()) {
  await sel.selectOption("pm-demo-book").catch(() => {});
  await page.waitForTimeout(1200);
}
console.log("risk: reconciliation Breaks-found badge:", await page.getByText(/Breaks found/i).count());
await shot("risk");

// Basket ④ Attribution — the realized day-over-day waterfall loads on tab open.
await page.goto(`${BASE}/basket`, { waitUntil: "networkidle" }).catch(() => {});
await page.waitForTimeout(800);
await page.getByRole("tab", { name: /Attribution/i }).click().catch(() => {});
await page.waitForTimeout(1500);
console.log(
  "realized day cards:",
  await page.locator('[aria-label^="Realized attribution day"]').count(),
);
console.log(
  "realized waterfalls:",
  await page.locator('[aria-label^="Realized P&L attribution"]').count(),
);
await shot("basket-attribution-realized");

await browser.close();
console.log("console errors:", errors.length);
for (const e of errors.slice(0, 10)) console.log("  ERR", e);
