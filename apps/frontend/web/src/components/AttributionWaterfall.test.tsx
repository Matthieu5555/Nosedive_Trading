import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

// Plotly draws to a canvas jsdom does not implement; swap the wrapper for the DOM stub that
// exposes the trace type as text (so we can assert a `waterfall` trace was requested).
vi.mock("../components/Plot", async () => await import("../test/plotMock"));

import type { AttributionResponse } from "../api";
import { AttributionWaterfall } from "./AttributionWaterfall";

const TERM_UNIT = "$ (PnL contribution)";
const RESIDUAL_UNIT = "$ (residual vs full reprice)";

// A populated payload mirroring the BFF serializer shape. The independent oracle is these
// hand-chosen dollar terms: the panel must render one labelled bar per term + the residual,
// each with its dollar value and unit string. Per the owner ruling (2026-06-15) analytics
// dollars render in scientific notation at six sig figs, trailing zeros stripped, with the
// backend unit string shown verbatim alongside. Hand-derived from sci():
//   42000  → "4.2 × 10⁴"   (4.20000e+4 → mantissa 4.2,  exp 4)
//   -3500  → "-3.5 × 10³"  (-3.50000e+3 → mantissa -3.5, exp 3)
//   12500  → "1.25 × 10⁴"  (1.25000e+4 → mantissa 1.25, exp 4)
//   -1200  → "-1.2 × 10³"  (-1.20000e+3 → mantissa -1.2, exp 3)
//   450    → "4.5 × 10²"   (4.50000e+2 → mantissa 4.5,  exp 2)
const POPULATED: AttributionResponse = {
  trade_date: "2026-05-29",
  portfolio_id: "pf-attribution",
  level: "book",
  contract_key: "__book__",
  found: true,
  terms: [
    { name: "Delta", dollars: 42000, unit: TERM_UNIT },
    { name: "Gamma", dollars: -3500, unit: TERM_UNIT },
    { name: "Vega", dollars: 12500, unit: TERM_UNIT },
    { name: "Theta", dollars: -1200, unit: TERM_UNIT },
  ],
  residual: { dollars: 450, unit: RESIDUAL_UNIT },
  verdict: { within_tolerance: false, residual_abs_tol: 100, residual_rel_tol: 0.001 },
  approx_pnl: 49800,
  full_reprice_pnl: 50250,
};

const EMPTY: AttributionResponse = {
  trade_date: "2026-05-29",
  portfolio_id: "nope",
  level: "book",
  contract_key: "__book__",
  found: false,
  terms: [],
  residual: { dollars: null, unit: RESIDUAL_UNIT },
  verdict: null,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

test("a populated payload mounts the waterfall with one labelled bar per term + residual", () => {
  render(<AttributionWaterfall attribution={POPULATED} kicker="pf-attribution 2026-05-29" />);
  // The Plotly trace is a waterfall (ADR 0030).
  const plot = screen.getByLabelText(/P&L attribution waterfall/i);
  expect(within(plot).getByTestId("plot-types")).toHaveTextContent("waterfall");

  // One labelled entry per term, each with its dollar value (scientific notation) and unit string.
  const legend = screen.getByRole("list", { name: /attribution terms/i });
  expect(within(legend).getByText(/Delta:/)).toBeInTheDocument();
  expect(within(legend).getByText("4.2 × 10⁴")).toBeInTheDocument(); // Delta 42000
  expect(within(legend).getByText("-3.5 × 10³")).toBeInTheDocument(); // Gamma -3500
  expect(within(legend).getByText("1.25 × 10⁴")).toBeInTheDocument(); // Vega 12500
  expect(within(legend).getByText("-1.2 × 10³")).toBeInTheDocument(); // Theta -1200
  // Every term carries its dollar unit string (§5.1/§2.5).
  expect(within(legend).getAllByText(new RegExp(TERM_UNIT.replace(/[()$]/g, "\\$&"))).length).toBe(
    POPULATED.terms.length,
  );
});

test("the residual is its own labelled bar, never folded into a term", () => {
  render(<AttributionWaterfall attribution={POPULATED} kicker="pf-attribution 2026-05-29" />);
  const legend = screen.getByRole("list", { name: /attribution terms/i });
  // The residual is a distinct entry, dollar-labelled with its own residual unit string.
  expect(within(legend).getByText(/Residual:/)).toBeInTheDocument();
  expect(within(legend).getByText("4.5 × 10²")).toBeInTheDocument(); // residual 450
  expect(
    within(legend).getByText(new RegExp(RESIDUAL_UNIT.replace(/[()$]/g, "\\$&"))),
  ).toBeInTheDocument();
});

test("the tolerance verdict is surfaced (residual exceeds tolerance here)", () => {
  render(<AttributionWaterfall attribution={POPULATED} kicker="pf-attribution 2026-05-29" />);
  expect(screen.getByText(/residual exceeds tolerance/i)).toBeInTheDocument();
});

test("an empty payload renders a labelled empty state, not a waterfall", () => {
  render(<AttributionWaterfall attribution={EMPTY} kicker="nope 2026-05-29" />);
  expect(screen.getByText(/No P&L attribution for this selection/i)).toBeInTheDocument();
  // No waterfall plot is mounted in the empty case.
  expect(screen.queryByTestId("plot-types")).not.toBeInTheDocument();
});

// --- The fetch-error path goes through the Basket page's handler (AsyncBlock-style alert) ----
import { BasketPage } from "../pages/Basket";

test("a fetch error renders a labelled alert on the page, not a blank page", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve({
        ok: false,
        status: 400,
        statusText: "Bad Request",
        json: async () => ({ error: "bad_trade_date" }),
      } as Response),
    ),
  );
  render(<BasketPage />);
  await user.click(screen.getByRole("button", { name: /P&L attribution/i }));
  await waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent(/Failed to load attribution/i),
  );
});
