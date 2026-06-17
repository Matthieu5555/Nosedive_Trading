import { expect, type Page, test } from "@playwright/test";

import {
  ANALYTICS_AAA,
  ANALYTICS_AAA_DEGENERATE,
  ANALYTICS_QUOTED,
} from "../src/test/fixtures";
import { collectPageErrors } from "./helpers";
import { mockBff } from "./mock-bff";

// Design-language acceptance e2e for the Market surface (tasks/MAT-LEGIBILITY-*.md +
// frontend-design-language-2026[-examples]). Every assertion is on the PM read — visible text,
// role, tone — never internal React state. The oracle for every test is the owner's one line:
// "can the PM tell what they're looking at, and would they ever be misled?"
//
// Two kinds of test live here, kept separate:
//   • LIVE — locks a truth the running app already holds (regression guard, must stay green).
//   • FIXME (test.fixme) — the future contract a named MAT-LEGIBILITY spec retires; the full
//     assertion is written now, only the feature is missing. The implementing agent flips
//     fixme→test in the same commit it ships the feature. `grep test.fixme e2e/market.spec.ts`
//     lists the open Market legibility frontier.
//
// Grounded in a read of apps/frontend/web/src on 2026-06-17 — the running Market page is the
// French analytics console (h1 "Market", panels "Cours quotidien — {index}", "Nappe de
// volatilité — {index}", "Dispersion (ρ̄) — {index}"), self-describing surface descriptors are
// wired (charts.tsx describeSurface), AsyncBlock mounts a footprint skeleton, and an
// AssistantPanel is summonable from the status row. The constants below are the rendered strings,
// hand-read from that source — never echoed from the component under test.

// SX5E is the only symbol that carries a published close instant in the running code
// (Market.tsx CLOSE_INSTANTS), so the close-instant pairs key off SX5E specifically.
const SX5E = "SX5E";

// A degenerate (market-probably-closed) analytics payload keyed to whatever index is selected.
// Reuses the shared ANALYTICS_AAA_DEGENERATE slice shape (every surface_slice.degenerate = true),
// re-stamped so it answers for the selected underlying — the silent-green canary fixture.
function degenerateFor(underlying: string) {
  return { ...ANALYTICS_AAA_DEGENERATE, underlying };
}

// An analytics payload with zero maturities — the "surface missing" branch in Market.tsx
// (maturities.length === 0), which renders the named empty-state copy rather than a blank chart.
function emptySurfaceFor(underlying: string) {
  return { underlying, trade_date: "2026-06-17", n_maturities: 0, maturities: [], surface: null };
}

async function gotoMarket(page: Page): Promise<void> {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "Market" })).toBeVisible();
}

// Select SX5E in the Index picker and wait for the status row to re-stamp to SX5E.
async function selectSx5e(page: Page): Promise<void> {
  const index = page.getByLabel("Index", { exact: true });
  await index.selectOption(SX5E);
  await expect(index).toHaveValue(SX5E);
}

// =================================================================================================
// Principle 2b — self-describing components (every label binds to live state)
// =================================================================================================

