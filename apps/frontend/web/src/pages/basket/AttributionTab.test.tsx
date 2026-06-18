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

  const legend = screen.getByRole("list", { name: /attribution terms/i });
  for (const term of ["Delta", "Gamma", "Vega", "Theta", "Rho", "Vanna", "Volga"]) {
    expect(within(legend).getByText(new RegExp(`^${term}:`))).toBeInTheDocument();
  }
  // Charm is a display Greek, never an attribution term — it must not appear here.
  expect(within(legend).queryByText(/^Charm:/)).not.toBeInTheDocument();
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

  const legend = screen.getByRole("list", { name: /attribution terms/i });
  // Vanna's dollar contribution rendered via the sig-fig formatter, with its $ unit.
  const vanna = within(legend)
    .getByText(/^Vanna:/)
    .closest("li");
  expect(vanna).not.toBeNull();
  expect(within(vanna as HTMLElement).getByText("2.5 × 10¹")).toBeInTheDocument();
  expect(vanna?.textContent).toContain("$");
});
