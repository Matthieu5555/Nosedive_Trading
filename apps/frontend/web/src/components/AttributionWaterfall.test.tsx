import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

vi.mock("../components/Plot", async () => await import("../test/plotMock"));

import type { AttributionResponse, RealizedAttributionResponse } from "../api";
import { renderWithClient } from "../test/renderWithClient";
import { AttributionWaterfall, RealizedAttributionWaterfall } from "./AttributionWaterfall";

const TERM_UNIT = "$ (PnL contribution)";
const RESIDUAL_UNIT = "$ (residual vs full reprice)";

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

test("a populated payload mounts the waterfall, one bar per term plus the residual as its own bar", () => {
  render(<AttributionWaterfall attribution={POPULATED} kicker="pf-attribution 2026-05-29" />);

  const plot = screen.getByLabelText(/P&L attribution waterfall/i);
  expect(within(plot).getByTestId("plot-types")).toHaveTextContent("waterfall");
  // One bar per Greek term, plus the residual as its own bar (never folded into a term). The bar
  // values + units live on the chart itself, so there is no separate legend duplicating them.
  expect(within(plot).getByTestId("plot-points")).toHaveTextContent(
    String(POPULATED.terms.length + 1),
  );
});

test("no legend duplicates the chart: the by-Greek numbers are not re-printed as a list", () => {
  render(<AttributionWaterfall attribution={POPULATED} kicker="pf-attribution 2026-05-29" />);
  expect(screen.queryByRole("list", { name: /attribution terms/i })).not.toBeInTheDocument();
});

test("the tolerance verdict is surfaced (residual exceeds tolerance here)", () => {
  render(<AttributionWaterfall attribution={POPULATED} kicker="pf-attribution 2026-05-29" />);
  expect(screen.getByText(/residual exceeds tolerance/i)).toBeInTheDocument();
});

test("an empty payload renders a labelled empty state, not a waterfall", () => {
  render(<AttributionWaterfall attribution={EMPTY} kicker="nope 2026-05-29" />);
  expect(screen.getByText(/No P&L attribution for this selection/i)).toBeInTheDocument();

  expect(screen.queryByTestId("plot-types")).not.toBeInTheDocument();
});

test("standalone (default): renders its own 'P&L attribution' heading + kicker", () => {
  render(<AttributionWaterfall attribution={POPULATED} kicker="pf-attribution 2026-05-29" />);
  expect(screen.getByRole("heading", { name: /P&L attribution/i })).toBeInTheDocument();
  expect(screen.getByText("pf-attribution 2026-05-29")).toBeInTheDocument();
});

test("embedded: suppresses the inner heading + kicker so a titled card never doubles the title", () => {
  // RiskScenarios / CombinedBookView drop this inside an already-titled card. `embedded` removes the
  // panel's own <h2> + kicker; the chart and verdict still render.
  render(
    <AttributionWaterfall attribution={POPULATED} kicker="pf-attribution 2026-05-29" embedded />,
  );
  expect(screen.queryByRole("heading", { name: /P&L attribution/i })).not.toBeInTheDocument();
  expect(screen.queryByText("pf-attribution 2026-05-29")).not.toBeInTheDocument();
  // The substance is intact.
  expect(screen.getByLabelText(/P&L attribution waterfall/i)).toBeInTheDocument();
  expect(screen.getByText(/residual exceeds tolerance/i)).toBeInTheDocument();
});

test("embedded empty state also drops the inner heading, keeps the empty message", () => {
  render(<AttributionWaterfall attribution={EMPTY} kicker="nope 2026-05-29" embedded />);
  expect(screen.queryByRole("heading", { name: /P&L attribution/i })).not.toBeInTheDocument();
  expect(screen.getByText(/No P&L attribution for this selection/i)).toBeInTheDocument();
});

const TERM_UNIT_PNL = "$ (PnL contribution)";
const RESIDUAL_UNIT_REPRICE = "$ (residual vs full reprice)";

