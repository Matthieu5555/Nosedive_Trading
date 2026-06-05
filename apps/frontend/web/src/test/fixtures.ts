// Hand-built API fixtures for component tests. Values are chosen independently (not copied
// from the backend's output) so a test asserts the page renders the contract faithfully.

import type { HealthResponse, RiskResponse, SurfaceResponse } from "../api";

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
      diagnostics: { rmse: 0.0009, n_points: 11, arb_free: true },
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
      diagnostics: { rmse: 0.0011, n_points: 9, arb_free: false },
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
