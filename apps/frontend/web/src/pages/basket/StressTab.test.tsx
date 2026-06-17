import { render, screen, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";

vi.mock("../../components/Plot", async () => await import("../../test/plotMock"));

import type { BasketScenariosResponse, NamedScenario, RateScenario } from "../../stressApi";
import { StressTab } from "./StressTab";

const SURFACE = {
  spot_shock: [-0.5, 0.0, 0.5],
  vol_shock: [-0.5, 0.0, 0.5],
  scenario_pnl: [
    [-2000, -1500, -900],
    [0, 0, 0],
    [800, 1200, 1700],
  ] as (number | null)[][],
  scenario_version: "scn-1.0.0",
  unit: "$ (full-reprice PnL)",
  n_cells: 9,
  has_holes: false,
  n_holes: 0,
};

const RATES: RateScenario[] = [
  {
    scenario_id: "rate_+0.0010",
    rate_shock: 0.001,
    bp: 10,
    scenario_pnl: 480,
    scenario_version: "v1",
    n_legs: 2,
    unit: "$ (full-reprice PnL)",
    bp_unit: "bp",
  },
  {
    scenario_id: "rate_-0.0010",
    rate_shock: -0.001,
    bp: -10,
    scenario_pnl: -450,
    scenario_version: "v1",
    n_legs: 2,
    unit: "$ (full-reprice PnL)",
    bp_unit: "bp",
  },
];

function stressOf(rate?: RateScenario[]): BasketScenariosResponse {
  return {
    basket_id: "basket-AAA-latest",
    trade_date: "2026-06-05",
    underlying: "AAA",
    surface: SURFACE,
    worst_case: { spot_shock: -0.5, vol_shock: -0.5, pnl: -2000, unit: "$ (full-reprice PnL)" },
    n_legs: 2,
    n_resolved: 2,
    gaps: [],
    n_gaps: 0,
    rate,
    n_rate: rate?.length,
  };
}

const NAMED: NamedScenario[] = [
  {
    scenario_id: "gfc-2008",
    label: "GFC 2008",
    spot_shock: -0.45,
    vol_shock: 0.3,
    rate_shock: -0.02,
    scenario_pnl: -8200,
    scenario_version: "v1",
    n_legs: 2,
    unit: "$ (full-reprice PnL)",
  },
];

const NOOP = () => {};

test("the rate sweep renders each cell labelled in bp and dollars when the basket payload carries rates", () => {
  render(
    <StressTab
      canStress
      loading={false}
      error={null}
      stress={stressOf(RATES)}
      currency="$"
      onStress={NOOP}
      namedScenarios={[]}
      namedLoading={false}
      namedError={null}
    />,
  );

  const table = screen.getByRole("table", { name: /rate-shock sweep/i });
  // bp label (basis points) and the dollar reprice delta, each rendered via the sig-fig formatter.
  expect(within(table).getByText("1 × 10¹ bp")).toBeInTheDocument();
  expect(within(table).getByText("-1 × 10¹ bp")).toBeInTheDocument();
  expect(within(table).getByText("4.8 × 10² $ (full-reprice PnL)")).toBeInTheDocument();
  expect(within(table).getByText("-4.5 × 10² $ (full-reprice PnL)")).toBeInTheDocument();
});

test("no rate family in the basket payload renders no rate-sweep panel (backward-compatible)", () => {
  render(
    <StressTab
      canStress
      loading={false}
      error={null}
      stress={stressOf(undefined)}
      currency="$"
      onStress={NOOP}
      namedScenarios={[]}
      namedLoading={false}
      namedError={null}
    />,
  );

  expect(screen.queryByRole("table", { name: /rate-shock sweep/i })).not.toBeInTheDocument();
});

test("the named historical crises render as shock presets in ③ Stress", () => {
  render(
    <StressTab
      canStress
      loading={false}
      error={null}
      stress={null}
      currency="$"
      onStress={NOOP}
      namedScenarios={NAMED}
      namedLoading={false}
      namedError={null}
    />,
  );

  expect(screen.getByRole("heading", { name: /shock presets/i })).toBeInTheDocument();
  const table = screen.getByRole("table", { name: /named historical scenarios/i });
  expect(within(table).getByText("GFC 2008")).toBeInTheDocument();
});

test("an empty named-scenario catalogue renders a labelled empty state, not a blank", () => {
  render(
    <StressTab
      canStress
      loading={false}
      error={null}
      stress={null}
      currency="$"
      onStress={NOOP}
      namedScenarios={[]}
      namedLoading={false}
      namedError={null}
    />,
  );

  expect(screen.getByText(/No named historical scenarios are configured/i)).toBeInTheDocument();
});
