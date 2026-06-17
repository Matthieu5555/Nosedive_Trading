// Throwaway diagnostic: drive the LIVE dev server on :5190 (real BFF), measure
// horizontal overflow per page at wide+narrow, screenshot, and report font load.
// Not a test gate; run with: node scripts/diag.mjs [tag]
import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";

const BASE = "http://127.0.0.1:5190";
const TAG = process.argv[2] ?? "baseline";
const OUT = `scripts/diag-shots/${TAG}`;
mkdirSync(OUT, { recursive: true });

const PAGES = [
  ["market", "/"],
  ["basket", "/basket"],
  ["signals", "/signals"],
  ["strategy", "/strategy"],
  ["risk", "/risk"],
  ["positions", "/positions"],
  ["operations", "/operations"],
];
const VIEWPORTS = [
  ["wide", 1440, 900],
  ["narrow", 390, 844],
];

const browser = await chromium.launch();
const results = [];
for (const [vpName, w, h] of VIEWPORTS) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h } });
  const page = await ctx.newPage();
  for (const [name, path] of PAGES) {
    await page.goto(BASE + path, { waitUntil: "networkidle" }).catch(() => {});
    await page.waitForTimeout(900);
    const metrics = await page.evaluate(() => {
      const el = document.scrollingElement || document.documentElement;
      // Find the widest element that exceeds the viewport, to name the offender.
      const vw = el.clientWidth;
      let worst = null;
      for (const node of document.querySelectorAll("body *")) {
        const r = node.getBoundingClientRect();
        if (r.right > vw + 1) {
          const over = r.right - vw;
          if (!worst || over > worst.over) {
            worst = {
              over: Math.round(over),
              right: Math.round(r.right),
              tag: node.tagName.toLowerCase(),
              cls: (node.className && node.className.toString().slice(0, 60)) || "",
            };
          }
        }
      }
      return {
        scrollWidth: el.scrollWidth,
        clientWidth: el.clientWidth,
        overflow: el.scrollWidth - el.clientWidth,
        worst,
        numericFont:
          document.fonts && document.fonts.check
            ? document.fonts.check('16px "Numeric Mono"')
            : null,
      };
    });
    await page.screenshot({ path: `${OUT}/${name}-${vpName}.png`, fullPage: true });
    results.push({ page: name, vp: vpName, ...metrics });
  }
  await ctx.close();
}
// Font load check on market
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
await page.goto(BASE + "/", { waitUntil: "networkidle" }).catch(() => {});
await page.waitForTimeout(600);
const fontInfo = await page.evaluate(async () => {
  if (document.fonts && document.fonts.ready) await document.fonts.ready;
  const loaded = [];
  document.fonts.forEach((f) => loaded.push(`${f.family} ${f.status}`));
  return { families: loaded };
});
await ctx.close();
await browser.close();

console.log("=== OVERFLOW (scrollWidth - clientWidth, px) ===");
for (const r of results) {
  const flag = r.overflow > 2 ? " <<< OVERFLOW" : "";
  console.log(
    `${r.page.padEnd(11)} ${r.vp.padEnd(7)} sw=${r.scrollWidth} cw=${r.clientWidth} over=${r.overflow}${flag}` +
      (r.worst ? `  offender=<${r.worst.tag} class="${r.worst.cls}"> over=${r.worst.over}px` : ""),
  );
}
console.log("=== FONTS ===");
console.log(fontInfo.families.join("\n") || "(none)");
