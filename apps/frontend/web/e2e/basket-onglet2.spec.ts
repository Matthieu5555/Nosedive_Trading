import { expect, type Page, test } from "@playwright/test";

import { collectPageErrors } from "./helpers";
import { mockBff } from "./mock-bff";

// Stream-B Onglet-2 (Risque) end-to-end flow, in a real browser against a mocked BFF:
//   ① Composer — layer two sub-strategies into one book and compose it (read the combined +
//      per-layer dollar Greeks, each with its unit string).
//   ③ Choquer — shock the composed basket; read the spot×vol stress surface AND the parallel
//      rate-sweep cells (each labelled in bp and dollars).
//   ④ Attribution — read the P&L waterfall; assert the second-order terms (Rho/Vanna/Volga)
//      render BESIDE the first-order Δ/Γ/Vega/Θ, with dollar unit strings.
// Every assertion is on visible operator-facing text/labels, never on internal state.

const SUB_STRATEGIES = { n_sub_strategies: 2, sub_strategies: ["SPX", "SX5E"] };

function dollarGreek(value: number, unit: string) {
  return { value, unit };
}

// A composed two-layer book. The combined row is the additive sum of the two layers — the values
// here are internally consistent (layer1 + layer2 = combined) so the rendered table is coherent.
const COMPOSE_BOOK = {
  book_id: "book-SPX-latest",
  valuation_ts: "2026-06-05T20:00:00+00:00",
  composition_version: "composition-1.0.0",
  config_hashes: { layer_set: "h1", grid: "h2", monetization: "h3" },
  combined: {
    level: "book",
    layer_label: "__book__",
    layer_index: -1,
    net_delta: -10,
    net_gamma: 0.5,
    net_vega: 800,
    net_theta: -120,
    dollar_delta: dollarGreek(-5000, "$ per $1 of underlying"),
    dollar_gamma: dollarGreek(1500, "$ per 1% move"),
    dollar_vega: dollarGreek(800, "$ per 1 vol point"),
    dollar_theta: dollarGreek(-120, "$ per calendar day"),
    dollar_rho: dollarGreek(30, "$ per 1% rate"),
  },
  layers: [
    {
      level: "layer",
      layer_label: "vol-seller",
      layer_index: 0,
      net_delta: -6,
      net_gamma: 0.3,
      net_vega: 500,
      net_theta: -80,
      dollar_delta: dollarGreek(-3000, "$ per $1 of underlying"),
      dollar_gamma: dollarGreek(900, "$ per 1% move"),
      dollar_vega: dollarGreek(500, "$ per 1 vol point"),
      dollar_theta: dollarGreek(-80, "$ per calendar day"),
      dollar_rho: dollarGreek(18, "$ per 1% rate"),
      n_legs: 1,
      n_resolved: 1,
    },
    {
      level: "layer",
      layer_label: "crash-hedge",
      layer_index: 1,
      net_delta: -4,
      net_gamma: 0.2,
      net_vega: 300,
      net_theta: -40,
      dollar_delta: dollarGreek(-2000, "$ per $1 of underlying"),
      dollar_gamma: dollarGreek(600, "$ per 1% move"),
      dollar_vega: dollarGreek(300, "$ per 1 vol point"),
      dollar_theta: dollarGreek(-40, "$ per calendar day"),
      dollar_rho: dollarGreek(12, "$ per 1% rate"),
      n_legs: 1,
      n_resolved: 1,
    },
  ],
  diversification_ratio: 1.4,
  surface: {
    scenario_version: "scn-compose",
    spot_axis: [-0.1, 0, 0.1],
    vol_axis: [-0.05, 0, 0.05],
    pnl_grid: [
      [-4000, -3000, -2000],
      [-100, 0, 120],
      [3000, 4000, 5000],
    ],
  },
};

