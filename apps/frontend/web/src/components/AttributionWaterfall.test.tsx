import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

vi.mock("../components/Plot", async () => await import("../test/plotMock"));

import type { AttributionResponse } from "../api";
import { AttributionWaterfall } from "./AttributionWaterfall";

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

test("a populated payload mounts the waterfall with one labelled bar per term + residual", () => {
  render(<AttributionWaterfall attribution={POPULATED} kicker="pf-attribution 2026-05-29" />);

  const plot = screen.getByLabelText(/P&L attribution waterfall/i);
  expect(within(plot).getByTestId("plot-types")).toHaveTextContent("waterfall");

  const legend = screen.getByRole("list", { name: /attribution terms/i });
  expect(within(legend).getByText(/Delta:/)).toBeInTheDocument();
  expect(within(legend).getByText("4.2 × 10⁴")).toBeInTheDocument();
  expect(within(legend).getByText("-3.5 × 10³")).toBeInTheDocument();
  expect(within(legend).getByText("1.25 × 10⁴")).toBeInTheDocument();
  expect(within(legend).getByText("-1.2 × 10³")).toBeInTheDocument();

  expect(within(legend).getAllByText(new RegExp(TERM_UNIT.replace(/[()$]/g, "\\$&"))).length).toBe(
    POPULATED.terms.length,
  );
});

test("the residual is its own labelled bar, never folded into a term", () => {
  render(<AttributionWaterfall attribution={POPULATED} kicker="pf-attribution 2026-05-29" />);
  const legend = screen.getByRole("list", { name: /attribution terms/i });

  expect(within(legend).getByText(/Residual:/)).toBeInTheDocument();
  expect(within(legend).getByText("4.5 × 10²")).toBeInTheDocument();
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

  expect(screen.queryByTestId("plot-types")).not.toBeInTheDocument();
});

test("standalone (default): renders its own 'P&L attribution' heading + kicker", () => {
  render(<AttributionWaterfall attribution={POPULATED} kicker="pf-attribution 2026-05-29" />);
  expect(screen.getByRole("heading", { name: /P&L attribution/i })).toBeInTheDocument();
  expect(screen.getByText("pf-attribution 2026-05-29")).toBeInTheDocument();
});

test("embedded: suppresses the inner heading + kicker so a titled card never doubles the title", () => {
  // RiskScenarios / CombinedBookView drop this inside an already-titled card. `embedded` removes the
  // panel's own <h2> + kicker; the chart, legend, and verdict still render.
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
  await user.click(screen.getByRole("tab", { name: /attribution/i }));
  await user.click(screen.getByRole("button", { name: /P&L attribution/i }));
  // With every fetch stubbed to fail, the page now surfaces each failure as its own alert (the
  // index list and delta-band axis included — no silent swallowing). Target the attribution alert
  // specifically rather than assuming it is the only one on the page.
  await waitFor(() => expect(screen.getByText(/Failed to load attribution/i)).toBeInTheDocument());
});
