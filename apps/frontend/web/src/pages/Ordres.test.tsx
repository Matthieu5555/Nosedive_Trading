import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";

vi.mock("../components/Plot", async () => await import("../test/plotMock"));

import type { BacktestResult, OrderTicketResponse } from "../api";
import { renderWithClient } from "../test/renderWithClient";
import { jsonPost, server } from "../test/server";
import { OrdresPage } from "./Ordres";

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

const RESULT: BacktestResult = {
  strategy_id: "bt-SX5E-put-line",
  summary: {
    total_pnl: 125000,
    total_net_pnl: 118500,
    total_transaction_cost: 6500,
    max_drawdown: -42000,
    sharpe: 1.37,
    turnover: 0.85,
    worst_stress_loss: -310000,
  },
  cumulative_attribution: {
    delta: -8000,
    gamma: -55000,
    vega: -22000,
    theta: 215000,
    rho: 1500,
    vanna: -3000,
    volga: -1000,
  },
  days: [
    {
      as_of: "2026-03-02",
      open_contracts: 1,
      entered: 1,
      realized_pnl: 4000,
      cumulative_pnl: 4000,
      cumulative_net_pnl: 3800,
      transaction_cost: 200,
      stress_loss: -90000,
      greeks: { delta: -12, gamma: -0.4, vega: 800, theta: -1500 },
    },
    {
      as_of: "2026-03-03",
      open_contracts: 2,
      entered: 1,
      realized_pnl: -1500,
      cumulative_pnl: 2500,
      cumulative_net_pnl: 2100,
      transaction_cost: 200,
      stress_loss: -180000,
      greeks: { delta: -25, gamma: -0.9, vega: 1700, theta: -3100 },
    },
  ],
};

test("the page renders the four locked Onglet-3 sections top to bottom", async () => {
  renderWithClient(<OrdresPage />);

  expect(await screen.findByRole("heading", { name: /Ordres/i, level: 1 })).toBeInTheDocument();
  expect(screen.getByText("Order ticket")).toBeInTheDocument();
  expect(screen.getByText("Send & status")).toBeInTheDocument();
  expect(screen.getByText("Broker reconciliation")).toBeInTheDocument();
  expect(screen.getByText("Backtest", { exact: true })).toBeInTheDocument();
});

test("① the ticket section builds the real ticket from composed legs, preview-only", async () => {
  const user = userEvent.setup();
  server.use(jsonPost("/api/ticket/preview", TICKET));
  renderWithClient(<OrdresPage />);

  await user.click(await screen.findByRole("button", { name: /template straddle/i }));
  const ticketPanel = screen.getByRole("region", { name: /order ticket/i });
  expect(within(ticketPanel).getByText(/preview only/i)).toBeInTheDocument();

  await user.click(within(ticketPanel).getByRole("button", { name: "Build ticket" }));
  const legsTable = await within(ticketPanel).findByRole("table", { name: /order ticket legs/i });
  expect(within(legsTable).getAllByText("BUY").length).toBeGreaterThanOrEqual(1);
});

test("the send-&-status section states it is paper-only with live sending off", async () => {
  renderWithClient(<OrdresPage />);

  expect(await screen.findByText(/Paper only · live sending is off/i)).toBeInTheDocument();
  // There is no separate transmit button — the one (disabled) send control lives on the ticket.
  expect(screen.queryByRole("button", { name: /transmit/i })).not.toBeInTheDocument();
});

test("the ticket's single send affordance is disabled and labelled live-sending-off", async () => {
  const user = userEvent.setup();
  server.use(jsonPost("/api/ticket/preview", TICKET));
  renderWithClient(<OrdresPage />);

  await user.click(await screen.findByRole("button", { name: /template straddle/i }));
  await user.click(screen.getByRole("button", { name: "Build ticket" }));
  await screen.findByRole("table", { name: /order ticket legs/i });

  const ticketPanel = screen.getByRole("region", { name: /order ticket/i });
  const send = within(ticketPanel).getByRole("button", { name: /send order to broker/i });
  expect(send).toBeDisabled();
  expect(within(ticketPanel).getByText(/Live sending is off/i)).toBeInTheDocument();
});

test("③ the broker reconciliation renders its match/break counts (does the broker agree?)", async () => {
  renderWithClient(<OrdresPage />);

  const recon = await screen.findByRole("article", { name: /Broker reconciliation/i });
  expect(within(recon).getByText(/Does the broker agree with our book\?/i)).toBeInTheDocument();
  expect(within(recon).getByRole("heading", { name: /^Positions$/i })).toBeInTheDocument();
  expect(within(recon).getByRole("heading", { name: /^Fills$/i })).toBeInTheDocument();
  expect(within(recon).getAllByText("Match").length).toBeGreaterThanOrEqual(1);
});

test("④ the backtest shows cumulative P&L and the by-Greek attribution", async () => {
  const user = userEvent.setup();
  server.use(jsonPost("/api/backtest/run", RESULT));
  renderWithClient(<OrdresPage />);

  const select = await screen.findByLabelText("backtest index");
  await waitFor(() =>
    expect(within(select).getByRole("option", { name: /EURO STOXX 50/i })).toBeInTheDocument(),
  );
  await user.selectOptions(select, "SX5E");
  await user.type(screen.getByLabelText("start date"), "2026-03-01");
  await user.type(screen.getByLabelText("end date"), "2026-03-31");
  await user.click(screen.getByRole("button", { name: /run backtest/i }));

  const equity = await screen.findByRole("article", { name: "Cumulative P&L" });
  const plot = within(equity).getByLabelText(/Cumulative P&L —/i);
  expect(within(plot).getByTestId("plot-types")).toHaveTextContent("scatter,scatter");

  const which = await screen.findByRole("article", { name: /Which Greek paid/i });
  expect(within(which).getByText(/Theta paid most/i)).toBeInTheDocument();
});
