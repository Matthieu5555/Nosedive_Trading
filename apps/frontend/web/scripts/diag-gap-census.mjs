// Throwaway diagnostic: drive the LIVE dev server on :5190 (real BFF), walk the
// rendered DOM in a real browser, read COMPUTED spacing (gaps/margins/paddings),
// and report any value that is NOT a member of the spacing scale
// {0,2,4,8,12,16,24,32,48,64}px (the --space-* tokens). This catches drift that
// static lint cannot see: computed cascades, third-party styles, unmigrated bits.
// 1px borders, font line-heights, and any non-spacing property are intentionally
// excluded (we only inspect gap/margin/padding, only px units, only > 0).
// Not a test gate; scripts/** is eslint-ignored. Run with:
//   node scripts/diag-gap-census.mjs [tag]
import { chromium } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";

const BASE = "http://127.0.0.1:5190";
const TAG = process.argv[2] ?? "gap-census";
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

const CAP = 40; // top offenders kept per page

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
const results = [];

for (const [name, path] of PAGES) {
  await page.goto(BASE + path, { waitUntil: "networkidle" }).catch(() => {});
  await page.waitForTimeout(900);
  const census = await page.evaluate((cap) => {
    const SCALE = new Set([0, 2, 4, 8, 12, 16, 24, 32, 48, 64]);
    const PROPS = [
      "rowGap",
      "columnGap",
      "marginTop",
      "marginBottom",
      "marginLeft",
      "marginRight",
      "paddingTop",
      "paddingBottom",
      "paddingLeft",
      "paddingRight",
    ];
    // Prefer elements under .page (the app shell); fall back to body *.
    const scope = document.querySelector(".page") ? ".page, .page *" : "body *";
    const nodes = document.querySelectorAll(scope);

    // Parse a computed value to a px number, or null if it isn't a px length
    // we should grade (auto/normal/empty/percent/other units are not violations).
    const toPx = (v) => {
      if (v == null) return null;
      const s = String(v).trim();
      if (s === "" || s === "auto" || s === "normal") return null;
      if (!s.endsWith("px")) return null; // ignore %, em, rem, etc.
      const n = parseFloat(s);
      return Number.isFinite(n) ? n : null;
    };

    const descriptor = (el) => {
      const tag = el.tagName.toLowerCase();
      const cls =
        (el.className && el.className.toString().trim().slice(0, 50)) || "";
      return cls ? `${tag}.${cls.split(/\s+/).slice(0, 3).join(".")}` : tag;
    };

    const seen = new Map(); // dedupe key -> { desc, property, value }
    let scanned = 0;
    let rawViolations = 0;

    for (const el of nodes) {
      scanned++;
      const cs = getComputedStyle(el);
      for (const prop of PROPS) {
        const px = toPx(cs[prop]);
        if (px == null) continue;
        // Round to nearest 0.5 to absorb sub-pixel noise.
        const rounded = Math.round(px * 2) / 2;
        const intPx = Math.round(rounded);
        if (intPx <= 0) continue; // 0 is on the scale; nothing to flag
        // Allow +-0.5px tolerance around any scale member.
        const onGrid = SCALE.has(intPx) && Math.abs(rounded - intPx) <= 0.5;
        if (onGrid) continue;
        rawViolations++;
        const desc = descriptor(el);
        const key = `${desc}|${prop}|${rounded}`;
        if (!seen.has(key)) {
          seen.set(key, { desc, property: prop, value: rounded });
        }
      }
    }

    const offenders = Array.from(seen.values()).sort(
      (a, b) => b.value - a.value,
    );
    const dropped = Math.max(0, offenders.length - cap);
    return {
      scanned,
      scope,
      rawViolations,
      uniqueViolations: offenders.length,
      dropped,
      offenders: offenders.slice(0, cap),
    };
  }, CAP);
  results.push({ page: name, path, ...census });
}

await ctx.close();
await browser.close();

// Per-page report to stdout.
console.log("=== GAP CENSUS (computed gap/margin/padding off the spacing grid) ===");
console.log("scale = {0,2,4,8,12,16,24,32,48,64}px  |  +-0.5px tolerance\n");
let grandRaw = 0;
let grandUnique = 0;
for (const r of results) {
  grandRaw += r.rawViolations;
  grandUnique += r.uniqueViolations;
  const flag = r.uniqueViolations > 0 ? " <<<" : " ok";
  console.log(
    `${r.page.padEnd(11)} scanned=${String(r.scanned).padStart(4)} ` +
      `unique-violations=${String(r.uniqueViolations).padStart(3)} ` +
      `(raw=${r.rawViolations}) scope=${r.scope}${flag}`,
  );
  for (const o of r.offenders) {
    console.log(`    ${String(o.value + "px").padStart(7)}  ${o.property.padEnd(13)}  ${o.desc}`);
  }
  if (r.dropped > 0) {
    console.log(`    ... and ${r.dropped} more unique offender(s) dropped (cap=${CAP})`);
  }
  console.log("");
}

const report = {
  base: BASE,
  tag: TAG,
  generatedAt: new Date().toISOString(),
  scale: [0, 2, 4, 8, 12, 16, 24, 32, 48, 64],
  cap: CAP,
  pages: results,
};
const jsonPath = `${OUT}/gap-census.json`;
writeFileSync(jsonPath, JSON.stringify(report, null, 2));

console.log("=== TALLY ===");
console.log(
  `pages=${results.length}  unique-violations=${grandUnique}  raw-violations=${grandRaw}`,
);
console.log(`report written to ${jsonPath}`);

process.exit(0);
