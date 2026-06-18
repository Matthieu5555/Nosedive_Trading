// Real-browser verification for the Greeks delta-spike fix + the Clean surface toggle.
// Drives the LIVE dev server on :5173, opens Market, picks SX5E + the latest close, pins the 3m
// tenor, then reads the Plotly delta trace straight out of the chart's data and asserts:
//   - the delta trace is monotone (single branch) with no impossible jump (the old spike was a
//     ~1.0 step from about -0.5 to +0.5 within one strike)
//   - the Clean surface / All slices, raw toggle is present and flips aria-pressed
// Run: node scripts/diag-greeks-spike.mjs
import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";

const BASE = "http://127.0.0.1:5173";
const OUT = "scripts/diag-shots/greeks-spike";
mkdirSync(OUT, { recursive: true });

const log = (...a) => console.log(...a);
let failures = 0;
const check = (name, cond) => {
  log(`${cond ? "PASS" : "FAIL"}  ${name}`);
  if (!cond) failures++;
};

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 1200 } });
const page = await ctx.newPage();

await page.goto(BASE + "/", { waitUntil: "networkidle" });

// Select SX5E if the index picker offers it.
await page.waitForTimeout(1200);
const indexSel = page.locator('select[aria-label="Index"]');
try {
  await indexSel.selectOption("SX5E");
} catch {
  // already SX5E or single option
}
await page.waitForTimeout(1500);

// Pin the 3m tenor (the screenshot's slice).
const tenorSel = page.locator('select[aria-label="Tenor"]');
try {
  await tenorSel.selectOption("3m");
} catch {
  // 3m may already be selected
}
await page.waitForTimeout(1500);

// Pull the delta trace out of the live Greeks chart. The Plot mock isn't here (real browser), so we
// read the Plotly figure data: find the plot whose data has a trace named "delta".
async function readDelta() {
  return await page.evaluate(() => {
    const gds = Array.from(document.querySelectorAll(".js-plotly-plot"));
    for (const gd of gds) {
      const data = gd.data || (gd._fullData ? gd._fullData : null);
      if (!data) continue;
      const delta = data.find((t) => t.name === "delta");
      if (delta && delta.y && delta.y.length) {
        return { x: Array.from(delta.x), y: Array.from(delta.y) };
      }
    }
    return null;
  });
}

const delta = await readDelta();
check("found a delta trace in the live Greeks chart", delta !== null && delta.y.length > 1);

if (delta) {
  // Largest absolute step between consecutive delta values. The bug produced a step of ~1.0
  // (sign flip at the at-the-money strike). A single-branch call-delta curve steps in small
  // increments (~0.02 per delta band).
  let maxStep = 0;
  for (let i = 1; i < delta.y.length; i++) {
    maxStep = Math.max(maxStep, Math.abs(delta.y[i] - delta.y[i - 1]));
  }
  log(
    `delta trace: ${delta.y.length} pts, range [${Math.min(...delta.y).toFixed(3)}, ${Math.max(...delta.y).toFixed(3)}], maxStep=${maxStep.toFixed(3)}`,
  );
  check("no impossible delta jump (maxStep < 0.4, the spike was ~1.0)", maxStep < 0.4);

  // Strikes strictly increasing (deduplicated, no repeated at-the-money strike).
  let strictlyIncreasing = true;
  for (let i = 1; i < delta.x.length; i++) {
    if (delta.x[i] <= delta.x[i - 1]) strictlyIncreasing = false;
  }
  check("strikes strictly increasing (duplicate ATM strike collapsed)", strictlyIncreasing);

  // Monotone (a clean call-delta S-curve is non-increasing in strike).
  let nonIncreasing = true;
  for (let i = 1; i < delta.y.length; i++) {
    if (delta.y[i] > delta.y[i - 1] + 1e-6) nonIncreasing = false;
  }
  check("delta is monotone non-increasing (single branch, no spike)", nonIncreasing);
}

await page.screenshot({ path: `${OUT}/01-greeks-clean.png`, fullPage: true });

// Toggle: Clean surface vs All slices, raw.
const cleanBtn = page.getByRole("button", { name: /Clean surface/i });
const allBtn = page.getByRole("button", { name: /All slices, raw/i });
check("Clean surface button present", (await cleanBtn.count()) > 0);
check("All slices, raw button present", (await allBtn.count()) > 0);
if ((await cleanBtn.count()) > 0) {
  const pressedClean = await cleanBtn.first().getAttribute("aria-pressed");
  check("Clean surface is the default (aria-pressed=true)", pressedClean === "true");
  await allBtn.first().click();
  await page.waitForTimeout(2000);
  const pressedAll = await allBtn.first().getAttribute("aria-pressed");
  check("clicking All slices flips aria-pressed to true", pressedAll === "true");
  await page.screenshot({ path: `${OUT}/02-greeks-all-slices.png`, fullPage: true });

  // After toggling to all-slices, the delta curve fix is STILL applied (the branch fix is
  // unconditional), so the spike must remain absent.
  const deltaAll = await readDelta();
  if (deltaAll) {
    let maxStepAll = 0;
    for (let i = 1; i < deltaAll.y.length; i++) {
      maxStepAll = Math.max(maxStepAll, Math.abs(deltaAll.y[i] - deltaAll.y[i - 1]));
    }
    log(
      `all-slices delta: ${deltaAll.y.length} pts, range [${Math.min(...deltaAll.y).toFixed(3)}, ${Math.max(...deltaAll.y).toFixed(3)}], maxStep=${maxStepAll.toFixed(3)}`,
    );
    check("spike stays absent under All slices (branch fix is unconditional)", maxStepAll < 0.4);
  } else {
    check("delta trace still present under All slices", false);
  }
}

await ctx.close();
await browser.close();
log(`\n=== ${failures === 0 ? "ALL CHECKS PASSED" : failures + " CHECK(S) FAILED"} ===`);
process.exit(failures === 0 ? 0 : 1);
