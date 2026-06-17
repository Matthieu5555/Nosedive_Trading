import { render, screen, within } from "@testing-library/react";
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

    for (const greek of ["delta", "gamma", "vega", "theta", "rho"]) {
      expect(within(table).getByRole("columnheader", { name: greek })).toBeInTheDocument();
    }

    expect(within(table).getAllByRole("columnheader", { name: /^raw / }).length).toBe(5);
    expect(within(table).getAllByRole("columnheader", { name: /€ value/ }).length).toBe(5);

    expect(within(table).getByRole("rowheader", { name: /30dp/ })).toBeInTheDocument();

    expect(within(table).getByText("€ per 1% move")).toBeInTheDocument();
  });

  test("the maturity in view is driven by the maturityLabel prop (the shared selector)", () => {
    const maturities = [ANALYTICS_AAA.maturities[0], CLEAN_12M];
    const { rerender } = render(
      <DollarGreeksByMaturity maturities={maturities} maturityLabel="3m (0.250y)" currency="€" />,
    );
    expect(screen.getByRole("table", { name: /Dollar Greeks — 3m/ })).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: /Dollar Greeks — 12m/ })).not.toBeInTheDocument();

    rerender(
      <DollarGreeksByMaturity maturities={maturities} maturityLabel="12m (1.000y)" currency="€" />,
    );
    expect(screen.getByRole("table", { name: /Dollar Greeks — 12m/ })).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: /Dollar Greeks — 3m/ })).not.toBeInTheDocument();
  });

  test("the put/call switch keeps one wing (ATM shared) and highlights the ATM row", () => {
    const threeSided: AnalyticsMaturity = {
      maturity_years: 1.0,
      tenor_label: "12m",
      label: "12m (1.000y)",
      smile: {
        axis_type: "delta",
        deltas: [-0.25, 0.0, 0.25],
        implied_vols: [0.24, 0.21, 0.22],
        log_moneyness: [-0.1, 0.0, 0.1],
      },
      surface_slice: null,
      points: [-0.25, 0.0, 0.25].map((target, i) => ({
        delta_band: ["25dp", "atm", "25dc"][i],
        target_delta: target,
        log_moneyness: [-0.1, 0.0, 0.1][i],
        strike: 175.0,
        forward_price: 195.0,
        implied_vol: [0.24, 0.21, 0.22][i],
        total_variance: 0.05,
        price: 6.0,
        metrics: {
          delta: { raw: target, dollar: target * 100, unit: "$ per $1 of underlying" },
          gamma: { raw: 0.01, dollar: 4.0, unit: "$ per 1% move" },
          vega: { raw: 0.5, dollar: 0.5, unit: "$ per 1 vol point" },
          theta: { raw: -0.01, dollar: -0.00002, unit: "$ per calendar day" },
          rho: { raw: 0.08, dollar: 0.001, unit: "$ per 1% rate" },
        },
        provenance: PROV,
      })),
    };

    render(
      <DollarGreeksByMaturity
        maturities={[threeSided]}
        maturityLabel="12m (1.000y)"
        side="call"
        currency="€"
      />,
    );
    const table = screen.getByRole("table", { name: /Dollar Greeks — 12m/ });

    // Calls + ATM survive; the put band is filtered out.
    expect(within(table).getByRole("rowheader", { name: /atm/i })).toBeInTheDocument();
    expect(within(table).getByRole("rowheader", { name: /25dc/ })).toBeInTheDocument();
    expect(within(table).queryByRole("rowheader", { name: /25dp/ })).not.toBeInTheDocument();

    // The ATM row carries the highlight class (selective, not the whole grid).
    expect(within(table).getByRole("row", { name: /atm/i })).toHaveClass("greeks-row--atm");
  });

  test("a railed-slice row is rendered (values intact) and flagged, never dropped or blown", () => {
    render(
      <DollarGreeksByMaturity maturities={ANALYTICS_AAA_DEGENERATE.maturities} currency="€" />,
    );
    const table = screen.getByRole("table", { name: /Dollar Greeks — 10d/ });

    expect(within(table).getByRole("rowheader", { name: /30dp/ })).toBeInTheDocument();
    expect(within(table).getByRole("rowheader", { name: /14dp/ })).toBeInTheDocument();
    expect(within(table).getByRole("rowheader", { name: /12dp/ })).toBeInTheDocument();

    const railed = within(table).getByRole("row", { name: /14dp/ });
    expect(railed).toHaveClass("flagged-row");
    expect(within(railed).getByTitle(/railed slice/i)).toBeInTheDocument();
    const good = within(table).getByRole("row", { name: /30dp/ });
    expect(good).not.toHaveClass("flagged-row");

    expect(within(railed).getByText(/2\.7 × 10³/)).toBeInTheDocument();
  });

  const WITH_SECOND_ORDER: AnalyticsMaturity = {
    ...CLEAN_12M,
    points: [
      {
        ...CLEAN_12M.points[0],
        metrics: {
          ...CLEAN_12M.points[0].metrics,
          vanna: { raw: 0.39, dollar: 11.7, unit: "$ delta per 1 vol point" },
          volga: { raw: 85.36, dollar: 0.0085, unit: "$ vega per 1 vol point" },
          charm: { raw: -0.037, dollar: -0.0102, unit: "$ delta per calendar day" },
        },
      },
    ],
  };

  test("renders a labelled second-order table (vanna/volga/charm) when the cell carries them", () => {
    render(
      <DollarGreeksByMaturity
        maturities={[WITH_SECOND_ORDER]}
        maturityLabel="12m (1.000y)"
        currency="€"
      />,
    );
    const table = screen.getByRole("table", { name: /Second-order Greeks — 12m/ });
    for (const greek of ["vanna", "volga", "charm"]) {
      expect(within(table).getByRole("columnheader", { name: greek })).toBeInTheDocument();
    }
    expect(within(table).getByRole("rowheader", { name: /30dp/ })).toBeInTheDocument();
    // The served dollar unit string is surfaced, currency-localised like the first-order columns
    // ("$ ..." -> "€ ...").
    expect(within(table).getByText("€ delta per 1 vol point")).toBeInTheDocument();
    // A raw value is rendered in scientific notation (0.39 -> 3.9 × 10⁻¹), not dropped.
    expect(within(table).getByText(/3\.9 × 10⁻¹/)).toBeInTheDocument();
  });

  test("shows an explicit gap note (no fabricated table) when the cell predates the second-order set", () => {
    render(
      <DollarGreeksByMaturity maturities={[CLEAN_12M]} maturityLabel="12m (1.000y)" currency="€" />,
    );
    expect(screen.queryByRole("table", { name: /Second-order Greeks — / })).not.toBeInTheDocument();
    expect(screen.getByText(/not banked for this close/i)).toBeInTheDocument();
  });
});
