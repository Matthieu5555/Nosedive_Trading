// Real-browser verification for the per-CALL / per-PUT / Combined vol-surface selector + the
// per-maturity isolation (the owner's Onglet-1 asks). Drives the LIVE dev server, opens Market,
// picks SX5E + the latest close, then:
//   - asserts the Call / Put / Combined selector is present and lands on Combined
//   - flips to Calls, screenshots the CALL surface, reads the live Plotly z-grid
//   - flips to Puts, screenshots the PUT surface, asserts the z-grid changed (real per-side data)
//   - isolates a single maturity, screenshots the cleaner 2D smile read
// Run: node scripts/diag-vol-side-selectors.mjs
import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";

const BASE = process.env.BASE || "http://127.0.0.1:5184";
const OUT = "scripts/diag-shots/vol-side-selectors";
mkdirSync(OUT, { recursive: true });

const log = (...a) => console.log(...a);
let failures = 0;
const check = (name, cond) => {
  log(`${cond ? "PASS" : "FAIL"}  ${name}`);
  if (!cond) failures++;
};

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 1300 } });
const page = await ctx.newPage();
page.on("pageerror", (e) => {
  log("PAGEERROR", e.message);
  failures++;
});

await page.goto(BASE + "/", { waitUntil: "networkidle" });
await page.waitForTimeout(1000);
try {
  await page.locator('select[aria-label="Index"]').selectOption("SX5E");
} catch {
  /* single option */
}
await page.waitForTimeout(2000);

// The surface side selector.
const sideGroup = page.locator('[role="group"][aria-label="Surface side"]');
await sideGroup.waitFor({ state: "visible", timeout: 15000 });
const combinedBtn = sideGroup.getByRole("button", { name: "Combined" });
const callsBtn = sideGroup.getByRole("button", { name: "Calls" });
const putsBtn = sideGroup.getByRole("button", { name: "Puts" });

check("side selector present", await sideGroup.count() === 1);
check(
  "lands on Combined",
  (await combinedBtn.getAttribute("aria-pressed")) === "true",
);

// Read the live Plotly surface z-grid (the 3D nappe trace carries z).
async function surfaceZ() {
  return await page.evaluate(() => {
    const gds = Array.from(document.querySelectorAll(".js-plotly-plot"));
    for (const gd of gds) {
      const data = gd.data || gd._fullData;
      if (!data) continue;
      const surf = data.find((t) => t.type === "surface" && t.z);
      if (surf) return JSON.stringify(surf.z).slice(0, 400);
    }
    return null;
  });
}

await page.waitForTimeout(1200);
const combinedZ = await surfaceZ();
check("combined surface drawn", !!combinedZ);

// CALL surface.
await callsBtn.click();
await page.waitForTimeout(1500);
check("Calls pressed", (await callsBtn.getAttribute("aria-pressed")) === "true");
const callZ = await surfaceZ();
check("call surface drawn", !!callZ);
check("call grid differs from combined", callZ !== combinedZ);
await page.locator('article[aria-label^="Volatility surface"]').scrollIntoViewIfNeeded();
await page.screenshot({ path: `${OUT}/01-call-surface.png` });

// PUT surface.
await putsBtn.click();
await page.waitForTimeout(1500);
check("Puts pressed", (await putsBtn.getAttribute("aria-pressed")) === "true");
const putZ = await surfaceZ();
check("put surface drawn", !!putZ);
check("put grid differs from call", putZ !== callZ);
await page.locator('article[aria-label^="Volatility surface"]').scrollIntoViewIfNeeded();
await page.screenshot({ path: `${OUT}/02-put-surface.png` });

// SINGLE-MATURITY view (the cleaner 2D smile read for the isolated maturity).
const maturitySel = page.locator('select[aria-label="Maturity"]');
const options = await maturitySel.locator("option").allTextContents();
const oneMaturity = options.find((o) => !/all maturities/i.test(o));
check("maturity selector offers a single tenor", !!oneMaturity);
if (oneMaturity) {
  await maturitySel.selectOption({ label: oneMaturity });
  await page.waitForTimeout(1500);
  // The surface panel should now carry a 2D smile (scatter), not the 3D surface trace.
  const hasSmile = await page.evaluate(() => {
    const article = Array.from(document.querySelectorAll("article")).find((a) =>
      (a.getAttribute("aria-label") || "").startsWith("Volatility surface"),
    );
    if (!article) return false;
    const gds = Array.from(article.querySelectorAll(".js-plotly-plot"));
    for (const gd of gds) {
      const data = gd.data || gd._fullData;
      if (data && data.some((t) => t.type === "scatter")) return true;
    }
    return false;
  });
  check("single maturity shows the 2D smile", hasSmile);
  await page.locator('article[aria-label^="Volatility surface"]').scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${OUT}/03-single-maturity-smile.png` });
}

log(`\n${failures === 0 ? "ALL PASS" : failures + " FAILURE(S)"}  shots in ${OUT}`);
await browser.close();
process.exit(failures === 0 ? 0 : 1);
