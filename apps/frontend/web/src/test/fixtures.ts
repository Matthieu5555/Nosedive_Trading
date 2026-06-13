// Hand-built API fixtures for component tests. Values are chosen independently (not copied
// from the backend's output) so a test asserts the page renders the contract faithfully.

import type {
  AnalyticsResponse,
  BasketRiskResponse,
  ConstituentsResponse,
  IndicesResponse,
  PriceHistoryBatchResponse,
  HealthResponse,
  PriceHistoryResponse,
  RecordedDatesResponse,
  RiskResponse,
  SurfaceResponse,
} from "../api";
import type { ScenariosResponse } from "../stressApi";

const PROV = {
  calc_ts: "2026-05-29T15:31:00+00:00",
  code_version: "abc123",
  config_hash: "cfg-9",
  stamp_hash: "stamp-x",
  n_sources: 4,
};

export const SURFACE_TWO_SLICES: SurfaceResponse = {
  underlying: "AAPL",
  trade_date: "2026-06-01",
  n_slices: 2,
  slices: [
    {
      snapshot_ts: "2026-06-01T13:30:00+00:00",
      underlying: "AAPL",
      maturity_years: 0.25,
      model_version: "svi-1",
      svi_a: 0.012,
      svi_b: 0.34,
      svi_rho: -0.21,
      svi_m: 0.0,
      svi_sigma: 0.18,
      expiry_date: "2026-08-31",
      day_count: "ACT/365",
      diagnostics: { rmse: 0.0009, n_points: 11, arb_free: true, bound_hits: [], converged: true },
      degenerate: false,
      degenerate_reasons: [],
      source_snapshot_ts: "2026-06-01T13:30:00+00:00",
      provenance: {
        calc_ts: "2026-06-01T13:31:00+00:00",
        code_version: "abc123",
        config_hash: "cfg-9",
        stamp_hash: "stamp-1",
        n_sources: 11,
      },
    },
    {
      snapshot_ts: "2026-06-01T13:30:00+00:00",
      underlying: "AAPL",
      maturity_years: 0.75,
      model_version: "svi-1",
      svi_a: 0.02,
      svi_b: 0.41,
      svi_rho: -0.18,
      svi_m: 0.01,
      svi_sigma: 0.22,
      expiry_date: "2027-03-01",
      day_count: "ACT/365",
      diagnostics: { rmse: 0.0011, n_points: 9, arb_free: false, bound_hits: [], converged: true },
      degenerate: true,
      degenerate_reasons: ["calendar_arbitrage"],
      source_snapshot_ts: "2026-06-01T13:30:00+00:00",
      provenance: {
        calc_ts: "2026-06-01T13:31:00+00:00",
        code_version: "abc123",
        config_hash: "cfg-9",
        stamp_hash: "stamp-2",
        n_sources: 9,
      },
    },
  ],
};

export const SURFACE_EMPTY: SurfaceResponse = {
  underlying: "ZZZZ",
  trade_date: null,
  n_slices: 0,
  slices: [],
};

export const RISK_TWO_GROUPS: RiskResponse = {
  portfolio_id: null,
  n_aggregates: 2,
  aggregates: [
    {
      valuation_ts: "2026-06-01T13:30:00+00:00",
      portfolio_id: "pf-1",
      group_key: "AAPL",
      net_delta: 123.45,
      net_gamma: 6.7,
      net_vega: 89.0,
      net_theta: -12.3,
      source_snapshot_ts: "2026-06-01T13:30:00+00:00",
      provenance: {
        calc_ts: "2026-06-01T13:31:00+00:00",
        code_version: "abc123",
        config_hash: "cfg-9",
        stamp_hash: "stamp-r1",
        n_sources: 4,
      },
    },
    {
      valuation_ts: "2026-06-01T13:30:00+00:00",
      portfolio_id: "pf-1",
      group_key: "MSFT",
      net_delta: -50.0,
      net_gamma: 2.2,
      net_vega: 30.5,
      net_theta: -4.0,
      source_snapshot_ts: "2026-06-01T13:30:00+00:00",
      provenance: {
        calc_ts: "2026-06-01T13:31:00+00:00",
        code_version: "abc123",
        config_hash: "cfg-9",
        stamp_hash: "stamp-r2",
        n_sources: 3,
      },
    },
  ],
};

export const HEALTH_HEALTHY: HealthResponse = {
  trade_date: "2026-06-01",
  data_flowing: "ok",
  surfaces_building: "ok",
  qc_status: "passing",
  scenarios_current: "current",
  events_total: 810,
  last_healthy_trade_date: "2026-06-01",
  backlog: [],
  is_healthy: true,
};

