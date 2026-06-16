import { render, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";

import type { NamedScenario } from "../stressApi";
import { NamedScenarios } from "./NamedScenarios";

const UNIT = "$ (full-reprice PnL)";

const SCENARIOS: NamedScenario[] = [
  {
    scenario_id: "named_covid-2020",
    label: "covid-2020",
    spot_shock: -0.34,
    vol_shock: 0.45,
    rate_shock: 0,
    scenario_pnl: -1500,
    scenario_version: "v1",
    n_legs: 1,
    unit: UNIT,
  },
  {
    scenario_id: "named_2008",
    label: "2008",
    spot_shock: -0.4,
    vol_shock: 0.3,
    rate_shock: -0.01,
    scenario_pnl: -2000,
    scenario_version: "v1",
    n_legs: 2,
    unit: UNIT,
  },
];

test("an empty list renders a labelled empty state, not a blank", () => {
  render(<NamedScenarios scenarios={[]} />);
  expect(screen.getByText(/No named historical scenarios are configured/i)).toBeInTheDocument();
});

test("the worst-loss scenario heads the table and the kicker", () => {
  render(<NamedScenarios scenarios={SCENARIOS} />);

  // -2000 (2008) is more negative than -1500 (covid), so it is the worst case.
  expect(screen.getByText(/Worst case: 2008/)).toBeInTheDocument();

  const table = screen.getByRole("table", { name: /Named historical scenarios/i });
  const rows = within(table).getAllByRole("row");
  // header row + 2 data rows; first data row is the worst (2008).
  const firstData = within(rows[1]).getByRole("rowheader");
  expect(firstData).toHaveTextContent("2008");
});

test("each scenario shows its spot shock as a percent and its stressed P&L with the unit", () => {
  render(<NamedScenarios scenarios={SCENARIOS} />);

  // spot_shock -0.4 -> -40.00%
  expect(screen.getByText("-40.00%")).toBeInTheDocument();
  // P&L -2000 in scientific notation with the unit (in the header status and the worst row).
  expect(screen.getAllByText(`-2 × 10³ ${UNIT}`).length).toBeGreaterThan(0);
});

test("a null rate shock renders as a dash, not the literal 'null'", () => {
  const noRate: NamedScenario[] = [{ ...SCENARIOS[0], rate_shock: null }];
  render(<NamedScenarios scenarios={noRate} />);
  const table = screen.getByRole("table", { name: /Named historical scenarios/i });
  const cells = within(table).getAllByRole("cell");
  expect(cells.some((cell) => cell.textContent === "—")).toBe(true);
});
