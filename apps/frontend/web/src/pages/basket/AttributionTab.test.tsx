import { render, screen, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";

vi.mock("../../components/Plot", async () => await import("../../test/plotMock"));

import type { AttributionResponse } from "../../api";
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

test("the attribution panel renders the Rho/Vanna/Volga terms beside Δ/Γ/Vega/Θ", () => {
  render(
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
  render(
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
