import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MarketPage } from "./Market";

const underlyings = {
  underlyings: [{ symbol: "SPX", name: "S&P 500 Index", asset_class: "index", currency: "USD" }],
};

const market = {
  underlying: underlyings.underlyings[0],
  index_snapshot: {
    symbol: "SPX",
    name: "S&P 500 Index",
    last: 5312.42,
    bid: 5311.9,
    ask: 5312.8,
    change_percent: 0.42,
    volume: 1840000,
    snapshot_ts: "2026-06-05T12:30:00Z",
    currency: "USD",
  },
  stock_snapshots: [
    {
      symbol: "AAPL",
      name: "Apple",
      last: 196.45,
      bid: 196.42,
      ask: 196.49,
      change_percent: 0.58,
      volume: 46210000,
      snapshot_ts: "2026-06-05T12:30:00Z",
      currency: "USD",
    },
  ],
  option_chain: [
    {
      contract_key: "SPX|2026-06-19|5350|CALL",
      underlying: "SPX",
      expiry: "2026-06-19",
      strike: 5350,
      option_type: "call",
      bid: 47.1,
      ask: 47.9,
      mid: 47.5,
      implied_vol: 0.18,
      open_interest: 6000,
      volume: 400,
      greeks: { delta: 0.48, gamma: 0.003, vega: 8.2, theta: -1.2, rho: 0.7 },
    },
  ],
  greek_totals: { delta: 0.48, gamma: 0.003, vega: 8.2, theta: -1.2, rho: 0.7 },
  volatility_surface: {
    underlying: "SPX",
    as_of: "2026-06-05T12:30:00Z",
    slices: [
      {
        maturity_years: 0.04,
        expiry: "2026-06-19",
        atm_vol: 0.165,
        skew_25_delta: -0.045,
        svi_a: 0.01,
        svi_b: 0.125,
        svi_rho: -0.42,
        svi_m: -0.015,
        svi_sigma: 0.18,
        rmse: 0.0019,
        n_points: 24,
      },
    ],
    points: [
      { log_moneyness: -0.09, maturity_years: 0.04, implied_vol: 0.19, total_variance: 0.0014 },
      { log_moneyness: 0, maturity_years: 0.04, implied_vol: 0.165, total_variance: 0.0011 },
    ],
  },
  provenance: {
    as_of: "2026-06-05T12:30:00Z",
    provider: "fixture",
    code_version: "m8-contract-fixture",
    config_hash: "bff-contract-v1",
    source: "market:SPX",
    stamp_hash: "abc123",
  },
};

describe("MarketPage", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn((path: string) =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve(path.includes("underlyings") ? underlyings : market),
        }),
      ),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders snapshots, option quotes, greeks, and volatility surface", async () => {
    render(<MarketPage />);

    expect(await screen.findByRole("heading", { name: "SPX" })).toBeInTheDocument();
    expect(screen.getByText("AAPL")).toBeInTheDocument();
    expect(screen.getByText("Options bid / ask")).toBeInTheDocument();
    expect(screen.getByText("Volatility surface")).toBeInTheDocument();
    expect(screen.getAllByText("Vega").length).toBeGreaterThan(0);
  });
});