test("nappe panel heading is a self-describing sentence naming the underlying (not a constant noun)", async ({
  page,
}) => {
  // 2b.5 (LIVE): Market.tsx builds the nappe panel <h2> off the live (index) state via its own
  // describeSurface — "Nappe de volatilité — {underlying}". The default SPX load must name SPX in
  // the heading; a static <h2>Volatility nappe</h2> would fail this. (charts.tsx:79,123;
  // Market.tsx describeSurface.) NOTE: the Plotly figure's OWN aria-label is still the descriptor
  // built INSIDE VolSurface, which Market mounts without identity props — so the figure title is
  // "Nappe de volatilité — indéterminé · …". That gap is the fixme below; the visible heading the
  // eye lands on first is what's wired today.
  const errors = collectPageErrors(page);
  await mockBff(page);
  await page.route("**/api/analytics**", (route) => route.fulfill({ json: ANALYTICS_AAA }));

  await gotoMarket(page);
  await expect(
    page.getByRole("heading", { level: 2, name: /^Nappe de volatilité — SPX$/ }),
  ).toBeVisible();
  // The 3D surface figure renders (it is on screen), even though its own label does not yet carry
  // the subject (see fixme). The surface figure is the one whose how-to-read clause ends "vs
  // maturité" — distinct from the smile/Greeks figures that share the same descriptor prefix.
  await expect(
    page.getByRole("figure", { name: /^Nappe de volatilité — .* vs maturité/ }),
  ).toBeVisible();

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("switching the index re-writes the nappe title and the price/dispersion headings together", async ({
  page,
}) => {
  // 2b.7 (LIVE for the wired labels): one piece of state (index) drives the nappe heading + the
  // figure title + the price-block heading + the dispersion heading. Switch SPX→SX5E and they ALL
  // change in the same paint — no stale frame, no two labels disagreeing on one screen.
  const errors = collectPageErrors(page);
  await mockBff(page);
  // Serve a payload whose `underlying` follows the request so the descriptor can never be stale.
  await page.route("**/api/analytics**", (route) => {
    const u = new URL(route.request().url()).searchParams.get("underlying") ?? "SPX";
    return route.fulfill({ json: { ...ANALYTICS_AAA, underlying: u } });
  });

  await gotoMarket(page);
  await expect(page.getByRole("heading", { level: 2, name: /^Nappe de volatilité — SPX$/ })).toBeVisible();
  await expect(page.getByRole("heading", { level: 2, name: /^Cours quotidien — SPX$/ })).toBeVisible();
  await expect(page.getByRole("heading", { level: 2, name: /^Dispersion \(ρ̄\) — SPX$/ })).toBeVisible();

  await selectSx5e(page);

  // Every self-describing label now reads SX5E; none is left stale on SPX.
  await expect(page.getByRole("heading", { level: 2, name: /^Nappe de volatilité — SX5E$/ })).toBeVisible();
  await expect(page.getByRole("heading", { level: 2, name: /^Cours quotidien — SX5E$/ })).toBeVisible();
  await expect(page.getByRole("heading", { level: 2, name: /^Dispersion \(ρ̄\) — SX5E$/ })).toBeVisible();
  await expect(page.getByRole("heading", { level: 2, name: /SPX$/ })).toHaveCount(0);

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("smile title carries the selected tenor and the degenerate-fit warning when the fit is railed", async ({
  page,
}) => {
  // 2b.3 (LIVE): charts.tsx SmileChart appends the selected tenor and " ⚠ degenerate fit" when
  // surface_slice.degenerate. The degenerate fixture's lone slice is railed, so the smile title
  // must carry both the tenor and the warning — a clean "Smile" with no tenor/flag would be a lie.
  const errors = collectPageErrors(page);
  await mockBff(page);
  await page.route("**/api/analytics**", (route) =>
    route.fulfill({ json: degenerateFor("SPX") }),
  );

  await gotoMarket(page);
  // The tenor panel defaults to 3m, which the degenerate fixture does not capture (its only slice
  // is 10d), so it opens on a projection gap. Select 10d to render the railed smile, whose title
  // must carry the tenor and " ⚠ degenerate fit".
  const tenor = page.getByLabel("Tenor", { exact: true });
  await tenor.selectOption("10d");
  await expect(
    page.getByRole("figure", { name: /smile 10d.*⚠ degenerate fit/i }),
  ).toBeVisible();

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("smile and greeks axes carry their units in the house idiom", async ({ page }) => {
  // 2b.8 (LIVE): axis titles render "log-moneyness (k)", "vol implicite (%)", "strike (...)" via
  // charts.tsx AXIS_* constants off lib/format UNITS. An unlabeled/unitless axis is a bug. Plotly
  // draws axis titles into the SVG as text; assert the unit text is present in the smile figure.
  const errors = collectPageErrors(page);
  await mockBff(page);
  await page.route("**/api/analytics**", (route) => route.fulfill({ json: ANALYTICS_QUOTED }));

  await gotoMarket(page);
  const smile = page.getByRole("figure", { name: /smile 3m/i });
  await expect(smile).toBeVisible();
  // The unit-bearing axis titles are drawn as text inside the figure, in the house UNITS idiom
  // (lib/format.ts: logMoneyness = "ln(K/F)", vol = "Vol"). An unlabeled/unitless axis is a bug.
  await expect(smile).toContainText("log-moneyness (ln(K/F))");
  await expect(smile).toContainText("vol implicite (Vol)");

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("smile legend names the put and call wings actually plotted, not Series 1/2", async ({
  page,
}) => {
  // 2b.9 (LIVE): charts.tsx names the wing traces "puts"/"calls"; the legend names the real series.
  const errors = collectPageErrors(page);
  await mockBff(page);
  await page.route("**/api/analytics**", (route) => route.fulfill({ json: ANALYTICS_QUOTED }));

  await gotoMarket(page);
  const smile = page.getByRole("figure", { name: /smile 3m/i });
  await expect(smile).toBeVisible();
  await expect(smile).toContainText("puts");
  await expect(smile).toContainText("calls");
  await expect(smile).not.toContainText(/Series \d/);

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test.fixme(
  "nappe figure title carries subject · as-of · mode · coverage (the full identity sentence)",
  async ({ page }) => {
    // 2b.4 / 2b.7 FIXME — retired by MAT-LEGIBILITY-coverage-headline + -strict-indicative-mode.
    // Today Market.tsx mounts <VolSurface> WITHOUT the identity props (subject/asOf/closeInstant/
    // mode/coverage), so the descriptor degrades to "indéterminé · clôture date inconnue · strict ·
    // couverture indisponible" inside the figure title — self-describing in shape but blank on
    // identity. When the page threads the live (index, effectiveAsOf, mode, coverage) tuple into
    // VolSurface, the SX5E figure title must read the full sentence with the real coverage fraction
    // and the 17:30 CET close instant.
    const errors = collectPageErrors(page);
    await mockBff(page);
    await page.route("**/api/analytics**", (route) =>
      route.fulfill({ json: { ...ANALYTICS_AAA, underlying: SX5E } }),
    );

    await gotoMarket(page);
    await selectSx5e(page);
    await expect(
      page.getByRole("figure", {
        name: /^Nappe de volatilité — SX5E · clôture 2026-\d{2}-\d{2} 17:30 CET · strict · \d/,
      }),
    ).toBeVisible();
    expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
  },
);

test.fixme(
  "nappe panel caption carries the SX5E 17:30 CET close instant, not just the date",
  async ({ page }) => {
    // 2b.5 / 2b.6 FIXME — retired by MAT-LEGIBILITY-coverage-headline (the Market.tsx mount threads
    // the close instant into describeSurface). Today Market.tsx describeSurface is called with
    // instant = closeInstant(index), so SX5E SHOULD carry "17:30 CET" in the caption — but the
    // status line above the nappe prints only descriptor.asOfPhrase off effectiveAsOf, and the
    // coverage fraction is still null ("couverture indisponible"). When coverage lands, the nappe
    // caption must read "clôture {date} 17:30 CET · strict · {n}/{m} cotations".
    const errors = collectPageErrors(page);
    await mockBff(page);
    await page.route("**/api/analytics**", (route) =>
      route.fulfill({ json: { ...ANALYTICS_AAA, underlying: SX5E } }),
    );

    await gotoMarket(page);
    await selectSx5e(page);
    const nappe = page.getByRole("article", { name: /^Nappe de volatilité — SX5E$/ });
    await expect(nappe.getByText(/clôture 2026-\d{2}-\d{2} 17:30 CET · strict · \d.* cotations/)).toBeVisible();
    expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
  },
);

// =================================================================================================
// Principle 2 — legibility & provenance (numbers carry unit + where-from)
// =================================================================================================

test("scorecards render label + value + hint, and a null reads em-dash not a blank cell", async ({
  page,
}) => {
  // 2.1 (LIVE): Scorecards.tsx renders each card as label/value/hint; a null value is "—".
  // ANALYTICS_AAA has only a single 30dp/30dc-bracketing slice with no ATM bands, so some metrics
  // resolve to "—" (the honest gap), never a blank.
  const errors = collectPageErrors(page);
  await mockBff(page);
  await page.route("**/api/analytics**", (route) => route.fulfill({ json: ANALYTICS_AAA }));

  await gotoMarket(page);
  const band = page.getByRole("region", { name: "Volatility scorecards" });
  await expect(band).toBeVisible();
  // Every card carries a label; at least one metric reads the honest em-dash gap.
  await expect(band.getByText("ATM level")).toBeVisible();
  await expect(band.getByText("—").first()).toBeVisible();

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("the scorecard band carries the plain-language sign legend", async ({ page }) => {
  // 2.2 (LIVE): Scorecards.tsx renders the sign legend in PM words — "RV−IV > 0 = vol cheap (buy)"
  // and the vp definition — so a coloured number is never read without its meaning.
  const errors = collectPageErrors(page);
  await mockBff(page);
  await page.route("**/api/analytics**", (route) => route.fulfill({ json: ANALYTICS_AAA }));

  await gotoMarket(page);
  const legend = page.getByLabel("Sign legend");
  await expect(legend).toBeVisible();
  await expect(legend).toContainText("RV−IV > 0 = vol cheap (buy)");
  await expect(legend).toContainText("vp = vol point = 0.01 annualized IV");

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("the price-structure block shows bid/ask/volume and reads an unquoted strike as an honest gap", async ({
  page,
}) => {
  // 2.4 / 7.2 (LIVE): the per-strike block shows bid/ask/volume columns (never a synthesized mid),
  // and the no-quote strike reads "—". This is the seam that broke — a fabricated mid filling the
  // gap is the lie this asserts against. ANALYTICS_QUOTED is keyed to SPX (the default).
  const errors = collectPageErrors(page);
  await mockBff(page);
  await page.route("**/api/analytics**", (route) => route.fulfill({ json: ANALYTICS_QUOTED }));

  await gotoMarket(page);
  const priceStructure = page.getByRole("table", { name: "Price structure — 3m (0.250y)" });
  await expect(priceStructure).toBeVisible();
  await expect(priceStructure.getByRole("columnheader", { name: /^bid/ })).toBeVisible();
  await expect(priceStructure.getByRole("columnheader", { name: /^ask/ })).toBeVisible();
  await expect(priceStructure.getByRole("columnheader", { name: /^volume/ })).toBeVisible();
  const rows = priceStructure.locator("tbody tr");
  await expect(rows.filter({ hasText: "atm" })).toContainText("1,234");
  await expect(rows.filter({ hasText: "30dp" })).toContainText("—");

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test.fixme(
  "an info dot on a scorecard metric reveals its what-and-where-from provenance gloss",
  async ({ page }) => {
    // 2.3 FIXME — retired by MAT-LEGIBILITY-explanation-map (the <InfoDot> + explanation map).
    // The <InfoDot> primitive and the Scorecards provenance legend exist (Scorecards.tsx:162), but
    // Market.tsx mounts <Scorecards> WITHOUT underlying/asOf/runId, so the provenance legend (and
    // its InfoDot) is not rendered on the live Market surface. When the page threads those props,
    // the scorecard band must carry a provenance InfoDot that opens a non-modal "where these
    // numbers come from" tooltip naming the source capture.
    const errors = collectPageErrors(page);
    await mockBff(page);
    await page.route("**/api/analytics**", (route) => route.fulfill({ json: ANALYTICS_AAA }));

    await gotoMarket(page);
    const dot = page.getByRole("button", { name: /where these numbers come from/i });
    await expect(dot).toBeVisible();
    await dot.click();
    await expect(page.getByRole("tooltip")).toContainText(/persisted signals computed by the backend/i);
    expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
  },
);

// =================================================================================================
// Principle 3 — no silent state, ever (loading / empty / error read differently)
// =================================================================================================

test("a loading nappe shows a footprint skeleton, never a blank chart or a bare Loading line", async ({
  page,
}) => {
  // 3.4 (LIVE): AsyncBlock mounts <ChartSkeleton> (role=status, aria-busy) after SKELETON_DELAY_MS
  // while loading — reserving the panel footprint, never the bare one-line "Loading…". Delay the
  // analytics response past the skeleton floor (1s) so the skeleton is observed before data lands.
  const errors = collectPageErrors(page);
  await mockBff(page);
  await page.route("**/api/analytics**", async (route) => {
    await new Promise((r) => setTimeout(r, 1800));
    return route.fulfill({ json: ANALYTICS_AAA });
  });

  await gotoMarket(page);
  // A role=status, aria-busy skeleton labelled "Chargement…" appears while the fetch is in flight.
  const skeleton = page.locator('[role="status"][aria-busy="true"]').first();
  await expect(skeleton).toBeVisible();
  // It must NOT be the bare one-line "Loading…" text the skeleton replaced.
  await expect(page.getByText(/^Loading…$/)).toHaveCount(0);
  // Once data lands the nappe renders and the skeleton is gone.
  await expect(
    page.getByRole("heading", { level: 2, name: /^Nappe de volatilité — SPX$/ }),
  ).toBeVisible();

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("a failed analytics fetch shows a loud alert, not a silently dead panel", async ({ page }) => {
  // 3.2 (LIVE): AsyncBlock renders a fetch failure as role="alert" loud copy — never a blank tile.
  const errors = collectPageErrors(page);
  await mockBff(page);
  await page.route("**/api/analytics**", (route) =>
    route.fulfill({ status: 503, json: { detail: "analytics upstream unavailable" } }),
  );

  await gotoMarket(page);
  await expect(page.getByRole("alert").first()).toBeVisible();

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("an empty (no-maturity) nappe names its subject and as-of, never a blank chart", async ({
  page,
}) => {
  // 3.5 (LIVE): when analytics returns zero maturities Market.tsx renders descriptor.emptyCopy in a
  // role="status" panel — "Aucune cotation deux-faces pour {index} au {date} — marché probablement
  // fermé." — a named empty state, never a blank <figure>.
  const errors = collectPageErrors(page);
  await mockBff(page);
  await page.route("**/api/analytics**", (route) =>
    route.fulfill({ json: emptySurfaceFor("SPX") }),
  );

  await gotoMarket(page);
  await expect(
    page.getByText(/Aucune cotation deux-faces pour SPX au .* — marché probablement fermé\./),
  ).toBeVisible();

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("a degenerate surface is never rendered clean — its fit carries a loud quality flag", async ({
  page,
}) => {
  // 3.3 (LIVE, partial — the silent-green guard the running code DOES give). The original sin is a
  // plausible nappe off a railed/closed-market fit with nothing saying so. Today the nappe figure
  // carries "⚠ N slice flagged (excluded from view)" and the per-tenor smile appends " ⚠ degenerate
  // fit" — the railed fit is NEVER painted as clean. (The explicit, plain-words "marché probablement
  // fermé" market-closed ALARM on the nappe itself is the stronger contract the fixme below owns;
  // it needs the identity props threaded into VolSurface.) The measured oracle here: the degenerate
  // fixture (surface_slice.degenerate=true, NaN/108% railed IVs) surfaces a visible warning, so the
  // PM can never mistake it for a healthy surface.
  const errors = collectPageErrors(page);
  await mockBff(page);
  await page.route("**/api/analytics**", (route) =>
    route.fulfill({ json: degenerateFor("SPX") }),
  );

  await gotoMarket(page);
  // The nappe figure shows the flagged-slice warning, never a clean title over a railed fit.
  await expect(page.getByRole("figure", { name: /⚠.*slice flagged/i })).toBeVisible();
  // The per-tenor smile, once its captured tenor is selected, carries its own degenerate warning.
  await page.getByLabel("Tenor", { exact: true }).selectOption("10d");
  await expect(page.getByRole("figure", { name: /⚠ degenerate fit/i })).toBeVisible();

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test.fixme(
  "a market-closed nappe says 'marché probablement fermé' in plain words, not just a slice flag",
  async ({ page }) => {
    // 3.3 FIXME — retired by MAT-LEGIBILITY-coverage-headline + -strict-indicative-mode. The full
    // silent-green canary: a degenerate/market-closed surface must say, in error tone and plain PM
    // words, "marché probablement fermé" on the nappe the eye lands on — not only the engineering
    // "⚠ N slice flagged". charts.tsx describeSurface already emits that phrase when degenerate=true,
    // but Market.tsx mounts <VolSurface> WITHOUT the identity props, so the descriptor inside the
    // figure degrades to "strict · couverture indisponible" and the loud market-closed clause never
    // reaches the figure. When the page threads (subject/asOf/mode/coverage/degenerate) into
    // VolSurface, the rendered surface block must carry the plain-words alarm.
    const errors = collectPageErrors(page);
    await mockBff(page);
    await page.route("**/api/analytics**", (route) =>
      route.fulfill({ json: degenerateFor("SPX") }),
    );

    await gotoMarket(page);
    await expect(page.getByText(/marché probablement fermé/).first()).toBeVisible();
    expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
  },
);

test("an uncaptured tenor shows a named projection-gap, never a blank chart", async ({ page }) => {
  // 3.1 (LIVE): selecting a tenor the capture didn't reach shows TenorPanel's labelled projection
  // gap (role=status), not a blank figure. ANALYTICS_AAA captures only 3m; pick an empty tenor.
  const errors = collectPageErrors(page);
  await mockBff(page);
  await page.route("**/api/analytics**", (route) => route.fulfill({ json: ANALYTICS_AAA }));

  await gotoMarket(page);
  const tenor = page.getByLabel("Tenor", { exact: true });
  await expect(tenor).toBeVisible();
  // The tenor selector offers the full pinned grid; pick one ANALYTICS_AAA does not capture.
  const options = await tenor.locator("option").allTextContents();
  const uncaptured = options.find((o) => !/3m/.test(o));
  expect(uncaptured, `tenor options: ${options.join(", ")}`).toBeTruthy();
  await tenor.selectOption({ label: uncaptured as string });
  // A labelled status (projection gap), never a blank figure.
  await expect(page.getByRole("status").filter({ hasText: /projection|capture|smile/i }).first()).toBeVisible();

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

// =================================================================================================
// Principle 5 — contextual guidance (always-on micro-glosses + a next-step hint)
// =================================================================================================

test("the convexity readout carries its butterfly-formula gloss in place", async ({ page }) => {
  // 5.1 (LIVE): TenorPanel ConvexityReadout carries the formula gloss "butterfly: IV(25Δp) +
  // IV(25Δc) − 2·ATM (vp = vol point = 0.01 IV)" — an in-place explanation where curvature is read.
  const errors = collectPageErrors(page);
  await mockBff(page);
  await page.route("**/api/analytics**", (route) => route.fulfill({ json: ANALYTICS_QUOTED }));

  await gotoMarket(page);
  await expect(
    page.getByText(/butterfly: IV\(25Δp\) \+ IV\(25Δc\) − 2·ATM/),
  ).toBeVisible();

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

// =================================================================================================
// Principle 6 — AI-first assistant (grounded, cites provenance, never invents a number)
// =================================================================================================

test("the assistant is a summonable, non-blocking panel — a button, not a wall", async ({
  page,
}) => {
  // 6 (LIVE, structural): the AssistantPanel mounts collapsed as a "Demander à l'assistant" button
  // in the status row (non-blocking — a panel you summon, not a wall you must pass). Opening it
  // reveals the grounded actions and a close affordance.
  const errors = collectPageErrors(page);
  await mockBff(page);
  await page.route("**/api/analytics**", (route) => route.fulfill({ json: ANALYTICS_AAA }));

  await gotoMarket(page);
  const launch = page.getByRole("button", { name: "Demander à l'assistant" });
  await expect(launch).toBeVisible();
  await launch.click();
  const panel = page.getByRole("complementary", { name: "Assistant" });
  await expect(panel).toBeVisible();
  // The grounded "explain this screen" shortcut is offered.
  await expect(panel.getByRole("button", { name: "Qu'est-ce que je regarde ?" })).toBeVisible();
  // Non-blocking: it can be dismissed.
  await panel.getByRole("button", { name: "Fermer l'assistant" }).click();
  await expect(page.getByRole("button", { name: "Demander à l'assistant" })).toBeVisible();

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("the assistant renders only numbers the BFF payload carried, with a citation each", async ({
  page,
}) => {
  // 6.2 (LIVE — grounding enforced in the DATA layer, not the model's goodwill): the panel renders
  // ONLY the citations and answer text the /api/assistant payload returns. We mock a grounded
  // answer whose single number (18.4%) is carried as a citation; the panel must show that number
  // beside its citation label+source. A number not in the payload can never appear because the
  // panel renders the server's words verbatim — this asserts the front never synthesizes a figure.
  const errors = collectPageErrors(page);
  await mockBff(page);
  await page.route("**/api/analytics**", (route) => route.fulfill({ json: ANALYTICS_AAA }));
  await page.route("**/api/assistant", (route) =>
    route.fulfill({
      json: {
        answer: "L'ATM 3m ressort à 18.4% sur cette clôture.",
        grounded: true,
        citations: [
          { id: "atm", label: "ATM level", value: "18.4%", source: "run abc123" },
        ],
        frame: {
          underlying: "SPX",
          trade_date: "2026-05-29",
          run_id: "abc123",
          mode: "strict",
          close_instant: null,
          coverage_label: "1 706/2 412 cotations",
        },
      },
    }),
  );

  await gotoMarket(page);
  await page.getByRole("button", { name: "Demander à l'assistant" }).click();
  await page.getByRole("button", { name: "Qu'est-ce que je regarde ?" }).click();

  const citations = page.getByRole("list", { name: "Citations" });
  await expect(citations).toBeVisible();
  await expect(citations).toContainText("ATM level");
  await expect(citations).toContainText("18.4%");
  await expect(citations).toContainText("run abc123");
  // The frame caption wears the same provenance the screen shows.
  await expect(page.getByText(/SPX · strict · 1 706\/2 412 cotations/)).toBeVisible();

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("an ungrounded assistant answer refuses to state a number and shows no citation", async ({
  page,
}) => {
  // 6.2 (LIVE — the honest-gap path): when the BFF cannot ground the answer (grounded=false), the
  // panel shows the loud "I won't invent it" copy as a role=status gap and renders NO citation
  // list — an uncited number is refused, not fabricated. This is the anti-hallucination contract
  // delivered at the UI: the panel can only ever show what the grounded payload carried.
  const errors = collectPageErrors(page);
  await mockBff(page);
  await page.route("**/api/analytics**", (route) => route.fulfill({ json: ANALYTICS_AAA }));
  await page.route("**/api/assistant", (route) =>
    route.fulfill({
      json: {
        answer: "Je ne peux pas chiffrer cela : l'écran ne porte pas ce nombre.",
        grounded: false,
        citations: [],
        frame: {
          underlying: "SPX",
          trade_date: "2026-05-29",
          run_id: null,
          mode: "strict",
          close_instant: null,
          coverage_label: null,
        },
      },
    }),
  );

  await gotoMarket(page);
  await page.getByRole("button", { name: "Demander à l'assistant" }).click();
  await page.getByRole("button", { name: "Qu'est-ce que je regarde ?" }).click();

  await expect(page.getByText(/Je ne peux pas chiffrer cela/)).toBeVisible();
  // No citation list is rendered for an ungrounded answer (no fabricated number to cite).
  await expect(page.getByRole("list", { name: "Citations" })).toHaveCount(0);

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("an assistant transport failure surfaces a loud alert, never a silent or fabricated answer", async ({
  page,
}) => {
  // 6 / 3 (LIVE): a 502 from /api/assistant renders the panel's role="alert" "Assistant
  // indisponible" copy — the failure is loud, never a silently empty thread or an invented answer.
  const errors = collectPageErrors(page);
  await mockBff(page);
  await page.route("**/api/analytics**", (route) => route.fulfill({ json: ANALYTICS_AAA }));
  await page.route("**/api/assistant", (route) =>
    route.fulfill({ status: 502, json: { detail: "assistant_unavailable" } }),
  );

  await gotoMarket(page);
  await page.getByRole("button", { name: "Demander à l'assistant" }).click();
  await page.getByRole("button", { name: "Qu'est-ce que je regarde ?" }).click();

  await expect(page.getByRole("alert").filter({ hasText: /Assistant indisponible/ })).toBeVisible();

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test.fixme(
  "the assistant explains indicative mode without presenting it as the stored close",
  async ({ page }) => {
    // 6.3 FIXME — retired by MAT-LEGIBILITY-strict-indicative-mode. The load-bearing guardrail:
    // when mode=indicative the frame caption must say INDICATIF and the answer must never present an
    // indicative mark as the canonical stored close. The Market page has no strict/indicative toggle
    // yet (it hard-codes mode="strict" into the AssistantPanel), so this contract can't be exercised
    // end-to-end until the toggle lands and threads mode=indicative into the panel + the BFF frame.
    const errors = collectPageErrors(page);
    await mockBff(page);
    await page.route("**/api/analytics**", (route) =>
      route.fulfill({ json: { ...ANALYTICS_AAA, underlying: SX5E } }),
    );
    await page.route("**/api/assistant", (route) =>
      route.fulfill({
        json: {
          answer: "Cette nappe est INDICATIVE — des marques à une face, pas la clôture stockée.",
          grounded: true,
          citations: [],
          frame: {
            underlying: SX5E,
            trade_date: "2026-06-17",
            run_id: null,
            mode: "indicative",
            close_instant: "17:30 CET",
            coverage_label: "2 280/2 412 (574 marques indicatives)",
          },
        },
      }),
    );

    await gotoMarket(page);
    await selectSx5e(page);
    // The mode toggle (unbuilt) flips the surface to indicative before the assistant is asked.
    await page.getByRole("button", { name: /indicati/i }).click();
    await page.getByRole("button", { name: "Demander à l'assistant" }).click();
    await page.getByRole("button", { name: "Qu'est-ce que je regarde ?" }).click();
    await expect(page.getByText(/INDICATIF/)).toBeVisible();
    await expect(page.getByText(/pas la clôture stockée/)).toBeVisible();
    expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
  },
);

// =================================================================================================
// Principle 7 — one design system; spend boldness once
// =================================================================================================

test("the QC verdict renders through the shared QcBadge palette in the status line", async ({
  page,
}) => {
  // 7.1 (LIVE): the status line carries the shared QcBadge (aria-label "QC {verdict}") — one
  // verdict-tone primitive, never a new per-feature accent. RECORDED_TWO_DATES tags its dates with
  // a QC verdict the badge renders.
  const errors = collectPageErrors(page);
  await mockBff(page);
  await page.route("**/api/analytics**", (route) => route.fulfill({ json: ANALYTICS_AAA }));

  await gotoMarket(page);
  await expect(page.getByLabel(/^QC (pass|fail|unknown)$/)).toBeVisible();

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});
