import { render, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";

import type { Fill } from "../api";
import { FillsLedger } from "./FillsLedger";

const FILL: Fill = {
  fill_id: "f-1",
  booking_id: "bk-9",
  source_basket_id: "basket-SX5E",
  trade_date: "2026-06-15",
  underlying: "SX5E",
  contract_key: "SX5E|OPT|EUR|XEUR|10|d|2026-09-18|4200|P",
  signed_qty: "-3",
  price: 4.5,
  fill_ts: "2026-06-15T17:30:01+00:00",
  mode: "paper",
  broker_contract_id: "111",
};

test("FillsLedger shows each fill with its venue timestamp, signed qty and price", () => {
  render(<FillsLedger fills={[FILL]} />);
  const table = screen.getByRole("table", { name: /Fills ledger/i });

  expect(within(table).getByText("2026-06-15T17:30:01+00:00")).toBeInTheDocument();
  expect(within(table).getByText("SX5E P 4200 2026-09-18")).toBeInTheDocument();

  expect(within(table).getByText("-3 × 10⁰")).toBeInTheDocument();
  expect(within(table).getByText("4.5 × 10⁰ $")).toBeInTheDocument();
  expect(within(table).getByText("paper")).toBeInTheDocument();
  expect(within(table).getByText("bk-9")).toBeInTheDocument();
});

test("FillsLedger re-currencies the fill price for the index currency (€)", () => {
  render(<FillsLedger fills={[FILL]} currency="€" />);
  const table = screen.getByRole("table", { name: /Fills ledger/i });
  expect(within(table).getByText("4.5 × 10⁰ €")).toBeInTheDocument();
});

test("FillsLedger shows a labelled empty state when nothing is booked", () => {
  render(<FillsLedger fills={[]} />);
  expect(screen.getByRole("status")).toHaveTextContent(/No fills booked/i);
});
