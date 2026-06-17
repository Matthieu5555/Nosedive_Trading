import { expect, type Page, test } from "@playwright/test";

import {
  ANALYTICS_AAA,
  ANALYTICS_AAA_DEGENERATE,
  ANALYTICS_QUOTED,
} from "../src/test/fixtures";
import { collectPageErrors } from "./helpers";
import { mockBff } from "./mock-bff";

// MAT-LEGIBILITY charts-surface acceptance suite (disjoint from legibility.spec.ts).
//
// This file turns the design-language acceptance criteria that live ON THE CHARTS (the nappe, the
// smile, the Greeks curves) into real-browser assertions, per tasks/MAT-LEGIBILITY-qa-strategy.md and
// the three feature specs. The oracle is the owner's one line: can the PM tell what they're looking
// at on the chart, and would they ever be misled?
//
// What it asserts (the four the task names):
//   1. the chart's self-describing title/caption RE-WRITES itself when the selector changes — the
//      smile title carries the live tenor and a railed slice flips it to "⚠ degenerate fit"; the
//      nappe panel <h2>/caption track the live underlying · as-of · mode · coverage. No stale frame.
//   2. loading shows a footprint SKELETON (role=status, aria-busy, "Loading…"), never a blank
//      <figure>; the empty/uncaptured state NAMES its subject, never a generic blank chart.
//   3. numbers render WITH their unit (the price-structure block carries the currency on its column
//      headers and never fabricates a mid for an unquoted strike).
//   4. the assistant's every numeric answer carries a CITATION; an answer it can't ground carries no
//      fabricated number and reads in honest-gap tone.
//
// All assertions are on the PM's read (visible text / role / aria), never internal React state — the
// template is market-read-flow.spec.ts. Every test mocks the BFF at the network layer (mock-bff.ts),
// never a live BFF or data/. Per-test route overrides run after mockBff so they win (handlers fire
// most-recent-first). Every test asserts pageErrors == [] (a crash is the loudest silent failure).
//
// The running UI is the English-heading Market page whose nappe/smile/Greeks blocks already mount the
// self-describing descriptor from charts.tsx describeSurface (subject · as-of · mode · coverage),
// AsyncBlock's footprint skeleton, and the grounded AssistantPanel. The pairs whose feature is NOT
// yet wired are test.fixme with the full future assertion and the MAT-LEGIBILITY spec that retires it.

const FIGURE_SMILE = /^Volatility surface .* smile 3m \(0\.250y\)/;

async function gotoMarket(page: Page): Promise<void> {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "Market" })).toBeVisible();
  // SPX is the default index; the as-of selector resolves the latest recorded close.
  await expect(page.getByLabel("Index", { exact: true })).toHaveValue("SPX");
}

async function openTenor(page: Page): Promise<void> {
  const tenor = page.getByLabel("Tenor", { exact: true });
  await expect(tenor).toBeVisible();
  await tenor.selectOption("3m");
  await expect(tenor).toHaveValue("3m");
}

// --------------------------------------------------------------------------------------------------
// 1. The title re-writes itself off live state — no stale frame.
// --------------------------------------------------------------------------------------------------

