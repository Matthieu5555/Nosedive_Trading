// Throwaway diagnostic: verify the assistant launcher moved bottom-LEFT, renders the spark glyph,
// and that opening the panel anchors to the same bottom-left corner. Run: node scripts/diag-assistant-launcher.mjs
import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";

const BASE = "http://127.0.0.1:5173";
const OUT = "scripts/diag-shots/assistant-launcher";
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
await page.goto(BASE + "/", { waitUntil: "networkidle" }).catch(() => {});
await page.waitForTimeout(900);

// Closed launcher
const launcher = page.getByRole("button", { name: "Ask the assistant" });
await launcher.waitFor({ state: "visible" });
const lBox = await launcher.boundingBox();
const spark = await page.locator(".assistant-launch__spark").count();
const sparkVisible = await page.locator(".assistant-launch__spark").isVisible();
const vw = page.viewportSize().width;
const vh = page.viewportSize().height;
const launchLeftGap = Math.round(lBox.x);
const launchRightGap = Math.round(vw - (lBox.x + lBox.width));
const launchBottomGap = Math.round(vh - (lBox.y + lBox.height));
await page.screenshot({ path: `${OUT}/closed-bottom-left.png` });

// Open the panel and check its corner
await launcher.click();
const panel = page.getByRole("complementary", { name: "Assistant" });
await panel.waitFor({ state: "visible" });
await page.waitForTimeout(300);
const pBox = await panel.boundingBox();
const panelLeftGap = Math.round(pBox.x);
const panelBottomGap = Math.round(vh - (pBox.y + pBox.height));
await page.screenshot({ path: `${OUT}/open-bottom-left.png` });

await ctx.close();
await browser.close();

console.log("=== CLOSED LAUNCHER ===");
console.log(`launcher box: x=${Math.round(lBox.x)} y=${Math.round(lBox.y)} w=${Math.round(lBox.width)} h=${Math.round(lBox.height)}`);
console.log(`gaps: left=${launchLeftGap}px right=${launchRightGap}px bottom=${launchBottomGap}px`);
console.log(`spark glyph: count=${spark} visible=${sparkVisible}`);
console.log(`anchored bottom-left? ${launchLeftGap < 40 && launchBottomGap < 40 && launchRightGap > 40 ? "YES" : "NO"}`);
console.log("=== OPEN PANEL ===");
console.log(`panel box: x=${Math.round(pBox.x)} y=${Math.round(pBox.y)} w=${Math.round(pBox.width)} h=${Math.round(pBox.height)}`);
console.log(`gaps: left=${panelLeftGap}px bottom=${panelBottomGap}px`);
console.log(`anchored bottom-left? ${panelLeftGap < 40 && panelBottomGap < 40 ? "YES" : "NO"}`);