export const HEALTH_DEGRADED: HealthResponse = {
  trade_date: "2026-06-02",
  data_flowing: "no_data",
  surfaces_building: "missing",
  qc_status: "unknown",
  scenarios_current: "stale",
  events_total: 0,
  last_healthy_trade_date: "2026-06-01",
  backlog: ["analytics", "qc"],
  is_healthy: false,
};

// --- WS 1I front-page fixtures (values chosen independently of the backend output) ---

// The enabled-index set the selector is driven by (GET /api/indices). SPX leads so the page's
// default index matches the SPX-centric recorded/analytics fixtures below.
export const INDICES_SPX_SX5E: IndicesResponse = {
  indices: [
    { symbol: "SPX", name: "S&P 500" },
    { symbol: "SX5E", name: "EURO STOXX 50" },
  ],
};

export const RECORDED_TWO_DATES: RecordedDatesResponse = {
  index: "SPX",
  count: 2,
  dates: ["2026-05-29", "2026-05-28"],
  available: [
    { date: "2026-05-29", qc: "pass" },
    { date: "2026-05-28", qc: "pass" },
  ],
};

export const RECORDED_EMPTY: RecordedDatesResponse = {
  index: "SPX",
  count: 0,
  dates: [],
  available: [],
};

export const CONSTITUENTS_TWO: ConstituentsResponse = {
  index: "SPX",
  as_of: "2026-05-29",
  n_constituents: 2,
  // Already price-first: AAA (close 192) before BBB (close 45.5).
  constituents: [
    {
      instrument_key: "AAA",
      symbol: "AAA",
      weight: 0.6,
      effective_add_date: "2026-01-01",
      effective_remove_date: null,
      latest_close: 192.0,
    },
    {
      instrument_key: "BBB",
      symbol: "BBB",
      weight: 0.4,
      effective_add_date: "2026-01-01",
      effective_remove_date: null,
      latest_close: 45.5,
    },
  ],
};

export const PRICE_HISTORY_AAA: PriceHistoryResponse = {
  underlying: "AAA",
  start: null,
  end: "2026-05-29",
  n_bars: 2,
  bars: [
    {
      provider: "IBKR",
      underlying: "AAA",
      trade_date: "2026-05-28",
      open: 188.0,
      high: 191.0,
      low: 187.0,
      close: 190.0,
      volume: 1000000.0,
      bar_type: "1d-TRADES",
      source: "test",
      provenance: PROV,
    },
    {
      provider: "IBKR",
      underlying: "AAA",
      trade_date: "2026-05-29",
      open: 190.0,
      high: 193.5,
      low: 189.5,
      close: 192.0,
      volume: 1200000.0,
      bar_type: "1d-TRADES",
      source: "test",
      provenance: PROV,
    },
  ],
};

export const PRICE_HISTORY_BBB: PriceHistoryResponse = {
  underlying: "BBB",
  start: null,
  end: "2026-05-29",
  n_bars: 2,
  bars: [
    {
      provider: "IBKR",
      underlying: "BBB",
      trade_date: "2026-05-28",
      open: 44.0,
      high: 46.0,
      low: 43.5,
      close: 45.0,
      volume: 500000.0,
      bar_type: "1d-TRADES",
      source: "test",
      provenance: PROV,
    },
    {
      provider: "IBKR",
      underlying: "BBB",
      trade_date: "2026-05-29",
      open: 45.0,
      high: 46.2,
      low: 44.8,
      close: 45.5,
      volume: 600000.0,
      bar_type: "1d-TRADES",
      source: "test",
      provenance: PROV,
    },
  ],
};

export const PRICE_HISTORY_BATCH_TWO: PriceHistoryBatchResponse = {
  underlyings: ["AAA", "BBB"],
  start: null,
  end: "2026-05-29",
  n_underlyings: 2,
  n_loaded: 2,
  n_empty: 0,
  n_bars: 4,
  histories: [PRICE_HISTORY_AAA, PRICE_HISTORY_BBB],
};

export const ANALYTICS_AAA: AnalyticsResponse = {
  underlying: "AAA",
  trade_date: "2026-05-29",
  n_maturities: 1,
  maturities: [
    {
      maturity_years: 0.25,
      tenor_label: "3m",
      label: "3m (0.250y)",
      smile: {
        axis_type: "delta",
        deltas: [-0.3, 0.3],
        implied_vols: [0.27, 0.23],
        log_moneyness: [-0.15, 0.12],
      },
      surface_slice: null,
      points: [
        {
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
        },
      ],
    },
  ],
};