// The basket stress payload: a spot×vol surface AND a parallel rate sweep with bp/dollar labels.
const BASKET_STRESS = {
  basket_id: "basket-SPX-latest",
  trade_date: "2026-06-05",
  underlying: "SPX",
  surface: {
    spot_shock: [-0.1, 0, 0.1],
    vol_shock: [-0.05, 0, 0.05],
    scenario_pnl: [
      [-4200, -3100, -2050],
      [-90, 0, 130],
      [3100, 4050, 5100],
    ],
    scenario_version: "scn-stress",
    unit: "$ (full-reprice PnL)",
    n_cells: 9,
    has_holes: false,
    n_holes: 0,
  },
  worst_case: { spot_shock: -0.1, vol_shock: -0.05, pnl: -4200, unit: "$ (full-reprice PnL)" },
  n_legs: 2,
  n_resolved: 2,
  gaps: [],
  n_gaps: 0,
  rate: [
    {
      scenario_id: "rate_-0.0025",
      rate_shock: -0.0025,
      bp: -25,
      scenario_pnl: -680,
      scenario_version: "scn-stress",
      n_legs: 2,
      unit: "$ (full-reprice PnL)",
      bp_unit: "bp",
    },
    {
      scenario_id: "rate_+0.0000",
      rate_shock: 0,
      bp: 0,
      scenario_pnl: 0,
      scenario_version: "scn-stress",
      n_legs: 2,
      unit: "$ (full-reprice PnL)",
      bp_unit: "bp",
    },
    {
      scenario_id: "rate_+0.0025",
      rate_shock: 0.0025,
      bp: 25,
      scenario_pnl: 710,
      scenario_version: "scn-stress",
      n_legs: 2,
      unit: "$ (full-reprice PnL)",
      bp_unit: "bp",
    },
  ],
  n_rate: 3,
};

// The attribution waterfall: first-order Δ/Γ/Vega/Θ AND second-order Rho/Vanna/Volga + residual.
// Charm is a display Greek and is deliberately absent from the terms.
const ATTRIBUTION = {
  found: true,
  trade_date: "2026-06-05",
  portfolio_id: "demo-book",
  level: "book",
  contract_key: "__book__",
  terms: [
    { name: "Delta", dollars: 1000, unit: "$ (PnL)" },
    { name: "Gamma", dollars: -250, unit: "$ (PnL)" },
    { name: "Vega", dollars: 400, unit: "$ (PnL)" },
    { name: "Theta", dollars: -120, unit: "$ (PnL)" },
    { name: "Rho", dollars: 30, unit: "$ (PnL)" },
    { name: "Vanna", dollars: -15, unit: "$ (PnL)" },
    { name: "Volga", dollars: 8, unit: "$ (PnL)" },
  ],
  residual: { dollars: 47, unit: "$ (PnL)" },
  verdict: { within_tolerance: true, residual_abs_tol: 100, residual_rel_tol: 0.001 },
};

// Route the Onglet-2 seams per path+method on top of the shared BFF mock. The shared mock catches
// every other /api/** call (indices, delta-bands, …) so no panel renders an error.
async function routeOnglet2(page: Page): Promise<void> {
  await mockBff(page);
  await page.route(
    (url) => url.pathname === "/api/compose/sub-strategies",
    (route) => route.fulfill({ json: SUB_STRATEGIES }),
  );
  await page.route(
    (url) => url.pathname === "/api/compose",
    (route) => route.fulfill({ json: COMPOSE_BOOK }),
  );
  await page.route(
    (url) => url.pathname === "/api/basket/scenarios",
    (route) => route.fulfill({ json: BASKET_STRESS }),
  );
  await page.route(
    (url) => url.pathname === "/api/attribution",
    (route) => route.fulfill({ json: ATTRIBUTION }),
  );
}

