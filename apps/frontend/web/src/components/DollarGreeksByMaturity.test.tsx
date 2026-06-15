// The per-maturity Greeks transpose: Greeks as columns (raw + currency pair), deltas as rows,
// one maturity in view via a selector, railed rows flagged. Independent fixtures, user-facing
// assertions (the rendered table the operator reads), not internal calls.

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test } from "vitest";

import type { AnalyticsMaturity } from "../api";
import { ANALYTICS_AAA, ANALYTICS_AAA_DEGENERATE } from "../test/fixtures";
import { DollarGreeksByMaturity } from "./DollarGreeksByMaturity";

const PROV = {
  calc_ts: "2026-05-29T15:31:00+00:00",
  code_version: "abc123",
  config_hash: "cfg-9",
  stamp_hash: "stamp-x",
  n_sources: 4,
};

// A clean second maturity so the selector has two options to switch between.
const CLEAN_12M: AnalyticsMaturity = {
  maturity_years: 1.0,
  tenor_label: "12m",
  label: "12m (1.000y)",
  smile: {
    axis_type: "delta",
    deltas: [-0.3, 0.3],
    implied_vols: [0.22, 0.2],
    log_moneyness: [-0.1, 0.1],
  },
  surface_slice: null,
  points: [
    {
      delta_band: "30dp",
      target_delta: -0.3,
      log_moneyness: -0.1,
      strike: 175.0,
      forward_price: 195.0,
      implied_vol: 0.22,
      total_variance: 0.05,
      price: 6.0,
      metrics: {
        delta: { raw: -0.3, dollar: -55.0, unit: "$ per $1 of underlying" },
        gamma: { raw: 0.01, dollar: 4.0, unit: "$ per 1% move" },
        vega: { raw: 0.5, dollar: 0.5, unit: "$ per 1 vol point" },
        theta: { raw: -0.01, dollar: -0.00002, unit: "$ per calendar day" },
        rho: { raw: 0.08, dollar: 0.001, unit: "$ per 1% rate" },
      },
      provenance: PROV,
    },
  ],
};

describe("DollarGreeksByMaturity", () => {
  test("lays Greeks as columns and delta bands as rows, with raw + currency pairs", () => {
    render(<DollarGreeksByMaturity maturities={ANALYTICS_AAA.maturities} currency="€" />);
    const table = screen.getByRole("table", { name: /Dollar Greeks — / });
    // Each Greek is a column group with a (raw | currency-value) pair.
    for (const greek of ["delta", "gamma", "vega", "theta", "rho"]) {
      expect(within(table).getByRole("columnheader", { name: greek })).toBeInTheDocument();
    }
    // Each Greek group has two sub-columns: a "raw" column and a "{currency} value" column.
    expect(within(table).getAllByRole("columnheader", { name: /^raw / }).length).toBe(5);
    expect(within(table).getAllByRole("columnheader", { name: /€ value/ }).length).toBe(5);
    // The delta band is a row header (deltas as ROWS).
    expect(within(table).getByRole("rowheader", { name: /30dp/ })).toBeInTheDocument();
    // Currency unit strings are rendered in € (the index's quote currency), not "$".
    expect(within(table).getByText("€ per 1% move")).toBeInTheDocument();
  });

  test("one maturity in view at a time; the selector switches the table", async () => {
    const user = userEvent.setup();
    render(
      <DollarGreeksByMaturity maturities={[ANALYTICS_AAA.maturities[0], CLEAN_12M]} currency="€" />,
    );
    // First maturity is shown by default.
    expect(screen.getByRole("table", { name: /Dollar Greeks — 3m/ })).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: /Dollar Greeks — 12m/ })).not.toBeInTheDocument();
    // Switching the selector swaps the visible maturity (only ONE in view).
    await user.selectOptions(screen.getByLabelText("Greeks maturity"), "12m (1.000y)");
    expect(screen.getByRole("table", { name: /Dollar Greeks — 12m/ })).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: /Dollar Greeks — 3m/ })).not.toBeInTheDocument();
  });

  test("a railed-slice row is rendered (values intact) and flagged, never dropped or blown", () => {
    render(
      <DollarGreeksByMaturity maturities={ANALYTICS_AAA_DEGENERATE.maturities} currency="€" />,
    );
    const table = screen.getByRole("table", { name: /Dollar Greeks — 10d/ });
    // All three rows render — the railed rows are NOT dropped (the served datum stays visible).
    expect(within(table).getByRole("rowheader", { name: /30dp/ })).toBeInTheDocument();
    expect(within(table).getByRole("rowheader", { name: /14dp/ })).toBeInTheDocument();
    expect(within(table).getByRole("rowheader", { name: /12dp/ })).toBeInTheDocument();
    // The two railed rows carry the ⚠ flag; the good row does not.
    const railed = within(table).getByRole("row", { name: /14dp/ });
    expect(railed).toHaveClass("flagged-row");
    expect(within(railed).getByTitle(/railed slice/i)).toBeInTheDocument();
    const good = within(table).getByRole("row", { name: /30dp/ });
    expect(good).not.toHaveClass("flagged-row");
    // The railed row's served dollar value is still rendered intact (the data is shown, just
    // flagged): the 14dp delta-$ is -2700 → "−2.7 × 10³".
    expect(within(railed).getByText(/2\.7 × 10³/)).toBeInTheDocument();
  });
});
