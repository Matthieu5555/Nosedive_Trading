import { render, screen, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";

// Plotly draws to a canvas jsdom does not implement; swap the wrapper for the DOM stub.
vi.mock("./Plot", async () => await import("../test/plotMock"));

import type { BasketRiskResponse } from "../api";
import { BASKET_RISK_AAA } from "../test/fixtures";
import { BasketRiskPanel } from "./BasketRiskPanel";

test("renders the book-additive totals with each unit string visible", () => {
  render(<BasketRiskPanel result={BASKET_RISK_AAA} />);
  const totals = screen.getByRole("table", { name: /book-additive sum/i });
  // The aggregate dollar values (scientific, 6 sig figs, trailing zeros stripped) and their
  // verbatim backend unit strings are both shown; the value cell carries no UNITS token because
  // the unit sits in its own adjacent column. 15.2 → "1.52 × 10¹".
  expect(within(totals).getByText("1.52 × 10¹")).toBeInTheDocument(); // gamma $
  expect(within(totals).getByText("$ per 1% move")).toBeInTheDocument();
  expect(within(totals).getByText("$ per 1 vol point")).toBeInTheDocument();
  expect(within(totals).getByText("$ per calendar day")).toBeInTheDocument();
});

test("renders the per-leg contribution breakdown (the proof the total is the sum)", () => {
  render(<BasketRiskPanel result={BASKET_RISK_AAA} />);
  const perLeg = screen.getByRole("table", { name: /per-leg contribution/i });
  // Both legs' signed Delta$ contributions appear (58.5 and -58.5 sum to the 0.0 total). These
  // cells stand alone (no adjacent unit column), so each carries its verbatim backend unit:
  // 58.5 → "5.85 × 10¹", with the leg metric's unit "$ per $1 of underlying".
  expect(within(perLeg).getByText("5.85 × 10¹ $ per $1 of underlying")).toBeInTheDocument();
  expect(within(perLeg).getByText("-5.85 × 10¹ $ per $1 of underlying")).toBeInTheDocument();
});

test("every panel self-labels and the per-leg Delta$ chart renders", () => {
  render(<BasketRiskPanel result={BASKET_RISK_AAA} />);
  expect(screen.getByLabelText(/Per-leg Delta\$ contribution/)).toBeInTheDocument();
});

test("with a non-$ currency every monetized unit renders in that currency's symbol", () => {
  render(<BasketRiskPanel result={BASKET_RISK_AAA} currency="€" />);
  const totals = screen.getByRole("table", { name: /book-additive sum/i });
  // The totals unit column re-currencies each backend "$"-unit: "$ per 1% move" -> "€ per 1% move".
  expect(within(totals).getByText("€ per 1% move")).toBeInTheDocument();
  expect(within(totals).getByText("€ per 1 vol point")).toBeInTheDocument();
  // The hard-coded price-row unit is re-currencied too: "$ (net leg value)" -> "€ (net leg value)".
  expect(within(totals).getByText("€ (net leg value)")).toBeInTheDocument();
  // The $-token must not leak through once a real currency is threaded.
  expect(within(totals).queryByText("$ per 1% move")).not.toBeInTheDocument();

  const perLeg = screen.getByRole("table", { name: /per-leg contribution/i });
  // Per-leg delta unit "$ per $1 of underlying" has TWO placeholders -> both substituted:
  // 58.5 -> "5.85 × 10¹", unit "€ per €1 of underlying".
  expect(within(perLeg).getByText("5.85 × 10¹ € per €1 of underlying")).toBeInTheDocument();
  expect(within(perLeg).getByText("-5.85 × 10¹ € per €1 of underlying")).toBeInTheDocument();
});

test("the default currency ($) leaves every unit string unchanged", () => {
  render(<BasketRiskPanel result={BASKET_RISK_AAA} />);
  const totals = screen.getByRole("table", { name: /book-additive sum/i });
  expect(within(totals).getByText("$ per 1% move")).toBeInTheDocument();
  expect(within(totals).getByText("$ (net leg value)")).toBeInTheDocument();
  const perLeg = screen.getByRole("table", { name: /per-leg contribution/i });
  expect(within(perLeg).getByText("5.85 × 10¹ $ per $1 of underlying")).toBeInTheDocument();
});

test("an unavailable Greek shows n/a, not a blank or a zero", () => {
  const withGap: BasketRiskResponse = {
    ...BASKET_RISK_AAA,
    metrics: { ...BASKET_RISK_AAA.metrics, theta: { dollar: null, unit: "$ per calendar day" } },
  };
  render(<BasketRiskPanel result={withGap} />);
  const totals = screen.getByRole("table", { name: /book-additive sum/i });
  expect(within(totals).getByText("n/a")).toBeInTheDocument();
});

test("labelled gaps render inline, never a blank panel", () => {
  const withGap: BasketRiskResponse = {
    ...BASKET_RISK_AAA,
    gaps: [
      { underlying: "AAA", tenor_label: "3m", delta_band: "10dp", reason: "no_analytics_row" },
    ],
    n_gaps: 1,
  };
  render(<BasketRiskPanel result={withGap} />);
  const gaps = screen.getByRole("alert", { name: /basket gaps/i });
  expect(within(gaps).getByText(/no_analytics_row/)).toBeInTheDocument();
});
