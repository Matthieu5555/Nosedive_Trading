import { render, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";

import type { Provenance, Signal, SignalsResponse } from "../api";
import { SignalsView } from "./SignalsView";

const PROV: Provenance = {
  calc_ts: "2026-06-15T17:30:00+00:00",
  code_version: "test",
  config_hash: "cfg",
  stamp_hash: "stamp",
  n_sources: 3,
};

function signal(over: Partial<Signal>): Signal {
  return {
    signal_kind: "iv_rank",
    label: "IV rank",
    subject: "SX5E",
    tenor_label: "1m",
    value: 0,
    unit: "fraction [0,1]",
    snapshot_ts: "2026-06-15T17:30:00+00:00",
    source_snapshot_ts: "2026-06-15T17:30:00+00:00",
    provenance: PROV,
    ...over,
  };
}

const RANK_HIGH = signal({ subject: "SX5E", tenor_label: "1m", value: 0.62 });
const RANK_LOW = signal({ subject: "SX5E", tenor_label: "3m", value: 0.1 });

const CORR = signal({
  signal_kind: "implied_correlation",
  label: "Implied correlation ρ̄",
  unit: "correlation [-1,1]",
  subject: "SX5E",
  tenor_label: "3m",
  value: 0.5,
});

const RVIV_POS = signal({
  signal_kind: "iv_vs_realized",
  label: "Realized − implied",
  unit: "vol points (annualized)",
  subject: "SX5E",
  tenor_label: "1m",
  value: 0.0425,
});
const RVIV_NEG = signal({
  signal_kind: "iv_vs_realized",
  label: "Realized − implied",
  unit: "vol points (annualized)",
  subject: "TOTF",
  tenor_label: "1m",
  value: -0.018,
});

function envelope(signals: Signal[]): SignalsResponse {
  const by_kind: Record<string, Signal[]> = {};
  const kinds: string[] = [];
  for (const s of signals) {
    if (!by_kind[s.signal_kind]) {
      by_kind[s.signal_kind] = [];
      kinds.push(s.signal_kind);
    }
    by_kind[s.signal_kind].push(s);
  }
  return {
    underlying: "SX5E",
    trade_date: "2026-06-15",
    snapshot_ts: "2026-06-15T17:30:00+00:00",
    n_signals: signals.length,
    kinds,
    by_kind,
    signals,
  };
}

test("renders one panel per kind with its label, unit and plain caption", () => {
  render(<SignalsView data={envelope([RANK_HIGH, CORR])} />);

  expect(screen.getByRole("heading", { name: "IV rank" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Implied correlation ρ̄" })).toBeInTheDocument();

  expect(screen.getByText("fraction [0,1]")).toBeInTheDocument();
  expect(screen.getByText("correlation [-1,1]")).toBeInTheDocument();

  expect(screen.getByText(/where today's implied vol sits/i)).toBeInTheDocument();
  expect(screen.getByText(/average implied correlation/i)).toBeInTheDocument();
});

test("iv_rank renders as a percent of its range with a left-anchored bar", () => {
  render(<SignalsView data={envelope([RANK_HIGH, RANK_LOW])} />);

  const table = screen.getByRole("table", { name: /IV rank signals/i });
  const rows = within(table).getAllByRole("row").slice(1);
  expect(rows).toHaveLength(2);

  // 0.62 -> "62.0%", 0.1 -> "10.0%" (independent of the component's arithmetic).
  expect(within(rows[0]).getByText("62.0%")).toBeInTheDocument();
  expect(within(rows[1]).getByText("10.0%")).toBeInTheDocument();

  const fill = rows[0].querySelector(".signal-bar-fill") as HTMLElement;
  expect(fill.style.left).toBe("0%");
  expect(fill.style.width).toBe("62%");
  expect(fill.getAttribute("data-tone")).toBe("neutral");
});

test("vol-point kinds render in sci+unit and scale the signed bar to the panel max", () => {
  render(<SignalsView data={envelope([RVIV_POS, RVIV_NEG])} />);

  const table = screen.getByRole("table", { name: /Realized − implied signals/i });
  const rows = within(table).getAllByRole("row").slice(1);

  expect(within(rows[0]).getByText("4.25 × 10⁻² vol points (annualized)")).toBeInTheDocument();
  expect(within(rows[1]).getByText("-1.8 × 10⁻² vol points (annualized)")).toBeInTheDocument();

  // Panel max |value| = 0.0425. Positive 0.0425 fills the full right half (50%) from centre.
  const pos = rows[0].querySelector(".signal-bar-fill") as HTMLElement;
  expect(pos.getAttribute("data-tone")).toBe("positive");
  expect(pos.style.left).toBe("50%");
  expect(pos.style.width).toBe("50%");

  // Negative -0.018 -> half = (0.018/0.0425)*50 = 21.176…%; grows leftward from centre.
  const neg = rows[1].querySelector(".signal-bar-fill") as HTMLElement;
  expect(neg.getAttribute("data-tone")).toBe("negative");
  const negHalf = (0.018 / 0.0425) * 50;
  expect(parseFloat(neg.style.width)).toBeCloseTo(negHalf, 6);
  expect(parseFloat(neg.style.left)).toBeCloseTo(50 - negHalf, 6);
});

test("implied_correlation uses a fixed [-1,1] axis, not the panel max", () => {
  render(<SignalsView data={envelope([CORR])} />);

  const table = screen.getByRole("table", { name: /Implied correlation/i });
  const rows = within(table).getAllByRole("row").slice(1);
  const fill = rows[0].querySelector(".signal-bar-fill") as HTMLElement;

  // 0.5 on the fixed [-1,1] axis -> half = 0.5*50 = 25%, not 50% (which a panel-max scale would give).
  expect(fill.getAttribute("data-tone")).toBe("positive");
  expect(parseFloat(fill.style.width)).toBeCloseTo(25, 6);
  expect(fill.style.left).toBe("50%");
});

test("labelled-empty envelope shows a plain no-signals state, not a blank", () => {
  const empty: SignalsResponse = {
    underlying: "SX5E",
    trade_date: "2026-06-15",
    snapshot_ts: null,
    n_signals: 0,
    kinds: [],
    by_kind: {},
    signals: [],
  };
  render(<SignalsView data={empty} />);
  expect(screen.getByRole("status")).toHaveTextContent(/No signals recorded for SX5E on 2026-06-15/i);
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
});