test("the smile figure title carries the live selected tenor", async ({ page }) => {
  const errors = collectPageErrors(page);
  await mockBff(page);
  // ANALYTICS_AAA carries a single 3m slice; the default mock serves it on /api/analytics.
  await gotoMarket(page);
  await openTenor(page);

  // The smile figure's own caption is a sentence that names the tenor it is plotting — never a bare
  // constant "Smile". The figcaption is mirrored into the figure's aria-label by Plot.tsx.
  const smile = page.getByRole("figure", { name: FIGURE_SMILE });
  await expect(smile).toBeVisible();
  await expect(smile).toContainText("smile 3m (0.250y)");
  // It also states HOW to read it (puts ◄ ATM ► calls) — provenance/how-to on the chart itself.
  await expect(smile).toContainText("puts ◄ ATM ► calls");

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("the smile title flips to a degenerate-fit warning when the slice is railed", async ({
  page,
}) => {
  const errors = collectPageErrors(page);
  await mockBff(page);
  // ANALYTICS_AAA_DEGENERATE is a single 10d slice with surface_slice.degenerate = true and a railed
  // SVI fit (params at bound, not converged). The smile title must NOT render clean over it.
  await page.route("**/api/analytics**", (route) =>
    route.fulfill({ json: ANALYTICS_AAA_DEGENERATE }),
  );
  await gotoMarket(page);
  // The captured tenor is 10d here; select it so the smile plots the railed slice.
  const tenor = page.getByLabel("Tenor", { exact: true });
  await expect(tenor).toBeVisible();
  await tenor.selectOption("10d");

  const smile = page.getByRole("figure", { name: /smile 10d \(0\.027y\) .*⚠ degenerate fit/ });
  await expect(smile).toBeVisible();
  // The lie this forbids: a clean "smile 10d" caption with no degenerate flag over a railed fit.
  await expect(smile).toContainText("⚠ degenerate fit");

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("the price chart title interpolates the selected underlying", async ({ page }) => {
  const errors = collectPageErrors(page);
  await mockBff(page);
  await gotoMarket(page);

  // The daily price chart titles itself with the underlying it is PLOTTING — the label binds to the
  // payload's `underlying`, so it can never be false for its contents (PriceChart label =
  // `${underlying} — daily price (OHLC candlestick)`). The mock serves PRICE_HISTORY_AAA, so the
  // figure names AAA — a self-describing title, not a constant "Price".
  await expect(
    page.getByRole("figure", { name: /^AAA — daily price \(OHLC candlestick\)/ }),
  ).toBeVisible();
  // No constant bare-noun "Price" title — the figure name must carry the subject.
  await expect(page.getByRole("figure", { name: /^Price$/ })).toHaveCount(0);

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("the nappe panel heading and caption track the live underlying as-of and mode", async ({
  page,
}) => {
  const errors = collectPageErrors(page);
  await mockBff(page);
  await gotoMarket(page);

  // The nappe block's panel <h2> names the live subject; its caption is the descriptor sentence
  // (close <as-of> · <mode> · <coverage>). The mock's RECORDED_TWO_DATES latest close is 2026-05-29.
  const nappe = page.getByRole("article", { name: "Volatility surface — SPX" });
  await expect(nappe).toBeVisible();
  await expect(nappe.getByRole("heading", { name: "Volatility surface — SPX" })).toBeVisible();
  // Caption carries the as-of close and the strict mode word together, off one descriptor.
  await expect(nappe).toContainText("close 2026-05-29");
  await expect(nappe).toContainText("strict");

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("switching the index re-writes the nappe heading and caption together", async ({ page }) => {
  const errors = collectPageErrors(page);
  await mockBff(page);
  // SX5E recorded dates so the second index resolves to a close; reuse the SPX shape, re-keyed.
  await page.route("**/api/recorded-dates**", (route) =>
    route.fulfill({
      json: {
        index: "SX5E",
        count: 1,
        dates: ["2026-06-17"],
        available: [
          {
            date: "2026-06-17",
            run_id: "run-0617",
            recorded_ts: "2026-06-17T17:30:00",
            qc: "pass",
          },
        ],
      },
    }),
  );
  await gotoMarket(page);

  // Before: SPX. The heading names SPX.
  await expect(
    page.getByRole("article", { name: "Volatility surface — SPX" }),
  ).toBeVisible();

  // Switch the underlying selector → the heading AND caption must both re-write to the new subject
  // in the same paint. A stale "Nappe — SPX" heading next to an SX5E caption is the lie forbidden.
  await page.getByLabel("Index", { exact: true }).selectOption("SX5E");
  const sx5e = page.getByRole("article", { name: "Volatility surface — SX5E" });
  await expect(sx5e).toBeVisible();
  await expect(sx5e.getByRole("heading", { name: "Volatility surface — SX5E" })).toBeVisible();
  // SX5E close is 17:30 CET (OESX settlement, not 22:00) — the caption carries that close instant.
  await expect(sx5e).toContainText("close 2026-06-17 17:30 CET");
  // The stale SPX heading is gone — both labels never disagree on one screen.
  await expect(
    page.getByRole("article", { name: "Volatility surface — SPX" }),
  ).toHaveCount(0);

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

// --------------------------------------------------------------------------------------------------
// 2. No silent state — loading is a footprint skeleton, empty NAMES its subject, never a blank chart.
// --------------------------------------------------------------------------------------------------

test("a loading nappe shows a footprint skeleton, not a blank figure", async ({ page }) => {
  const errors = collectPageErrors(page);
  await mockBff(page);
  // Hold the analytics response open past the skeleton-visible floor (SKELETON_DELAY_MS = 1000ms) so
  // the skeleton has time to mount, then release. The skeleton reserves the chart footprint with a
  // role="status" aria-busy element labelled "Loading…" — never a bare blank <figure>.
  await page.route("**/api/analytics**", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1600));
    await route.fulfill({ json: ANALYTICS_AAA });
  });
  await gotoMarket(page);

  // While the fetch is in flight, the footprint skeleton is on screen (role=status, "Loading").
  const skeleton = page.getByRole("status", { name: /Loading/ }).first();
  await expect(skeleton).toBeVisible();
  // It is NOT a bare one-line "Loading…" text — it carries a real footprint (a reserved height box).
  const box = await skeleton.boundingBox();
  expect(box, "skeleton has no rendered footprint box").not.toBeNull();
  expect(box!.height, "skeleton reserves the chart footprint, not a one-line height").toBeGreaterThan(
    80,
  );

  // Then the real nappe figure resolves in its place. The surface figcaption uniquely ends with its
  // how-to-read gloss "vs maturity" (the smile/Greeks figures share the descriptor prefix but not it).
  await expect(
    page.getByRole("figure", { name: /Volatility surface .*vs log-moneyness vs maturity$/ }),
  ).toBeVisible({ timeout: 5000 });

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("an uncaptured tenor shows a named projection gap, never a blank chart", async ({ page }) => {
  const errors = collectPageErrors(page);
  await mockBff(page);
  // ANALYTICS_AAA captures only 3m. Select a far grid tenor that was NOT captured → the panel must
  // render a NAMED, honest empty state ("… is not captured … projection gap"), never a blank figure.
  await gotoMarket(page);
  const tenor = page.getByLabel("Tenor", { exact: true });
  await tenor.selectOption("3y");

  const gap = page.getByRole("status").filter({ hasText: "projection gap" });
  await expect(gap).toBeVisible();
  // It NAMES the subject (the tenor) — not a generic "No data".
  await expect(gap).toContainText("3y is not captured for this close");
  // No smile figure is plotted for the uncaptured tenor.
  await expect(page.getByRole("figure", { name: /smile 3y/ })).toHaveCount(0);

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("a failed analytics fetch renders a loud alert, not a blank panel", async ({ page }) => {
  const errors = collectPageErrors(page);
  await mockBff(page);
  // A 500 on the analytics fetch must surface as a role="alert" inside the nappe block (AsyncBlock's
  // error branch), never a silently dead/blank tile. The loud, recoverable read is the contract.
  await page.route("**/api/analytics**", (route) =>
    route.fulfill({ status: 500, json: { detail: "analytics backend exploded" } }),
  );
  await gotoMarket(page);

  await expect(page.getByRole("alert").first()).toBeVisible();
  // The error reads DIFFERENTLY from an empty state — it is an alert, not a status, and it is loud.
  await expect(page.getByRole("alert").first()).not.toHaveText("");

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

// FIXME — the silent-green canary. Retired by MAT-LEGIBILITY-coverage-headline +
// MAT-LEGIBILITY-strict-indicative-mode wiring the ANALYTICS_MARKET_CLOSED degenerate-coverage path
// through Market.tsx so the nappe caption raises the alarm rather than rendering a plausible surface.
test.fixme(
  "a market-closed degenerate surface is loudly flagged, never silent-green",
  async ({ page }) => {
    const errors = collectPageErrors(page);
    await mockBff(page);
    // ANALYTICS_MARKET_CLOSED (to be added to fixtures.ts): every surface_slice.degenerate = true,
    // zero two-sided rows. The nappe must say, in error tone, "market probably closed" — NOT a
    // plausible nappe off a closed market with nothing on screen saying so (the original sin).
    await gotoMarket(page);
    const nappe = page.getByRole("article", { name: /Volatility surface/ });
    await expect(nappe).toContainText("market probably closed");
    // Tone: the degenerate caption rides the alarm role, not a quiet status.
    await expect(nappe.getByRole("alert")).toBeVisible();
    expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
  },
);

// --------------------------------------------------------------------------------------------------
// 3. Numbers carry their unit; an unquoted strike reads as an honest gap, never a fabricated mid.
// --------------------------------------------------------------------------------------------------

test("the price-structure block carries its currency unit and never fabricates a mid", async ({
  page,
}) => {
  const errors = collectPageErrors(page);
  await mockBff(page);
  // ANALYTICS_QUOTED (SPX) carries per-strike bid/ask/volume; the 80 (30dp) strike has no quote.
  await page.route("**/api/analytics**", (route) => route.fulfill({ json: ANALYTICS_QUOTED }));
  await gotoMarket(page);
  await openTenor(page);

  const priceStructure = page.getByRole("table", { name: "Price structure — 3m (0.250y)" });
  await expect(priceStructure).toBeVisible();
  // The unit (currency) rides the column header — bid/ask carry the $ unit, not a bare number.
  await expect(priceStructure.getByRole("columnheader", { name: /bid.*\$/ })).toBeVisible();
  await expect(priceStructure.getByRole("columnheader", { name: /ask.*\$/ })).toBeVisible();
  // The atm row carries its real quote; the unquoted 30dp strike reads "—", never a synthesized mid.
  const rows = priceStructure.locator("tbody tr");
  await expect(rows.filter({ hasText: "atm" })).toContainText("4.1");
  await expect(rows.filter({ hasText: "30dp" })).toContainText("—");

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

// --------------------------------------------------------------------------------------------------
// 4. The grounded assistant — every numeric answer cites; an uncited number is refused.
// --------------------------------------------------------------------------------------------------

const ASSISTANT_FRAME = {
  underlying: "SPX",
  trade_date: "2026-05-29",
  run_id: "run-0529",
  mode: "strict" as const,
  close_instant: "17:30 CET",
  coverage_label: "1 706/2 412 quotes",
};

test("the assistant surfaces a number only with a citation that names its source", async ({
  page,
}) => {
  const errors = collectPageErrors(page);
  await mockBff(page);
  // The grounded answer: the BFF returns the answer text PLUS the citations (the only numbers the
  // assistant is allowed to surface, lifted verbatim from the server facts block). The panel must
  // render every citation as label + value + source — the number never floats free of provenance.
  await page.route("**/api/assistant", (route) =>
    route.fulfill({
      json: {
        answer: "The SPX surface on 2026-05-29 rests on 1,706 two-sided quotes.",
        citations: [
          {
            id: "coverage.two_sided",
            label: "Cotations two-sided",
            value: "1 706/2 412 quotes",
            source: "coverage @ close 2026-05-29",
          },
        ],
        grounded: true,
        frame: ASSISTANT_FRAME,
      },
    }),
  );
  await gotoMarket(page);

  await page.getByRole("button", { name: "Ask the assistant" }).click();
  await page.getByRole("button", { name: "What am I looking at?" }).click();

  // The citation list renders with the value and its source — the number is backed by provenance.
  const citations = page.getByRole("list", { name: "Citations" });
  await expect(citations).toBeVisible();
  await expect(citations).toContainText("1 706/2 412 quotes");
  await expect(citations).toContainText("coverage @ close 2026-05-29");

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("the assistant refuses to invent a number it cannot ground", async ({ page }) => {
  const errors = collectPageErrors(page);
  await mockBff(page);
  // The honest-gap contract: asked for a figure the facts block doesn't carry, grounded=false → the
  // answer is the loud "I won't invent it" copy, citations is empty, and NO fabricated number
  // appears. This is enforced in the data layer (the BFF), surfaced honestly by the panel.
  await page.route("**/api/assistant", (route) =>
    route.fulfill({
      json: {
        answer:
          "I can't ground this number on the current screen — there's no aggregate-gamma data on it.",
        citations: [],
        grounded: false,
        frame: ASSISTANT_FRAME,
      },
    }),
  );
  await gotoMarket(page);

  await page.getByRole("button", { name: "Ask the assistant" }).click();
  await page.getByRole("button", { name: "What am I looking at?" }).click();

  // The honest-gap answer reads in status tone (a non-blocking "I can't ground it"), with NO
  // citation list (no number is surfaced) — never a hallucinated figure.
  await expect(page.getByText(/Je ne peux pas fonder ce chiffre/)).toBeVisible();
  await expect(page.getByRole("list", { name: "Citations" })).toHaveCount(0);

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("the assistant frame echoes the on-screen subject, never a different one", async ({ page }) => {
  const errors = collectPageErrors(page);
  await mockBff(page);
  // The assistant answers from the SAME frame the screen shows (subject · close · mode · coverage).
  // Its answer caption must echo that frame — it can never describe a different surface than the page.
  await page.route("**/api/assistant", (route) =>
    route.fulfill({
      json: {
        answer: "Vous regardez la nappe SPX, mode strict.",
        citations: [],
        grounded: true,
        frame: ASSISTANT_FRAME,
      },
    }),
  );
  await gotoMarket(page);

  await page.getByRole("button", { name: "Ask the assistant" }).click();
  await page.getByRole("button", { name: "What am I looking at?" }).click();

  const assistant = page.getByRole("complementary", { name: "Assistant" });
  await expect(assistant).toContainText("SPX");
  await expect(assistant).toContainText("strict");
  await expect(assistant).toContainText("17:30 CET");

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});
