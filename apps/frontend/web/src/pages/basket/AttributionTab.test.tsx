import { screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

vi.mock("../../components/Plot", async () => await import("../../test/plotMock"));

import type { AttributionResponse } from "../../api";
import { renderWithClient } from "../../test/renderWithClient";
import { AttributionTab } from "./AttributionTab";

// The ④ Attribution panel renders every by-Greek term the BFF sends — including the second-order
// Rho/Vanna/Volga terms that now flow through the generic AttributionTerm (B2). Charm is a display
// Greek, NOT an attribution term, so it is correctly absent here.
const ATTRIBUTION_SECOND_ORDER: AttributionResponse = {
  found: true,
  trade_date: "2026-06-05",
  portfolio_id: "demo-book",
  level: "book",
  contract_key: "__book__",
  terms: [
    { name: "Delta", dollars: 1200, unit: "$" },
    { name: "Gamma", dollars: -300, unit: "$" },
    { name: "Vega", dollars: 450, unit: "$" },
    { name: "Theta", dollars: -150, unit: "$" },
    { name: "Rho", dollars: 60, unit: "$" },
    { name: "Vanna", dollars: 25, unit: "$" },
    { name: "Volga", dollars: -18, unit: "$" },
  ],
  residual: { dollars: 20, unit: "$" },
  verdict: { within_tolerance: true, residual_abs_tol: 50, residual_rel_tol: 0.01 },
};

const NOOP = () => {};

// The tab now also fires useRealizedAttribution on mount; stub fetch to a labelled-empty realized
// payload so that query resolves to its honest empty state and never touches the network. These
// tests still assert on the scenario-attribution panel driven by the `attribution` prop.
afterEach(() => {
  vi.unstubAllGlobals();
});

function stubEmptyRealized() {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve({
        ok: true,
        status: 200,
        statusText: "OK",
        json: async () => ({
          found: false,
          underlying: "SX5E",
          expiry: "2026-09-18",
          portfolio_id: "demo",
          term_unit: "$",
          residual_unit: "$",
          contracts: [],
          dates: [],
          steps: [],
        }),
      } as Response),
    ),
  );
}

test("the attribution panel renders the Rho/Vanna/Volga terms beside Δ/Γ/Vega/Θ", () => {
  stubEmptyRealized();
  renderWithClient(
    <AttributionTab
      portfolioId="demo-book"
      onPortfolioId={NOOP}
      tradeDate="2026-06-05"
      loading={false}
      error={null}
      attribution={ATTRIBUTION_SECOND_ORDER}
      onLoad={NOOP}
    />,
  );

  // The by-Greek bars live on the waterfall itself (no legend re-printing them beside the chart).
  const plot = screen.getByLabelText(/P&L attribution waterfall/i);
  const bars = within(plot).getByTestId("plot-x");
  for (const term of ["Delta", "Gamma", "Vega", "Theta", "Rho", "Vanna", "Volga", "Residual"]) {
    expect(bars).toHaveTextContent(term);
  }
  // Charm is a display Greek, never an attribution term — it must not appear as a bar.
  expect(bars).not.toHaveTextContent("Charm");
});

test("each second-order term carries its own unit label", () => {
  stubEmptyRealized();
  renderWithClient(
    <AttributionTab
      portfolioId="demo-book"
      onPortfolioId={NOOP}
      tradeDate="2026-06-05"
      loading={false}
      error={null}
      attribution={ATTRIBUTION_SECOND_ORDER}
      onLoad={NOOP}
    />,
  );

  // Each bar carries its own value + unit text label on the chart. Vanna's dollar contribution
  // renders via the sig-fig formatter, with its $ unit.
  const plot = screen.getByLabelText(/P&L attribution waterfall/i);
  const labels = within(plot).getByTestId("plot-text");
  expect(labels).toHaveTextContent("2.5 × 10¹");
  expect(labels).toHaveTextContent("$");
});
