import { render, screen, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";

// Plotly draws to a canvas jsdom does not implement; swap the wrapper for the DOM stub.
vi.mock("./Plot", async () => await import("../test/plotMock"));

import { BasketRiskPanel } from "./BasketRiskPanel";
import { BASKET_RISK_AAA } from "../test/fixtures";
import type { BasketRiskResponse } from "../api";

test("renders the book-additive totals with each unit string visible", () => {
  render(<BasketRiskPanel result={BASKET_RISK_AAA} />);
  const totals = screen.getByRole("table", { name: /book-additive sum/i });
  // The aggregate dollar values and their unit strings are both shown.
  expect(within(totals).getByText("15.2000")).toBeInTheDocument(); // gamma $
  expect(within(totals).getByText("$ per 1% move")).toBeInTheDocument();
  expect(within(totals).getByText("$ per 1 vol point")).toBeInTheDocument();
  expect(within(totals).getByText("$ per calendar day")).toBeInTheDocument();
});

test("renders the per-leg contribution breakdown (the proof the total is the sum)", () => {
  render(<BasketRiskPanel result={BASKET_RISK_AAA} />);
  const perLeg = screen.getByRole("table", { name: /per-leg contribution/i });
  // Both legs' signed Delta$ contributions appear (58.5 and -58.5 sum to the 0.0 total).
  expect(within(perLeg).getByText("58.5000")).toBeInTheDocument();
  expect(within(perLeg).getByText("-58.5000")).toBeInTheDocument();
});

test("every panel self-labels and the per-leg Delta$ chart renders", () => {
  render(<BasketRiskPanel result={BASKET_RISK_AAA} />);
  expect(screen.getByLabelText(/Per-leg Delta\$ contribution/)).toBeInTheDocument();
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
    gaps: [{ underlying: "AAA", tenor_label: "3m", delta_band: "10dp", reason: "no_analytics_row" }],
    n_gaps: 1,
  };
  render(<BasketRiskPanel result={withGap} />);
  const gaps = screen.getByRole("alert", { name: /basket gaps/i });
  expect(within(gaps).getByText(/no_analytics_row/)).toBeInTheDocument();
});
