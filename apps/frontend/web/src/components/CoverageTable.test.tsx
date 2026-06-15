import { render, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";

import { type CoverageData, CoverageTable } from "./CoverageTable";

const POPULATED: CoverageData = {
  underlying: "SPX",
  trade_date: "2026-06-11",
  n_expiries: 2,
  expiries: [
    {
      expiry: "2026-06-19",
      tenor: "10d",
      n_strikes: 32,
      n_calls: 32,
      n_puts: 32,
      strike_min: 7315,
      strike_max: 7470,
    },
    {
      expiry: "2026-09-18",
      tenor: "3m",
      n_strikes: 3,
      n_calls: 3,
      n_puts: 0,
      strike_min: 200,
      strike_max: 210,
    },
  ],
  tenors: [
    { tenor: "10d", measured: 2, floor: 5, status: "fail" },
    { tenor: "1m", measured: 0, floor: 5, status: "fail" },
    { tenor: "3m", measured: 3, floor: 5, status: "fail" },
    { tenor: "6m", measured: null, floor: null, status: "pass" },
  ],
  constituents: [
    {
      symbol: "ASML",
      rank: 1,
      weight: 0.4,
      outcome: "captured",
      n_options: 6,
      detail: "captured 6 option leg(s)",
    },
    {
      symbol: "SAN1",
      rank: 2,
      weight: 0.3,
      outcome: "unentitled",
      n_options: 0,
      detail: "account not entitled (HTTP 403)",
    },
    {
      symbol: "ENEL",
      rank: 3,
      weight: 0.1,
      outcome: "no_options",
      n_options: 0,
      detail: "lists no option months",
    },
  ],
  qc_status: "fail",
  delta_band_status: "fail",
};

const EMPTY: CoverageData = {
  underlying: "SPX",
  trade_date: null,
  n_expiries: 0,
  expiries: [],
  tenors: [{ tenor: "10d", measured: null, floor: null, status: "unknown" }],
  constituents: [],
  qc_status: "unknown",
  delta_band_status: "unknown",
};

test("renders the captured-expiries rows and the whole per-tenor grid", () => {
  render(<CoverageTable data={POPULATED} />);

  // Captured expiries: one row per expiry with the hand-built counts.
  const expiriesTable = screen.getByRole("table", { name: /captured expiries/i });
  const expiryRows = within(expiriesTable).getAllByRole("row").slice(1); // drop header
  expect(expiryRows).toHaveLength(2);
  expect(within(expiryRows[0]).getByText("2026-06-19")).toBeInTheDocument();
  // Strike span is two strikes in scientific notation, sharing one "$" unit: 7315 → 7.315 × 10³,
  // 7470 → 7.47 × 10³. The strike counts (32 / 32) are cardinalities and stay plain.
  expect(within(expiryRows[0]).getByText("7.315 × 10³–7.47 × 10³ $")).toBeInTheDocument();
  expect(within(expiryRows[0]).getByText("32 / 32")).toBeInTheDocument();
  expect(within(expiryRows[1]).getByText("3 / 0")).toBeInTheDocument();

  // Per-tenor coverage: every pinned tenor shows, empty tenors included as labeled rows.
  const tenorTable = screen.getByRole("table", { name: /per-tenor coverage/i });
  const tenorRows = within(tenorTable).getAllByRole("row").slice(1);
  expect(tenorRows).toHaveLength(4);
  // 1m is an empty/failing tenor — it is SHOWN (measured 0), not omitted.
  const oneMonth = tenorRows.find((r) => within(r).queryByText("1m"));
  expect(oneMonth).toBeDefined();
  expect(oneMonth!.getAttribute("data-status")).toBe("fail");
});

test("shows the QC and 30Δ-band badges", () => {
  render(<CoverageTable data={POPULATED} />);
  expect(screen.getByTitle(/QC: fail/i)).toBeInTheDocument();
  expect(screen.getByTitle(/30Δ band: fail/i)).toBeInTheDocument();
});

test("renders the per-constituent capture-outcome ledger, heaviest-first with labelled verdicts", () => {
  render(<CoverageTable data={POPULATED} />);

  const table = screen.getByRole("table", { name: /constituent capture outcomes/i });
  const rows = within(table).getAllByRole("row").slice(1); // drop header
  expect(rows).toHaveLength(3);
  // Rank order: ASML(1) captured, SAN1(2) unentitled, ENEL(3) no_options.
  expect(within(rows[0]).getByText("ASML")).toBeInTheDocument();
  expect(rows[0].getAttribute("data-outcome")).toBe("captured");
  expect(rows[0].getAttribute("data-status")).toBe("pass");
  expect(within(rows[0]).getByText("6")).toBeInTheDocument(); // n_options
  // A non-captured name is shown as a failing row — the entitlement gap is visible, not hidden.
  expect(within(rows[1]).getByText("SAN1")).toBeInTheDocument();
  expect(rows[1].getAttribute("data-outcome")).toBe("unentitled");
  expect(rows[1].getAttribute("data-status")).toBe("fail");
  expect(rows[2].getAttribute("data-outcome")).toBe("no_options");
});

test("omits the constituent table when there are no per-name outcomes", () => {
  render(<CoverageTable data={EMPTY} />);
  expect(
    screen.queryByRole("table", { name: /constituent capture outcomes/i }),
  ).not.toBeInTheDocument();
});

test("renders a labeled empty state when nothing was captured", () => {
  render(<CoverageTable data={EMPTY} />);
  expect(screen.getByText(/No capture for this date/i)).toBeInTheDocument();
  // The expiries table is absent, but the per-tenor grid still renders.
  expect(screen.queryByRole("table", { name: /captured expiries/i })).not.toBeInTheDocument();
  expect(screen.getByRole("table", { name: /per-tenor coverage/i })).toBeInTheDocument();
});
