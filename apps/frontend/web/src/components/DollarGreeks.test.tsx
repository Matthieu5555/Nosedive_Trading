import { render, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";

import { DollarGreeks, DollarGreeksMatrix } from "./DollarGreeks";
import type { AnalyticsPoint } from "../api";

const PROV = {
  calc_ts: "2026-06-01T13:31:00+00:00",
  code_version: "abc123",
  config_hash: "cfg-9",
  stamp_hash: "stamp-x",
  n_sources: 4,
};

// A single hand-built point. Values chosen so the scientific-notation rendering is easy to derive
// by hand: raw delta -0.3 → "-3 × 10⁻¹", raw gamma 0.02 → "2 × 10⁻²". The backend unit strings
// carry "$" as the currency placeholder (the stored legacy contract); the front re-currencies them.
const POINT: AnalyticsPoint = {
  delta_band: "30dp",
  target_delta: -0.3,
  log_moneyness: -0.15,
  strike: 165.75,
  forward_price: 195.0,
  implied_vol: 0.27,
  total_variance: 0.0182,
  price: 4.2,
  metrics: {
    delta: { raw: -0.3, dollar: -58.5, unit: "$ per $1 of underlying" },
    gamma: { raw: 0.02, dollar: 7.6, unit: "$ per 1% move" },
    vega: { raw: 0.31, dollar: 0.31, unit: "$ per 1 vol point" },
    theta: { raw: -0.05, dollar: -0.000041, unit: "$ per calendar day" },
    rho: { raw: 0.04, dollar: 0.0005, unit: "$ per 1% rate" },
  },
  provenance: PROV,
};

// A point with a null delta unit, to exercise the "n/a" fallback (an older partition predates the
// unit field). The matrix's unitFor must find the first non-null unit / fall back to "n/a".
const POINT_NULL_DELTA_UNIT: AnalyticsPoint = {
  ...POINT,
  metrics: {
    ...POINT.metrics,
    delta: { raw: -0.3, dollar: -58.5, unit: null },
  },
};

test("DollarGreeks renders the default ($) currency unchanged", () => {
  render(<DollarGreeks point={POINT} />);
  const table = screen.getByRole("table", { name: /Dollar Greeks/i });
  // Backend dollar unit verbatim (no currency prop → "$").
  expect(within(table).getByText("$ per $1 of underlying")).toBeInTheDocument();
  expect(within(table).getByText("$ per 1% move")).toBeInTheDocument();
  // The raw delta unit token "$/$" rides alongside the raw number in the "raw" column.
  expect(within(table).getByText("-3 × 10⁻¹ $/$")).toBeInTheDocument();
});

test("DollarGreeks renders monetized units in the index's currency (€ for SX5E)", () => {
  render(<DollarGreeks point={POINT} currency="€" />);
  const table = screen.getByRole("table", { name: /Dollar Greeks/i });
  // Backend dollar unit strings re-currencied: "$ per $1 of underlying" → "€ per €1 of underlying".
  expect(within(table).getByText("€ per €1 of underlying")).toBeInTheDocument();
  expect(within(table).getByText("€ per 1% move")).toBeInTheDocument();
  // The raw delta unit token "$/$" → "€/€", alongside the unchanged scientific-notation number.
  expect(within(table).getByText("-3 × 10⁻¹ €/€")).toBeInTheDocument();
  // The raw gamma unit token "1/$" → "1/€".
  expect(within(table).getByText("2 × 10⁻² 1/€")).toBeInTheDocument();
  // The legacy "$"-placeholder is gone from the unit column: the old $-unit text is absent.
  expect(within(table).queryByText("$ per $1 of underlying")).not.toBeInTheDocument();
  expect(within(table).queryByText("-3 × 10⁻¹ $/$")).not.toBeInTheDocument();
});

test("DollarGreeksMatrix renders the default ($) currency unchanged", () => {
  render(<DollarGreeksMatrix points={[POINT]} />);
  const table = screen.getByRole("table", { name: /Dollar Greeks by delta band/i });
  expect(within(table).getByText("$ per $1 of underlying")).toBeInTheDocument();
  expect(within(table).getByText("$ per 1% move")).toBeInTheDocument();
});

test("DollarGreeksMatrix renders the row unit strings in the index's currency (€)", () => {
  render(<DollarGreeksMatrix points={[POINT]} currency="€" />);
  const table = screen.getByRole("table", { name: /Dollar Greeks by delta band/i });
  expect(within(table).getByText("€ per €1 of underlying")).toBeInTheDocument();
  expect(within(table).getByText("€ per 1% move")).toBeInTheDocument();
  // The legacy "$"-placeholder unit text is gone from the unit column.
  expect(within(table).queryByText("$ per $1 of underlying")).not.toBeInTheDocument();
  expect(within(table).queryByText("$ per 1% move")).not.toBeInTheDocument();
});

test("DollarGreeksMatrix falls back to 'n/a' for a Greek with no unit, in any currency", () => {
  render(<DollarGreeksMatrix points={[POINT_NULL_DELTA_UNIT]} currency="€" />);
  const table = screen.getByRole("table", { name: /Dollar Greeks by delta band/i });
  // The delta row has a null unit → labelled "n/a" (never a re-currencied blank).
  expect(within(table).getByText("n/a")).toBeInTheDocument();
  // The other rows still re-currency normally.
  expect(within(table).getByText("€ per 1% move")).toBeInTheDocument();
});