test("Onglet-2: compose a 2-layer book, shock it (spot/vol + rate), read the attribution", async ({
  page,
}) => {
  const { pageErrors } = collectPageErrors(page);
  await routeOnglet2(page);
  await page.goto("/risque");
  await expect(page.getByRole("heading", { level: 1, name: "Risque" })).toBeVisible();

  // ── ① Composer: build a 2-layer book ───────────────────────────────────────────────────
  // Two legs in the shared composer (a straddle template seeds two legs → "can stress").
  await page.getByRole("button", { name: /template straddle/i }).click();
  const legs = page.getByRole("table", { name: /composed legs/i });
  await expect(legs.getByText("atm").first()).toBeVisible();

  // Add two layers, then compose the book.
  const composerTab = page.getByRole("tabpanel");
  await page.getByRole("button", { name: "add layer", exact: true }).click();
  await page.getByRole("button", { name: "add layer", exact: true }).click();
  const layerTable = page.getByRole("table", { name: /composed layers/i });
  await expect(layerTable.getByRole("row")).toHaveCount(3); // header + 2 layers

  await composerTab.getByRole("button", { name: /compose book/i }).click();

  // The combined book table renders with the combined row and both layers, each $-Greek labelled.
  const bookTable = page.getByRole("table", { name: /book-additive sum/i });
  await expect(bookTable).toBeVisible();
  const combinedRow = page.getByRole("row", { name: "combined book Greeks" });
  await expect(combinedRow).toBeVisible();
  await expect(page.getByRole("row", { name: "layer vol-seller" })).toBeVisible();
  await expect(page.getByRole("row", { name: "layer crash-hedge" })).toBeVisible();
  // The combined dollar-delta value (-5000 → "5 × 10³" in the app's sci notation) and a $-Greek
  // column header are both visible — proving the monetized value + its unit render to the operator.
  await expect(combinedRow.getByText(/5 × 10/).first()).toBeVisible();
  await expect(bookTable.getByRole("columnheader", { name: /delta \$/i })).toBeVisible();

  // ── ③ Choquer: shock the book across spot/vol and the rate sweep ────────────────────────
  await page.getByRole("tab", { name: /choquer/i }).click();
  await page.getByRole("button", { name: /stress basket/i }).click();

  // The stress surface summary renders with its cell count and PnL unit.
  await expect(page.getByRole("heading", { name: "Stress summary" })).toBeVisible();
  await expect(page.getByText("9 cells").first()).toBeVisible();

  // The parallel rate sweep renders its own labelled table; the ±25 bp cells are visible and
  // each carries its bp unit. This is the ±rate read the spec calls out.
  const sweep = page.getByRole("article", { name: "Rate-shock sweep" });
  await expect(sweep.getByRole("heading", { name: "Rate-shock sweep" })).toBeVisible();
  const sweepTable = sweep.getByRole("table", { name: /Rate-shock sweep/i });
  await expect(sweepTable).toBeVisible();
  // bp labels (basis points) render on the rate rows — user-facing unit assertion. The ±25 bp
  // shocks render in the app's scientific notation ("2.5 × 10¹ bp"), so assert the mantissa and
  // the literal "bp" unit string that the operator reads.
  await expect(sweepTable.getByText(/2\.5 × 10/).first()).toBeVisible();
  await expect(sweepTable.getByText(/\bbp\b/).first()).toBeVisible();

  // ── ④ Attribution: read the waterfall; Rho/Vanna/Volga beside Δ/Γ/Vega/Θ ────────────────
  await page.getByRole("tab", { name: /attribution/i }).click();
  await page.getByLabel("portfolio").fill("demo-book");
  await page.getByRole("button", { name: /P&L attribution/i }).click();

  const waterfall = page.getByRole("article", { name: "P&L attribution" });
  await expect(waterfall).toBeVisible();
  const legend = waterfall.getByRole("list", { name: "attribution terms" });
  // First-order terms.
  for (const term of ["Delta", "Gamma", "Vega", "Theta"]) {
    await expect(legend.getByText(new RegExp(`^${term}:`))).toBeVisible();
  }
  // Second-order terms render BESIDE the first-order ones — the Onglet-2 acceptance criterion.
  for (const term of ["Rho", "Vanna", "Volga"]) {
    await expect(legend.getByText(new RegExp(`^${term}:`))).toBeVisible();
  }
  // Charm is a display Greek, never an attribution term.
  await expect(legend.getByText(/^Charm:/)).toHaveCount(0);
  // The residual carries its own bar and a dollar unit string.
  await expect(legend.getByText(/^Residual:/)).toBeVisible();
  await expect(legend.getByText(/\$/).first()).toBeVisible();

  // No panel fell back to its error boundary and nothing crashed across the whole flow.
  await expect(page.getByText("failed to render", { exact: false })).toHaveCount(0);
  expect(pageErrors, pageErrors.join("\n")).toEqual([]);
});
