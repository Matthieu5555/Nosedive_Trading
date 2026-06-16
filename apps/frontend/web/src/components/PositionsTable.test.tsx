import { render, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";

import type { PositionGreek, PositionLine } from "../api";
import { PositionsTable } from "./PositionsTable";

function greek(dollar: number, unit: string): PositionGreek {
  return { raw: dollar / 100, position: dollar, dollar, unit };
}

const LINE: PositionLine = {
  contract_key: "SX5E|OPT|EUR|XEUR|10|d|2026-09-18|4200|P",
  underlying: "SX5E",
  strike: 4200,
  expiry: "2026-09-18",
  option_right: "P",
  multiplier: 10,
  quantity: 2,
  broker_contract_id: "111",
  mark_price: 4.25,
  market_value: 85,
  greeks: {
    delta: greek(-585, "$ per $1 of underlying"),
    gamma: greek(76, "$ per 1% move"),
    vega: greek(31, "$ per 1 vol point"),
    theta: greek(-4.1, "$ per calendar day"),
    rho: greek(5, "$ per 1% rate"),
  },
};

test("PositionsTable shows one row per contract with qty, mark, market value and dollar Greeks", () => {
  render(<PositionsTable lines={[LINE]} />);
  const table = screen.getByRole("table", { name: /Open positions/i });

  expect(within(table).getByText("SX5E P 4.2 × 10³ 2026-09-18")).toBeInTheDocument();

  expect(within(table).getByText("2 × 10⁰")).toBeInTheDocument();

  expect(within(table).getByText("4.25 × 10⁰ $")).toBeInTheDocument();
  expect(within(table).getByText("8.5 × 10¹ $")).toBeInTheDocument();

  expect(within(table).getByText("-5.85 × 10² $ per $1 of underlying")).toBeInTheDocument();
});

test("PositionsTable re-currencies the marks and units for the index currency (€)", () => {
  render(<PositionsTable lines={[LINE]} currency="€" />);
  const table = screen.getByRole("table", { name: /Open positions/i });

  expect(within(table).getByText("4.25 × 10⁰ €")).toBeInTheDocument();
  expect(within(table).getByText("-5.85 × 10² € per €1 of underlying")).toBeInTheDocument();
  expect(within(table).queryByText("-5.85 × 10² $ per $1 of underlying")).not.toBeInTheDocument();
});

test("PositionsTable shows a labelled empty state when no positions are open", () => {
  render(<PositionsTable lines={[]} />);
  expect(screen.getByRole("status")).toHaveTextContent(/No open positions/i);
});
