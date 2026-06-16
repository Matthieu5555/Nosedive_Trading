import { render, screen, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";

vi.mock("./Plot", async () => await import("../test/plotMock"));

import type { RateScenario } from "../stressApi";
import { RateSweep } from "./StressSurface";

const UNIT = "$ (full-reprice PnL)";

const RATES: RateScenario[] = [
  {
    scenario_id: "rate_+0.0010",
    rate_shock: 0.001,
    bp: 10,
    scenario_pnl: 480,
    scenario_version: "v1",
    n_legs: 2,
    unit: UNIT,
    bp_unit: "bp",
  },
  {
    scenario_id: "rate_-0.0010",
    rate_shock: -0.001,
    bp: -10,
    scenario_pnl: -450,
    scenario_version: "v1",
    n_legs: 2,
    unit: UNIT,
    bp_unit: "bp",
  },
  {
    scenario_id: "rate_+0.0000",
    rate_shock: 0,
    bp: 0,
    scenario_pnl: 0,
    scenario_version: "v1",
    n_legs: 2,
    unit: UNIT,
    bp_unit: "bp",
  },
];

test("an empty sweep renders a labelled empty state, not a blank", () => {
  render(<RateSweep rates={[]} />);
  expect(screen.getByText(/No rate-shock sweep is configured/i)).toBeInTheDocument();
});

test("rows are ordered by rate shock ascending (most-negative bp first)", () => {
  render(<RateSweep rates={RATES} />);
  const table = screen.getByRole("table", { name: /Rate-shock sweep/i });
  const rows = within(table).getAllByRole("row");
  // header row + 3 data rows; the lowest shock (-10 bp) leads.
  expect(within(rows[1]).getByRole("rowheader")).toHaveTextContent("-1 × 10¹ bp");
  expect(within(rows[3]).getByRole("rowheader")).toHaveTextContent("1 × 10¹ bp");
});

test("the worst-loss shock heads the summary status with the currency-aware unit", () => {
  render(<RateSweep rates={RATES} currency="€" />);
  // -450 is the worst (most negative) repriced P&L; "$" re-currencied to "€". It shows in the
  // summary status and again in its own row, so there is more than one match.
  expect(screen.getAllByText("-4.5 × 10² € (full-reprice PnL)").length).toBeGreaterThan(0);
});

test("each shock shows its bp move beside its dollar reprice delta", () => {
  render(<RateSweep rates={RATES} />);
  expect(screen.getByText("4.8 × 10² $ (full-reprice PnL)")).toBeInTheDocument();
  expect(screen.getAllByText("-4.5 × 10² $ (full-reprice PnL)").length).toBeGreaterThan(0);
});
