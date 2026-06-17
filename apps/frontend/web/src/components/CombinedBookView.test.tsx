import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";

vi.mock("./Plot", async () => await import("../test/plotMock"));

import type { AttributionResponse, ComposeGreeks, ComposeResponse } from "../api";
import { jsonGet, server } from "../test/server";
import { CombinedBookView } from "./CombinedBookView";

function greeks(
  level: string,
  label: string,
  index: number,
  delta: number | null,
): ComposeGreeks {
  return {
    level,
    layer_label: label,
    layer_index: index,
    net_delta: delta ?? 0,
    net_gamma: 0,
    net_vega: 0,
    net_theta: 0,
    dollar_delta: { value: delta, unit: "$ per 1% move" },
    dollar_gamma: { value: 0.5, unit: "$ per 1% move²" },
    dollar_vega: { value: 12, unit: "$ per vol pt" },
    dollar_theta: { value: -3, unit: "$ per day" },
    dollar_rho: { value: 7, unit: "$ per bp" },
  };
}

const BOOK: ComposeResponse = {
  book_id: "book-SX5E-latest",
  valuation_ts: "2026-06-17T00:00:00Z",
  composition_version: "compose-1.0.0",
  config_hashes: { layer_set: "abc", grid: "def", monetization: "ghi" },
  combined: greeks("book", "Combined", 0, 1500),
  layers: [
    { ...greeks("layer", "S1 dispersion", 0, 1000), n_legs: 2, n_resolved: 2 },
    { ...greeks("layer", "S2 put line", 1, 500), n_legs: 1, n_resolved: 1 },
  ],
  diversification_ratio: 0.82,
  surface: {
    scenario_version: "scn-1.0.0",
    spot_axis: [-0.5, 0.0, 0.5],
    vol_axis: [-0.5, 0.0, 0.5],
    pnl_grid: [
      [-2000, -1500, -900],
      [0, 0, 0],
      [800, 1200, 1700],
    ],
  },
};

test("the combined Greeks table renders the combined book row plus each layer with their dollar unit strings", () => {
  render(<CombinedBookView book={BOOK} currency="$" />);

  const table = screen.getByRole("table", { name: /book-additive sum/i });
  const combinedRow = within(table).getByRole("row", { name: /combined book Greeks/i });
  // The combined dollar-delta, rendered from its unit string (never re-derived).
  expect(within(combinedRow).getByText("1.5 × 10³ $ per 1% move")).toBeInTheDocument();

  const s1 = within(table).getByRole("row", { name: /layer S1 dispersion/i });
  expect(within(s1).getByText("1 × 10³ $ per 1% move")).toBeInTheDocument();
  expect(within(s1).getByText("2/2")).toBeInTheDocument();
});

test("the diversification ratio is shown as a read-only diagnostic", () => {
  render(<CombinedBookView book={BOOK} />);
  expect(screen.getByText(/Diversification ratio/i)).toBeInTheDocument();
  expect(screen.getByText("8.2 × 10⁻¹")).toBeInTheDocument();
});

test("the combined PnL surface mounts as a Plotly surface trace from the BFF payload", () => {
  render(<CombinedBookView book={BOOK} />);
  const surface = screen.getByLabelText(/Combined stressed PnL surface/i);
  expect(within(surface).getByTestId("plot-types")).toHaveTextContent("surface");
});

test("an empty surface renders a labelled empty state, not a broken Plotly mount", () => {
  const empty: ComposeResponse = {
    ...BOOK,
    surface: { scenario_version: null, spot_axis: [], vol_axis: [], pnl_grid: [] },
  };
  render(<CombinedBookView book={empty} />);
  expect(screen.getByText(/No combined PnL surface/i)).toBeInTheDocument();
});

test("drilling a layer fetches and renders its 2C attribution waterfall", async () => {
  const user = userEvent.setup();
  const attribution: AttributionResponse = {
    found: true,
    trade_date: "2026-06-05",
    portfolio_id: "S1 dispersion",
    level: "book",
    contract_key: "__book__",
    terms: [
      { name: "Delta", dollars: 800, unit: "$" },
      { name: "Vega", dollars: 200, unit: "$" },
    ],
    residual: { dollars: 5, unit: "$" },
    verdict: { within_tolerance: true, residual_abs_tol: 50, residual_rel_tol: 0.01 },
  };
  server.use(jsonGet("/api/attribution", attribution));

  render(<CombinedBookView book={BOOK} />);
  await user.click(screen.getByRole("button", { name: /attribution for S1 dispersion/i }));

  await waitFor(() =>
    expect(screen.getByLabelText(/P&L attribution waterfall/i)).toBeInTheDocument(),
  );
});
