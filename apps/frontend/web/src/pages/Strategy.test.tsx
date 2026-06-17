import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, test, vi } from "vitest";

vi.mock("../components/Plot", async () => await import("../test/plotMock"));

import type { BacktestResult } from "../api";
import { jsonPost, server } from "../test/server";
import { StrategyPage } from "./Strategy";

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

async function runDefaultBacktest(user: ReturnType<typeof userEvent.setup>) {
  const select = await screen.findByLabelText("backtest index");
  await waitFor(() =>
    expect(within(select).getByRole("option", { name: /EURO STOXX 50/i })).toBeInTheDocument(),
  );
  await user.selectOptions(select, "SX5E");
  await user.type(screen.getByLabelText("start date"), "2026-03-01");
  await user.type(screen.getByLabelText("end date"), "2026-03-31");
  await user.click(screen.getByRole("button", { name: /run backtest/i }));
}

test("the page starts with an empty-state prompt and no results", async () => {
  render(<StrategyPage />);
  expect(await screen.findByText(/No backtest run yet/i)).toBeInTheDocument();
  expect(screen.queryByRole("article", { name: /Backtest summary/i })).not.toBeInTheDocument();
});

test("the index selector is wired to /api/indices (SX5E + SPX offered)", async () => {
  render(<StrategyPage />);
  const select = await screen.findByLabelText("backtest index");
  await waitFor(() =>
    expect(within(select).getAllByRole("option").length).toBeGreaterThanOrEqual(2),
  );
  expect(within(select).getByRole("option", { name: /EURO STOXX 50/i })).toBeInTheDocument();
});

test("the run button is disabled until both dates are picked", async () => {
  const user = userEvent.setup();
  render(<StrategyPage />);
  const run = await screen.findByRole("button", { name: /run backtest/i });
  expect(run).toBeDisabled();

  await user.type(screen.getByLabelText("start date"), "2026-03-01");
  await user.type(screen.getByLabelText("end date"), "2026-03-31");
  expect(screen.getByRole("button", { name: /run backtest/i })).toBeEnabled();
});

test("running a backtest renders the summary strip with net P&L, Sharpe and worst stress", async () => {
  const user = userEvent.setup();
  server.use(jsonPost("/api/backtest/run", RESULT));
  render(<StrategyPage />);
  await runDefaultBacktest(user);

  const summary = await screen.findByRole("article", { name: /Backtest summary/i });
  // net 118500 -> 1.185e5 €, shown both in the status badge and as a metric.
  expect(within(summary).getAllByText("1.185 × 10⁵ €").length).toBeGreaterThanOrEqual(1);
  // Sharpe 1.37 -> 1.37 × 10⁰ (unitless ratio).
  expect(within(summary).getByText("1.37 × 10⁰")).toBeInTheDocument();
  // worst stress -310000 -> -3.1e5 €.
  expect(within(summary).getByText("-3.1 × 10⁵ €")).toBeInTheDocument();
});

test("the cumulative attribution names theta as what paid (largest positive bar)", async () => {
  const user = userEvent.setup();
  server.use(jsonPost("/api/backtest/run", RESULT));
  render(<StrategyPage />);
  await runDefaultBacktest(user);

  const which = await screen.findByRole("article", { name: /Which Greek paid/i });
  // Theta (+215000) is the largest magnitude, so it "paid most".
  expect(within(which).getByText(/Theta paid most/i)).toBeInTheDocument();
  // Total of all seven terms: -8000-55000-22000+215000+1500-3000-1000 = 127500.
  const legend = within(which).getByRole("list", { name: /by-Greek contributions/i });
  expect(within(legend).getByText("1.275 × 10⁵ €")).toBeInTheDocument();
});

test("the equity curve plots both the gross and net cumulative lines", async () => {
  const user = userEvent.setup();
  server.use(jsonPost("/api/backtest/run", RESULT));
  render(<StrategyPage />);
  await runDefaultBacktest(user);

  const equity = await screen.findByRole("article", { name: "Cumulative P&L" });
  const plot = within(equity).getByLabelText(/Cumulative P&L —/i);
  // Two scatter traces (gross + net).
  expect(within(plot).getByTestId("plot-types")).toHaveTextContent("scatter,scatter");
  // Ending net 2100 -> 2.1e3.
  expect(within(equity).getByText("2.1 × 10³ €")).toBeInTheDocument();
});

test("a labelled 400 from the BFF surfaces as an alert, not a blank page", async () => {
  const user = userEvent.setup();
  server.use(
    http.post("/api/backtest/run", () =>
      HttpResponse.json({ error: "no_banked_days", detail: "no partitions" }, { status: 400 }),
    ),
  );
  render(<StrategyPage />);
  await runDefaultBacktest(user);

  await waitFor(() => expect(screen.getByText(/Backtest failed:/i)).toBeInTheDocument());
  expect(screen.getByText(/no partitions/i)).toBeInTheDocument();
  expect(screen.queryByRole("article", { name: /Backtest summary/i })).not.toBeInTheDocument();
});