// The surface-grid fallback day (no projected analytics yet): the smile axis is moneyness
// buckets, declared as such (F-BFF-04), and the attached SVI slice is a degenerate
// calibration (rho railed to its bound, not converged, arb breached — the live shape).
export const ANALYTICS_AAA_MONEYNESS_FALLBACK: AnalyticsResponse = {
  underlying: "AAA",
  trade_date: "2026-05-29",
  n_maturities: 1,
  maturities: [
    {
      maturity_years: 0.25,
      tenor_label: "0.250y",
      label: "0.250y",
      smile: {
        axis_type: "moneyness",
        moneyness_buckets: [-0.1, 0.0, 0.1],
        implied_vols: [0.26, 0.24, 0.25],
        log_moneyness: [-0.1, 0.0, 0.1],
      },
      surface_slice: {
        snapshot_ts: "2026-05-29T21:00:00+00:00",
        underlying: "AAA",
        maturity_years: 0.25,
        model_version: "svi-test",
        svi_a: 1e-28,
        svi_b: 0.05,
        svi_rho: -0.999,
        svi_m: 0.0,
        svi_sigma: 0.2,
        expiry_date: "2026-08-29",
        day_count: "ACT/365",
        diagnostics: {
          rmse: 1e-6,
          n_points: 5,
          arb_free: false,
          bound_hits: ["rho_lower"],
          converged: false,
        },
        degenerate: true,
        degenerate_reasons: ["param_at_bound:rho_lower", "not_converged", "butterfly_arbitrage"],
        source_snapshot_ts: "2026-05-29T21:00:00+00:00",
        provenance: PROV,
      },
      points: [],
    },
  ],
};

// A priced long strangle on AAA (WS 2A): long 30Δ call + long 30Δ put. The aggregate dollar
// Greeks are the hand sum of the two legs' contributions (delta cancels, gamma/vega/theta/rho add).
export const BASKET_RISK_AAA: BasketRiskResponse = {
  basket_id: "strangle-AAA",
  trade_date: "2026-05-29",
  underlying: "AAA",
  price: 8.4,
  metrics: {
    delta: { dollar: 0.0, unit: "$ per $1 of underlying" },
    gamma: { dollar: 15.2, unit: "$ per 1% move" },
    vega: { dollar: 0.62, unit: "$ per 1 vol point" },
    theta: { dollar: -0.000082, unit: "$ per calendar day" },
    rho: { dollar: 0.001, unit: "$ per 1% rate" },
  },
  legs: [
    {
      instrument_kind: "option", side: "long", quantity: 1, underlying: "AAA",
      tenor_label: "3m", delta_band: "30dc", resolved: true, gap_reason: null,
      forward_price: 195.0, implied_vol: 0.23, log_moneyness: 0.12, strike: 200.0, price: 4.2,
      metrics: {
        delta: { dollar: 58.5, unit: "$ per $1 of underlying" },
        gamma: { dollar: 7.6, unit: "$ per 1% move" },
        vega: { dollar: 0.31, unit: "$ per 1 vol point" },
        theta: { dollar: -0.000041, unit: "$ per calendar day" },
        rho: { dollar: 0.0005, unit: "$ per 1% rate" },
      },
    },
    {
      instrument_kind: "option", side: "long", quantity: 1, underlying: "AAA",
      tenor_label: "3m", delta_band: "30dp", resolved: true, gap_reason: null,
      forward_price: 195.0, implied_vol: 0.27, log_moneyness: -0.15, strike: 190.0, price: 4.2,
      metrics: {
        delta: { dollar: -58.5, unit: "$ per $1 of underlying" },
        gamma: { dollar: 7.6, unit: "$ per 1% move" },
        vega: { dollar: 0.31, unit: "$ per 1 vol point" },
        theta: { dollar: -0.000041, unit: "$ per calendar day" },
        rho: { dollar: 0.0005, unit: "$ per 1% rate" },
      },
    },
  ],
  gaps: [],
  n_legs: 2,
  n_gaps: 0,
};

// --- Risk-scenarios shell fixtures (shared by the msw default handlers and the page tests) ---

export const PORTFOLIOS_ONE = { portfolios: ["CORE-INDEX-OPTIONS"] };

// No persisted stress surface yet: empty axes, zero cells — the labelled-empty page state.
export const SCENARIOS_EMPTY: ScenariosResponse = {
  portfolio_id: null,
  n_cells: 0,
  surface: {
    spot_shock: [],
    vol_shock: [],
    scenario_pnl: [],
    scenario_version: null,
    unit: "$ (full-reprice PnL)",
    n_cells: 0,
    has_holes: false,
    n_holes: 0,
  },
};
