import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, test, vi } from "vitest";

// Plotly draws to a canvas jsdom does not implement; swap the wrapper for the DOM stub.
vi.mock("../components/Plot", async () => await import("../test/plotMock"));

import { BasketPage } from "./Basket";
import type { OrderTicketResponse } from "../api";
import { BASKET_RISK_AAA } from "../test/fixtures";
import { jsonPost, server } from "../test/server";

// A malformed-basket rejection, as the BFF serves it: a 400 whose typed `detail` names the
// problem — the UI must surface that detail, never a bare status line.
function badBasket(path: string) {
  return http.post(path, () =>
    HttpResponse.json({ error: "bad_basket", detail: "boom" }, { status: 400 }),
  );
}

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
  server.use(jsonPost("/api/basket/risk", BASKET_RISK_AAA));
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

test("a pricing error renders a labelled alert carrying the BFF's typed detail", async () => {
  const user = userEvent.setup();
  server.use(badBasket("/api/basket/risk"));
  render(<BasketPage />);
  await user.click(screen.getByRole("button", { name: /template strangle/i }));
  await user.click(screen.getByRole("button", { name: /price basket/i }));
  await waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent(/Failed to price basket/i),
  );
  // The 400's labelled detail reaches the operator — not a bare "400 Bad Request".
  expect(screen.getByRole("alert")).toHaveTextContent(/boom/);
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
  server.use(jsonPost("/api/basket/scenarios", BASKET_STRESS_AAA));
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

test("a stress error renders a labelled alert carrying the BFF's typed detail", async () => {
  const user = userEvent.setup();
  server.use(badBasket("/api/basket/scenarios"));
  render(<BasketPage />);
  await user.click(screen.getByRole("button", { name: /template strangle/i }));
  await user.click(screen.getByRole("button", { name: /stress basket/i }));
  await waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent(/Failed to stress basket/i),
  );
  expect(screen.getByRole("alert")).toHaveTextContent(/boom/);
});

// The booking chain's single home (frontend-orders-booking-reconcile, ruling (b)): the Basket
// page composes legs, then builds the real, store-backed ticket from POST /api/ticket/preview.
// There is no separate Orders sketch — these stand in for "exactly one real booking surface".
//
// What the BFF returns for the previewed ticket; the long->BUY / short->SELL mapping is pinned in
// the Python unit tests, so this fixture is the BFF's authority, not a re-derivation. It comes
// back gated (transmit:false) so the send affordance must stay disabled.
const TICKET: OrderTicketResponse = {
  source_basket_id: "basket-AAA-latest",
  trade_date: "",
  underlying: "AAA",
  target_broker: "ibkr",
  time_in_force: "day",
  mode: "paper",
  legs: [
    { instrument_kind: "option", underlying: "AAA", side: "buy", quantity: 1,
      price_spec: { kind: "market" }, tenor_label: "1m", delta_band: "atm" },
    { instrument_kind: "option", underlying: "AAA", side: "buy", quantity: 1,
      price_spec: { kind: "market" }, tenor_label: "1m", delta_band: "atmp" },
  ],
  n_legs: 2,
  gated: { transmit: false, reason: "sign-and-send is behind an explicit owner gate" },
};

test("the single booking home builds the real ticket and self-labels it as preview-only", async () => {
  const user = userEvent.setup();
  server.use(jsonPost("/api/ticket/preview", TICKET));
  render(<BasketPage />);

  // Composing legs reveals the real ticket panel (it is gated on legs.length > 0).
  await user.click(screen.getByRole("button", { name: /template straddle/i }));
  const ticketPanel = screen.getByRole("region", { name: /order ticket/i });
  // Self-labels: the real Execution ticket, preview-only — not an "indicative sketch".
  expect(within(ticketPanel).getByRole("heading", { name: /order ticket/i })).toBeInTheDocument();
  expect(within(ticketPanel).getByText(/preview only/i)).toBeInTheDocument();

  await user.click(within(ticketPanel).getByRole("button", { name: "Build ticket" }));
  const legsTable = await within(ticketPanel).findByRole("table", { name: /order ticket legs/i });
  expect(within(legsTable).getAllByText("BUY").length).toBeGreaterThanOrEqual(1);
});

test("the booking home's send affordance is disabled and 3B-gated; nothing can transmit", async () => {
  const user = userEvent.setup();
  server.use(jsonPost("/api/ticket/preview", TICKET));
  render(<BasketPage />);

  await user.click(screen.getByRole("button", { name: /template straddle/i }));
  await user.click(screen.getByRole("button", { name: "Build ticket" }));
  await screen.findByRole("table", { name: /order ticket legs/i });

  const send = screen.getByRole("button", { name: /sign and send order/i });
  expect(send).toBeDisabled();
  expect(screen.getByText(/3B — gated/)).toBeInTheDocument();
});

test("the retired Orders sketch renders nowhere — no hardcoded strike 5350, no Submit (sketch)", async () => {
  const user = userEvent.setup();
  server.use(jsonPost("/api/ticket/preview", TICKET));
  render(<BasketPage />);

  await user.click(screen.getByRole("button", { name: /template straddle/i }));
  await user.click(screen.getByRole("button", { name: "Build ticket" }));
  await screen.findByRole("table", { name: /order ticket legs/i });

  // The dead sketch's tells must not appear anywhere on the booking home.
  expect(screen.queryByText("5350")).not.toBeInTheDocument();
  expect(screen.queryByText(/Submit \(sketch/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/Execution sketch — read-only/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/Indicative only/i)).not.toBeInTheDocument();
});
