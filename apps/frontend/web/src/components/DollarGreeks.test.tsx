import { render, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";

import type { AnalyticsPoint } from "../api";
import { DollarGreeks, DollarGreeksMatrix } from "./DollarGreeks";

const PROV = {
  calc_ts: "2026-06-01T13:31:00+00:00",
  code_version: "abc123",
  config_hash: "cfg-9",
  stamp_hash: "stamp-x",
  n_sources: 4,
};

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

  expect(within(table).getByText("$ per $1 of underlying")).toBeInTheDocument();
  expect(within(table).getByText("$ per 1% move")).toBeInTheDocument();

  expect(within(table).getByText("-3 × 10⁻¹ $/$")).toBeInTheDocument();
});

test("DollarGreeks renders monetized units in the index's currency (€ for SX5E)", () => {
  render(<DollarGreeks point={POINT} currency="€" />);
  const table = screen.getByRole("table", { name: /Dollar Greeks/i });

  expect(within(table).getByText("€ per €1 of underlying")).toBeInTheDocument();
  expect(within(table).getByText("€ per 1% move")).toBeInTheDocument();

  expect(within(table).getByText("-3 × 10⁻¹ €/€")).toBeInTheDocument();

  expect(within(table).getByText("2 × 10⁻² 1/€")).toBeInTheDocument();

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

  expect(within(table).queryByText("$ per $1 of underlying")).not.toBeInTheDocument();
  expect(within(table).queryByText("$ per 1% move")).not.toBeInTheDocument();
});

test("DollarGreeksMatrix falls back to 'n/a' for a Greek with no unit, in any currency", () => {
  render(<DollarGreeksMatrix points={[POINT_NULL_DELTA_UNIT]} currency="€" />);
  const table = screen.getByRole("table", { name: /Dollar Greeks by delta band/i });

  expect(within(table).getByText("n/a")).toBeInTheDocument();

  expect(within(table).getByText("€ per 1% move")).toBeInTheDocument();
});
