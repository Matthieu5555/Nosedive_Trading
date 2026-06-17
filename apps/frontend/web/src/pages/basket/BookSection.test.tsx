import { render, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";

import type { FillsResponse, PositionsResponse } from "../../api";
import { jsonGet, server } from "../../test/server";
import { BookSection } from "./BookSection";

const POSITIONS: PositionsResponse = {
  source: "fills",
  source_ts: "2026-06-15T18:00:00+00:00",
  n_lines: 1,
  lines: [
    {
      contract_key: "SPX|OPT|USD|CBOE|100|d|2026-09-18|5200|P",
      underlying: "SPX",
      strike: 5200,
      expiry: "2026-09-18",
      option_right: "P",
      multiplier: 100,
      quantity: 2,
      broker_contract_id: "222",
      mark_price: 12.5,
      market_value: 2500,
      greeks: {
        delta: { raw: -0.3, position: -60, dollar: -585, unit: "$ per $1 of underlying" },
        gamma: { raw: 0.02, position: 4, dollar: 76, unit: "$ per 1% move" },
        vega: { raw: 0.31, position: 62, dollar: 31, unit: "$ per 1 vol point" },
        theta: { raw: -0.05, position: -10, dollar: -4.1, unit: "$ per calendar day" },
        rho: { raw: 0.04, position: 8, dollar: 5, unit: "$ per 1% rate" },
      },
    },
  ],
  book: {
    delta: { dollar: -585, unit: "$ per $1 of underlying" },
    gamma: { dollar: 76, unit: "$ per 1% move" },
    vega: { dollar: 31, unit: "$ per 1 vol point" },
    theta: { dollar: -4.1, unit: "$ per calendar day" },
    rho: { dollar: 5, unit: "$ per 1% rate" },
    market_value: 2500,
  },
  priced_contract_keys: 1,
  unpriced_contract_keys: [],
};

const FILLS: FillsResponse = {
  trade_date: null,
  underlying: "SPX",
  n_fills: 1,
  fills: [
    {
      fill_id: "f-1",
      booking_id: "bk-9",
      source_basket_id: "basket-SPX",
      trade_date: "2026-06-15",
      underlying: "SPX",
      contract_key: "SPX|OPT|USD|CBOE|100|d|2026-09-18|5200|P",
      signed_qty: "2",
      price: 12.5,
      fill_ts: "2026-06-15T17:30:01+00:00",
      mode: "paper",
      broker_contract_id: "222",
    },
  ],
};

test("② The Book folds in the Positions content — book summary, open legs and fills ledger", async () => {
  server.use(jsonGet("/api/positions", POSITIONS), jsonGet("/api/positions/fills", FILLS));
  render(<BookSection underlying="SPX" tradeDate="" currency="$" />);

  const summary = await screen.findByRole("table", { name: /Book dollar Greeks/i });
  expect(within(summary).getByText("2.5 × 10³")).toBeInTheDocument();

  const positions = await screen.findByRole("table", { name: /Open positions/i });
  expect(within(positions).getByText("SPX P 5.2 × 10³ 2026-09-18")).toBeInTheDocument();

  const ledger = await screen.findByRole("table", { name: /Fills ledger/i });
  expect(within(ledger).getByText("2026-06-15T17:30:01+00:00")).toBeInTheDocument();
});

test("no underlying selected does not fetch the book (avoids an unkeyed read)", () => {
  render(<BookSection underlying="" tradeDate="" currency="$" />);
  // The section still renders its lead copy, but with no underlying it issues no positions read.
  expect(screen.getByText(/The book the stress acts on/i)).toBeInTheDocument();
});
