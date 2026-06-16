import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, test, vi } from "vitest";

vi.mock("../components/Plot", async () => await import("../test/plotMock"));

import type { OrderTicketResponse } from "../api";
import { BASKET_RISK_AAA } from "../test/fixtures";
import { jsonPost, server } from "../test/server";
import { BasketPage } from "./Basket";

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

  expect(within(totals).getByText("1.52 × 10¹")).toBeInTheDocument();
  expect(within(totals).getByText("$ per 1% move")).toBeInTheDocument();
});

test("the leg band selector is wired to the platform band axis (>8 options)", async () => {
  render(<BasketPage />);

  const bandSelect = await screen.findByLabelText("leg band");
  await waitFor(() => expect(within(bandSelect).getAllByRole("option").length).toBeGreaterThan(8));

  expect(within(bandSelect).getByRole("option", { name: "02dp" })).toBeInTheDocument();
  expect(within(bandSelect).getByRole("option", { name: "02dc" })).toBeInTheDocument();
});

test("selecting the EUR-quoted index renders monetized values in € (not $)", async () => {
  const user = userEvent.setup();
  server.use(jsonPost("/api/basket/risk", BASKET_RISK_AAA));
  render(<BasketPage />);

  const underlying = await screen.findByLabelText("underlying");
  await user.selectOptions(underlying, "SX5E");
  await user.click(screen.getByRole("button", { name: /template straddle/i }));
  await user.click(screen.getByRole("button", { name: /price basket/i }));
  await waitFor(() =>
    expect(screen.getByRole("table", { name: /book-additive sum/i })).toBeInTheDocument(),
  );
  const totals = screen.getByRole("table", { name: /book-additive sum/i });

  expect(within(totals).getByText("€ per 1% move")).toBeInTheDocument();
  expect(within(totals).queryByText("$ per 1% move")).not.toBeInTheDocument();
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

  expect(screen.getAllByText("-2 × 10³ $ (full-reprice PnL)").length).toBeGreaterThanOrEqual(1);

  expect(screen.getAllByText("-5 × 10⁻¹ (frac)").length).toBe(2);
  expect(screen.getByText("2/2 legs repriced")).toBeInTheDocument();

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

const TICKET: OrderTicketResponse = {
  source_basket_id: "basket-AAA-latest",
  trade_date: "",
  underlying: "AAA",
  target_broker: "ibkr",
  time_in_force: "day",
  mode: "paper",
  legs: [
    {
      instrument_kind: "option",
      underlying: "AAA",
      side: "buy",
      quantity: 1,
      price_spec: { kind: "market" },
      tenor_label: "1m",
      delta_band: "atm",
    },
    {
      instrument_kind: "option",
      underlying: "AAA",
      side: "buy",
      quantity: 1,
      price_spec: { kind: "market" },
      tenor_label: "1m",
      delta_band: "atmp",
    },
  ],
  n_legs: 2,
  gated: { transmit: false, reason: "sign-and-send is behind an explicit owner gate" },
};

test("the single booking home builds the real ticket and self-labels it as preview-only", async () => {
  const user = userEvent.setup();
  server.use(jsonPost("/api/ticket/preview", TICKET));
  render(<BasketPage />);

  await user.click(screen.getByRole("button", { name: /template straddle/i }));
  const ticketPanel = screen.getByRole("region", { name: /order ticket/i });

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

  expect(screen.queryByText("5350")).not.toBeInTheDocument();
  expect(screen.queryByText(/Submit \(sketch/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/Execution sketch — read-only/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/Indicative only/i)).not.toBeInTheDocument();
});
