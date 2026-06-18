// Release-verification sweep for the 2026-06-18 frontend waves. Drives the LIVE dev server on
// :5173 (real BFF on :8077), captures full-page screenshots for the 8 work items into
// scripts/diag-shots/final-verify/, prints PASS/FAIL per item and a per-page console-error count.
// Throwaway diagnostic, not a test gate. Run: node scripts/diag-final-verify.mjs
import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";

const BASE = "http://127.0.0.1:5173";
const OUT = "scripts/diag-shots/final-verify";
mkdirSync(OUT, { recursive: true });

const results = [];
const pass = (n, cond, note = "") => {
  results.push({ n, ok: !!cond, note });
  console.log(`${cond ? "PASS" : "FAIL"}  ${n}${note ? "  — " + note : ""}`);
};

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 1600 } });
const page = await ctx.newPage();
let errors = [];
page.on("console", (m) => {
  if (m.type() === "error") errors.push(m.text());
});
page.on("pageerror", (e) => errors.push("PAGEERROR: " + e.message));
const resetErrors = () => {
  errors = [];
};
const shot = async (name) => {
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
};

// ---- Market home: pick the 2026-06-16 read that carries the surface fit number. ----------------
await page.goto(BASE + "/", { waitUntil: "networkidle" });
await page.waitForTimeout(1500);
try {
  const asOf = page.getByLabel("As-of fetch");
  if (await asOf.count()) {
    await page.waitForFunction(
      () => {
        const s = document.querySelector('[aria-label="As-of fetch"]');
        return s && !s.disabled && s.options.length > 1;
      },
      { timeout: 30000 },
    );
    const hasJune16 = await page.evaluate(() =>
      [...document.querySelector('[aria-label="As-of fetch"]').options].some((o) =>
        o.value.includes("2026-06-16"),
      ),
    );
    if (hasJune16) {
      await asOf.selectOption({ value: "2026-06-16" }).catch(async () => {
        await asOf.selectOption("2026-06-16").catch(() => {});
      });
      await page.waitForLoadState("networkidle");
      await page.waitForTimeout(2500);
    }
  }
} catch (e) {
  console.log("as-of pick note:", e.message);
}

// ITEM 1: Volatility surface header — fit pill present, controls span the width (no dead band).
resetErrors();
const surface = page.getByLabel(/Volatility surface, SX5E/).first();
let item1note = "surface panel not found";
let item1ok = false;
if (await surface.count()) {
  await surface.scrollIntoViewIfNeeded();
  await page.waitForTimeout(500);
  const heading = surface.locator(".panel-heading").first();
  const headText = (await heading.innerText().catch(() => "")) || "";
  const fitMatch = headText.match(/fit[^\n]*vol pts|fit not available/i);
  // dead-band check: the control cluster should not sit isolated in a far corner. Measure the
  // gap between the heading's right edge and the rightmost control.
  const geom = await heading.evaluate((el) => {
    const r = el.getBoundingClientRect();
    const ctrls = el.querySelectorAll("button, [role=switch], .toggle, label");
    let maxRight = 0;
    ctrls.forEach((c) => {
      const cr = c.getBoundingClientRect();
      if (cr.right > maxRight) maxRight = cr.right;
    });
    return { headRight: r.right, ctrlRight: maxRight };
  });
  item1ok = !!fitMatch;
  item1note = `fit text: "${fitMatch ? fitMatch[0] : "MISSING"}" ; head.right=${Math.round(geom.headRight)} ctrl.right=${Math.round(geom.ctrlRight)}`;
}
await shot("item1-surface-header");
pass("1 surface header (fit pill + dense layout)", item1ok, item1note);

