import { render, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";

import type { ReconciliationResponse } from "../api";
import { Reconciliation } from "./Reconciliation";

const AGREES: ReconciliationResponse = {
  account_id: "DUQ574355",
  as_of_ts: "2026-06-12T16:30:00+00:00",
  book_source: "fills.jsonl",
  book_source_ts: "2026-06-12T16:30:00+00:00",
  threshold_version: "recon-1",
  ok: true,
  positions: {
    counts: { match: 2, break: 0, broker_only: 0, book_only: 0 },
    n_lines: 2,
    lines: [],
  },
  cash: { counts: { match: 0, break: 0, broker_only: 1, book_only: 0 }, n_lines: 1, lines: [] },
  fills: { counts: { match: 2, break: 0, broker_only: 0, book_only: 0 }, n_lines: 2, lines: [] },
};

const BREAKS: ReconciliationResponse = {
  ...AGREES,
  ok: false,
  positions: {
    counts: { match: 1, break: 1, broker_only: 0, book_only: 0 },
    n_lines: 2,
    lines: [
      {
        join_key: "265598",
        broker_contract_key: "SX5E-CALL",
        book_contract_key: "SX5E-CALL",
        broker_quantity: 5,
        book_quantity: 3,
        quantity_diff: 2,
        status: "break",
        threshold: 0,
        threshold_version: "recon-1",
      },
      {
        join_key: "311042",
        broker_contract_key: "SX5E-PUT",
        book_contract_key: "SX5E-PUT",
        broker_quantity: -4,
        book_quantity: -4,
        quantity_diff: 0,
        status: "match",
        threshold: 0,
        threshold_version: "recon-1",
      },
    ],
  },
};

test("an agreeing book reads 'In agreement' and shows the per-status counts", () => {
  render(<Reconciliation report={AGREES} />);
  expect(screen.getByText("In agreement")).toBeInTheDocument();
  expect(screen.getByText(/Every broker position matches a book position/i)).toBeInTheDocument();
  expect(screen.getByText("Account DUQ574355")).toBeInTheDocument();
});

test("a break is flagged and only the breaking line is tabled", () => {
  render(<Reconciliation report={BREAKS} />);
  expect(screen.getByText("Breaks found")).toBeInTheDocument();

  const table = screen.getByRole("table", { name: /Position breaks/i });
  const dataRows = within(table).getAllByRole("row").slice(1);
  // Only the one breaking position (qty 5 vs 3), the matched put is excluded.
  expect(dataRows).toHaveLength(1);
  expect(within(dataRows[0]).getByRole("rowheader")).toHaveTextContent("SX5E-CALL");
  // broker_quantity 5, book_quantity 3, quantity_diff 2 are counts, plain integers (not sci).
  expect(within(dataRows[0]).getByText("5")).toBeInTheDocument();
  expect(within(dataRows[0]).getByText("3")).toBeInTheDocument();
  expect(within(dataRows[0]).getByText("2")).toBeInTheDocument();
});

test("cash is framed as broker-only and never claims to reconcile", () => {
  render(<Reconciliation report={AGREES} />);
  expect(screen.getByText(/our fills-based book carries no cash leg/i)).toBeInTheDocument();
});