function realizedStep(start: string, end: string, dSpot: number) {
  return {
    start_date: start,
    end_date: end,
    portfolio_id: "demo-sep-straddle",
    terms: [
      { name: "Delta", dollars: 54.96, unit: TERM_UNIT_PNL },
      { name: "Gamma", dollars: 95.15, unit: TERM_UNIT_PNL },
      { name: "Vega", dollars: 287.65, unit: TERM_UNIT_PNL },
      { name: "Theta", dollars: -210.0, unit: TERM_UNIT_PNL },
      { name: "Rho", dollars: 0.0, unit: TERM_UNIT_PNL },
      { name: "Vanna", dollars: 1.18, unit: TERM_UNIT_PNL },
      { name: "Volga", dollars: -0.001, unit: TERM_UNIT_PNL },
    ],
    approx_pnl: { dollars: 228.94, unit: TERM_UNIT_PNL },
    full_reprice_pnl: { dollars: 225.56, unit: RESIDUAL_UNIT_REPRICE },
    residual: { dollars: -3.38, unit: RESIDUAL_UNIT_REPRICE },
    verdict: {
      within_tolerance: true,
      diagnostic: "",
      residual_abs_tol: 1.0,
      residual_rel_tol: 0.05,
    },
    move: { d_spot: dSpot, d_vol: 0.0011, d_time: 0.00274, d_rate: 0.0 },
  };
}

const REALIZED: RealizedAttributionResponse = {
  found: true,
  underlying: "SX5E",
  expiry: "2026-09-18",
  portfolio_id: "demo-sep-straddle",
  term_unit: TERM_UNIT_PNL,
  residual_unit: RESIDUAL_UNIT_REPRICE,
  contracts: ["SX5E|2026-09-18|6275|C", "SX5E|2026-09-18|6275|P"],
  dates: ["2026-06-15", "2026-06-16", "2026-06-17"],
  steps: [
    realizedStep("2026-06-15", "2026-06-16", 34.5),
    realizedStep("2026-06-16", "2026-06-17", -12.0),
  ],
};

test("realized: one day card per step, each a waterfall of the seven Greeks + a residual bar", () => {
  render(<RealizedAttributionWaterfall realized={REALIZED} />);

  // One waterfall plot per held day.
  const plots = screen.getAllByLabelText(/Realized P&L attribution/i);
  expect(plots).toHaveLength(REALIZED.steps.length);

  // Each day plots the seven Greek bars plus the residual as its own bar (never folded into a
  // term): eight bars, with no legend re-printing the same numbers beside the chart.
  for (const plot of plots) {
    expect(within(plot).getByTestId("plot-points")).toHaveTextContent("8");
  }
  expect(screen.queryByRole("list", { name: /attribution terms/i })).not.toBeInTheDocument();
});

test("realized: the per-day move drives a plain-language sentence (direction from d_spot sign)", () => {
  render(<RealizedAttributionWaterfall realized={REALIZED} />);
  // Day 1 spot rose, day 2 spot fell — both surfaced in plain language, no quant jargon.
  expect(screen.getByText(/the underlying rose/i)).toBeInTheDocument();
  expect(screen.getByText(/the underlying fell/i)).toBeInTheDocument();
});

test("realized: an empty payload renders an honest empty state, not a waterfall", () => {
  render(
    <RealizedAttributionWaterfall
      realized={{ ...REALIZED, found: false, steps: [], contracts: [], dates: [] }}
    />,
  );
  expect(screen.getByText(/No realized attribution for this position yet/i)).toBeInTheDocument();
  expect(screen.queryByTestId("plot-types")).not.toBeInTheDocument();
});

import { BuildBasket } from "../pages/simulate/BuildBasket";

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
  // BuildBasket's ③ Attribution tab now fires a react-query hook (the realized waterfall), so the
  // page needs a QueryClient in scope; the stubbed-failing fetch still drives the assertion.
  renderWithClient(<BuildBasket />);
  await user.click(screen.getByRole("tab", { name: /attribution/i }));
  await user.click(screen.getByRole("button", { name: /P&L attribution/i }));
  // With every fetch stubbed to fail, the page now surfaces each failure as its own alert (the
  // index list and delta-band axis included — no silent swallowing). Target the attribution alert
  // specifically rather than assuming it is the only one on the page.
  await waitFor(() => expect(screen.getByText(/Failed to load attribution/i)).toBeInTheDocument());
});