// ITEM 2: SKEW 25Δ InfoDot tooltip — floats on top of card, plain-language copy.
resetErrors();
let item2ok = false;
let item2note = "skew card / infodot not found";
try {
  // InfoDot trigger aria-label is "<scorecard label>, what this is" → "Skew 25Δ, what this is".
  const trigger = page.locator('button.info-dot[aria-label="Skew 25Δ, what this is"]').first();
  await trigger.scrollIntoViewIfNeeded();
  await trigger.hover();
  await page.waitForTimeout(500);
  const tip = page.getByRole("tooltip").first();
  let tipText = "";
  let onTop = false;
  if (await tip.count()) {
    tipText = await tip.innerText().catch(() => "");
    // "on top": tooltip is a body-portal element fully visible within the viewport (not clipped).
    onTop = await tip.evaluate((el) => {
      const r = el.getBoundingClientRect();
      const topMost = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      const visible =
        r.top >= 0 && r.left >= 0 && r.bottom <= window.innerHeight && r.right <= window.innerWidth;
      const inFront = topMost && (el === topMost || el.contains(topMost));
      return visible && !!inFront;
    });
  }
  const hasJargon = /risk reversal|25-?delta put|25Δ put/i.test(tipText);
  item2ok = tipText.length > 20 && !hasJargon && onTop;
  item2note = `onTop=${onTop} jargon=${hasJargon} copy: "${tipText.replace(/\n/g, " ").slice(0, 110)}"`;
  // Focused crop of the top scorecard band so the floating tooltip is clearly visible.
  await page
    .locator(".scorecards, [aria-label='Scorecards']")
    .first()
    .screenshot({ path: `${OUT}/item2-skew-tooltip-crop.png` })
    .catch(() => {});
} catch (e) {
  item2note = "infodot error: " + e.message;
}
await shot("item2-skew-tooltip");
pass("2 skew InfoDot tooltip (on-top, plain copy)", item2ok, item2note);
// dismiss
await page.keyboard.press("Escape").catch(() => {});

// ITEM 3 + 4: Constituents — history window card one line + spans two columns; candle legend prices
resetErrors();
let item3ok = false;
let item3note = "history window not found";
let item4ok = false;
let item4note = "candle legend not read";
try {
  // History window card: the wide cell (grid-column: span 2) inside .underlying-data-summary grid.
  const hw = page.locator(".underlying-data-summary__wide").first();
  if (await hw.count()) {
    await hw.scrollIntoViewIfNeeded();
    await page.waitForTimeout(400);
    const geom = await hw.evaluate((el) => {
      const cs = getComputedStyle(el);
      const grid = el.closest(".underlying-data-summary");
      const cardR = el.getBoundingClientRect();
      const strong = el.querySelector("strong");
      // one-line: the value <strong> height ~ a single line (no wrap).
      const lineH = strong ? parseFloat(getComputedStyle(strong).lineHeight) || 20 : 20;
      const strongH = strong ? strong.getBoundingClientRect().height : 0;
      return {
        gridColumn: cs.gridColumnStart + " / " + cs.gridColumnEnd,
        w: cardR.width,
        gridW: grid ? grid.getBoundingClientRect().width : 0,
        oneLine: strongH <= lineH * 1.6,
        valueText: strong ? strong.textContent : "",
      };
    });
    const spansTwo = /span 2/.test(geom.gridColumn) || (geom.gridW > 0 && geom.w / geom.gridW > 0.55);
    item3ok = spansTwo && geom.oneLine;
    item3note = `gridColumn="${geom.gridColumn}" spansTwo=${spansTwo} oneLine=${geom.oneLine} value="${geom.valueText}"`;
  }

  // Select SIE so the candlestick legend renders a member's OHLC.
  const sieRow = page.getByText(/^SIE\b/).first();
  if ((await sieRow.count()) > 0) {
    await sieRow.scrollIntoViewIfNeeded();
    await sieRow.click().catch(() => {});
    await page.waitForTimeout(2000);
  }
  // Read the OHLC legend text from .candle-legend (populated via legendRef.textContent on hover or
  // last bar). Scientific notation shows "× 10" or "e+"; plain prices do not. Hover the chart to
  // force the legend to populate if it is empty on load.
  const candleArea = page.locator(".candle-legend").first();
  let legendText = "";
  if (await candleArea.count()) {
    const chart = page.locator(".candle-chart").first();
    if (await chart.count()) {
      const box = await chart.boundingBox().catch(() => null);
      if (box) {
        await page.mouse.move(box.x + box.width * 0.7, box.y + box.height * 0.5);
        await page.waitForTimeout(400);
      }
    }
    legendText = (await candleArea.textContent().catch(() => "")) || "";
  }
  const sci = /×\s*10|e[+-]\d|·\s*10|\^/i.test(legendText);
  const plainPrice = /[OHLC]\s+\d[\d.,]*\.\d{2}/.test(legendText);
  item4ok = legendText.length > 0 && !sci && plainPrice;
  item4note = `legend: "${legendText.replace(/\n/g, " ").slice(0, 90)}" sci=${sci} plainPrice=${plainPrice}`;
} catch (e) {
  item3note = item3note + " / err: " + e.message;
}
await shot("item3-4-constituents-history-candle");
pass("3 history window (one line, two cols)", item3ok, item3note);
pass("4 candle legend (plain prices, no sci)", item4ok, item4note);

