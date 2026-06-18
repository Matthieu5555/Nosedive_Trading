// Throwaway: screenshot the marimo gallery on :8200 to prove cells render.
// Run: node scripts/diag-marimo.mjs
import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";

const BASE = "http://127.0.0.1:8200";
const OUT = "scripts/diag-shots/marimo";
mkdirSync(OUT, { recursive: true });

const ROUTES = [
  ["landing", "/"],
  ["dashboard", "/dashboard"],
  ["vol", "/vol"],
  ["greeks", "/greeks"],
  ["market", "/market"],
  ["scenarios", "/scenarios"],
];

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
for (const [name, path] of ROUTES) {
  await page.goto(BASE + path, { waitUntil: "networkidle" }).catch(() => {});
  // marimo runs cells over a websocket after load; give it room to compute.
  await page.waitForTimeout(name === "landing" ? 500 : 6000);
  const info = await page.evaluate(() => ({
    title: document.title,
    plotly: document.querySelectorAll(".plotly, .js-plotly-plot").length,
    tables: document.querySelectorAll("table").length,
    text: (document.body.innerText || "").replace(/\s+/g, " ").slice(0, 120),
  }));
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
  console.log(
    `${name.padEnd(10)} plotly=${info.plotly} tables=${info.tables} | ${info.text}`,
  );
}
await browser.close();
