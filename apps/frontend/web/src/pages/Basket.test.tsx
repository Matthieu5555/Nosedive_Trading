import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

// Plotly draws to a canvas jsdom does not implement; swap the wrapper for the DOM stub.
vi.mock("../components/Plot", async () => await import("../test/plotMock"));

import { BasketPage } from "./Basket";
import { BASKET_RISK_AAA } from "../test/fixtures";
import { mockFetch } from "../test/http";

afterEach(() => {
  vi.unstubAllGlobals();
});

test("the strangle template pre-fills the ±30Δ wing legs in the grid", async () => {
  const user = userEvent.setup();
  render(<BasketPage />);
  await user.click(screen.getByRole("button", { name: /template strangle/i }));
  const legs = screen.getByRole("table", { name: /composed legs/i });
  // The strangle template fills the +30Δ call + −30Δ put legs.
  expect(within(legs).getByText("30dc")).toBeInTheDocument();
  expect(within(legs).getByText("30dp")).toBeInTheDocument();
});

test("the straddle template pre-fills the two ATM legs (atm call + atmp put)", async () => {
  const user = userEvent.setup();
  render(<BasketPage />);
  await user.click(screen.getByRole("button", { name: /template straddle/i }));
  const legs = screen.getByRole("table", { name: /composed legs/i });
  expect(within(legs).getByText("atm")).toBeInTheDocument();
  expect(within(legs).getByText("atmp")).toBeInTheDocument();
  // Not the ±30Δ wings — that would be the strangle.
  expect(within(legs).queryByText("30dc")).not.toBeInTheDocument();
});

test("pricing a composed basket renders the totals with unit strings visible", async () => {
  const user = userEvent.setup();
  mockFetch(BASKET_RISK_AAA);
  render(<BasketPage />);
  await user.click(screen.getByRole("button", { name: /template straddle/i }));
  await user.click(screen.getByRole("button", { name: /price basket/i }));
  await waitFor(() =>
    expect(screen.getByRole("table", { name: /book-additive sum/i })).toBeInTheDocument(),
  );
  const totals = screen.getByRole("table", { name: /book-additive sum/i });
  expect(within(totals).getByText("15.2000")).toBeInTheDocument(); // gamma $
  expect(within(totals).getByText("$ per 1% move")).toBeInTheDocument();
});

test("a pricing error renders a labelled alert, not a blank panel", async () => {
  const user = userEvent.setup();
  mockFetch({ error: "bad_basket", detail: "boom" }, false);
  render(<BasketPage />);
  await user.click(screen.getByRole("button", { name: /template strangle/i }));
  await user.click(screen.getByRole("button", { name: /price basket/i }));
  await waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent(/Failed to price basket/i),
  );
});

const BASKET_STRESS_AAA = {
  basket_id: "basket-AAA-latest",
  trade_date: "2026-06-05",
  underlying: "AAA",
  surface: {
    spot_shock: [-0.5, 0.0, 0.5],
    vol_shock: [-0.5, 0.0, 0.5],
    scenario_pnl: [
      [-2000, -1500, -900],
      [0, 0, 0],
      [800, 1200, 1700],
    ],
    scenario_version: "scn-1.0.0+abc+def",
    unit: "$ (full-reprice PnL)",
    n_cells: 9,
    has_holes: false,
    n_holes: 0,
  },
  worst_case: { spot_shock: -0.5, vol_shock: -0.5, pnl: -2000, unit: "$ (full-reprice PnL)" },
  n_legs: 2,
  n_resolved: 2,
  gaps: [],
  n_gaps: 0,
};

test("stressing a composed basket renders the worst case and the PnL surface", async () => {
  const user = userEvent.setup();
  mockFetch(BASKET_STRESS_AAA);
  render(<BasketPage />);
  await user.click(screen.getByRole("button", { name: /template straddle/i }));
  await user.click(screen.getByRole("button", { name: /stress basket/i }));
  await waitFor(() => expect(screen.getByText("Worst case")).toBeInTheDocument());
  // The worst-case PnL (independently the -50%/-50% cell) is shown as signed money — it appears
  // both in the worst-case panel and as the surface's max loss (same cell).
  expect(screen.getAllByText("-$2,000").length).toBeGreaterThanOrEqual(1);
  expect(screen.getByText("2/2 legs repriced")).toBeInTheDocument();
  // The surface and heatmap render as Plotly traces.
  const surface = await screen.findByLabelText(/Stress PnL surface/i);
  expect(within(surface).getByTestId("plot-types")).toHaveTextContent("surface");
  const heatmap = await screen.findByLabelText(/Stress PnL heatmap/i);
  expect(within(heatmap).getByTestId("plot-types")).toHaveTextContent("heatmap");
});

test("a stress error renders a labelled alert", async () => {
  const user = userEvent.setup();
  mockFetch({ error: "bad_basket", detail: "boom" }, false);
  render(<BasketPage />);
  await user.click(screen.getByRole("button", { name: /template strangle/i }));
  await user.click(screen.getByRole("button", { name: /stress basket/i }));
  await waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent(/Failed to stress basket/i),
  );
});