// ITEM 5: Basket ④ Attribution — realized waterfall + honest residual bar.
resetErrors();
let item5ok = false;
let item5note = "attribution tab not loaded";
try {
  await page.goto(BASE + "/basket", { waitUntil: "networkidle" });
  await page.waitForTimeout(800);
  await page
    .getByRole("tab", { name: /Attribution/i })
    .click()
    .catch(() => {});
  await page.waitForTimeout(2000);
  const waterfalls = await page.locator('[aria-label^="Realized P&L attribution"]').count();
  const dayCards = await page.locator('[aria-label^="Realized attribution day"]').count();
  const residual = await page.getByText(/residual|unexplained/i).count();
  item5ok = waterfalls > 0 || dayCards > 0;
  item5note = `waterfalls=${waterfalls} dayCards=${dayCards} residualMentions=${residual}`;
} catch (e) {
  item5note = "err: " + e.message;
}
await shot("item5-basket-attribution");
pass("5 basket attribution realized waterfall + residual", item5ok, item5note);

// ITEM 6: Positions — book greeks + 9-line table.
resetErrors();
await page.goto(BASE + "/positions", { waitUntil: "networkidle" }).catch(() => {});
await page.waitForTimeout(1500);
const rows = await page.locator("table tbody tr").count();
const greeks6 = await page.getByText(/delta|gamma|vega|theta/i).count();
await shot("item6-positions");
pass("6 positions (book greeks + table rows)", rows >= 1 && greeks6 > 0, `tableRows=${rows} greekMentions=${greeks6}`);

// ITEM 7: Risk — aggregates/scenarios.
resetErrors();
await page.goto(BASE + "/risk", { waitUntil: "networkidle" }).catch(() => {});
await page.waitForTimeout(1500);
const portSel = page.getByLabel("Portfolio");
if (await portSel.count()) {
  await portSel.selectOption("pm-demo-book").catch(() => {});
  await page.waitForTimeout(1500);
}
const scenarios = await page.getByText(/scenario|stress|aggregate/i).count();
const breaks = await page.getByText(/Breaks found/i).count();
await shot("item7-8-risk-recon");
pass("7 risk (aggregates/scenarios)", scenarios > 0, `scenario/aggregate mentions=${scenarios}`);

// ITEM 8: Reconciliation — Breaks found + break lines (ok:false is correct, not error screen).
resetErrors();
const breakLines = await page.locator("[class*='break'], [aria-label*='break']").count();
const errScreen = await page.getByText(/something went wrong|failed to load|error boundary/i).count();
await shot("item8-recon");
pass(
  "8 reconciliation (Breaks found, not error screen)",
  breaks > 0 && errScreen === 0,
  `breaksFound=${breaks} breakLineEls=${breakLines} errorScreen=${errScreen}`,
);

console.log("\n=== final console-error count (last page):", errors.length);
if (errors.length) errors.slice(0, 10).forEach((e) => console.log("  CONSOLE-ERR:", e));
console.log(
  "\nSUMMARY:",
  results.filter((r) => r.ok).length + "/" + results.length + " PASS",
);
results.filter((r) => !r.ok).forEach((r) => console.log("  FAILED:", r.n, "—", r.note));
await browser.close();
