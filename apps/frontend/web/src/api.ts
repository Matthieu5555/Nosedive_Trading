// Typed client for the BFF. Response shapes mirror
// apps/frontend/src/algotrading/frontend/serializers.py; keep them in sync when a
// serializer changes (the HTTP contract is the seam).

export interface Provenance {
  calc_ts: string;
  code_version: string;
  config_hash: string;
  stamp_hash: string;
  n_sources: number;
}

export interface SurfaceSlice {
  snapshot_ts: string;
  underlying: string;
  maturity_years: number;
  model_version: string;
  svi_a: number;
  svi_b: number;
  svi_rho: number;
  svi_m: number;
  svi_sigma: number;
  expiry_date: string;
  day_count: string;
  diagnostics: { rmse: number; n_points: number; arb_free: boolean };
  source_snapshot_ts: string;
  provenance: Provenance;
}

export interface SurfaceResponse {
  underlying: string;
  trade_date: string | null;
  n_slices: number;
  slices: SurfaceSlice[];
}

export interface RiskAggregate {
  valuation_ts: string;
  portfolio_id: string;
  group_key: string;
  net_delta: number;
  net_gamma: number;
  net_vega: number;
  net_theta: number;
  source_snapshot_ts: string;
  provenance: Provenance;
}

export interface RiskResponse {
  portfolio_id: string | null;
  n_aggregates: number;
  aggregates: RiskAggregate[];
}

export interface HealthResponse {
  trade_date: string;
  data_flowing: string;
  surfaces_building: string;
  qc_status: string;
  scenarios_current: string;
  events_total: number;
  last_healthy_trade_date: string | null;
  backlog: string[];
  is_healthy: boolean;
}

export interface Provider {
  provider: string;
  asset_class: string;
  auth_required: boolean;
  data_latency: string;
  status: string;
  note: string;
}

export interface ProvidersResponse {
  providers: Provider[];
}

export interface Job {
  job_id: string;
  provider: string;
  underlying: string;
  state: "queued" | "running" | "done" | "error";
  started_at: string | null;
  finished_at: string | null;
  message: string;
  summary: Record<string, unknown>;
}

// --- WS 1I front-page seams: price-history, constituents, analytics, recorded-dates ---
// Each interface mirrors a serializer in
// apps/frontend/src/algotrading/frontend/serializers.py / the matching router.

export interface DailyBar {
  provider: string;
  underlying: string;
  trade_date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  bar_type: string;
  source: string;
  provenance: Provenance;
}

export interface PriceHistoryResponse {
  underlying: string;
  start: string | null;
  end: string | null;
  n_bars: number;
  bars: DailyBar[];
}

export interface Constituent {
  instrument_key: string;
  symbol: string;
  weight: number | null;
  effective_add_date: string | null;
  effective_remove_date: string | null;
  latest_close: number | null;
}

export interface ConstituentsResponse {
  index: string;
  as_of: string;
  n_constituents: number;
  constituents: Constituent[];
}

// One dollar metric: the raw per-unit Greek, the dollar value, and the unit string it is
// quoted in (P0.2 / ADR 0036). dollar/unit are null on an older partition that predates them.
export interface DollarMetric {
  raw: number;
  dollar: number | null;
  unit: string | null;
}

export interface AnalyticsPoint {
  delta_band: string;
  target_delta: number;
  log_moneyness: number;
  strike: number;
  forward_price: number;
  implied_vol: number;
  total_variance: number;
  price: number;
  metrics: {
    delta: DollarMetric;
    gamma: DollarMetric;
    vega: DollarMetric;
    theta: DollarMetric;
    rho: DollarMetric;
  };
  provenance: Provenance;
}

export interface AnalyticsMaturity {
  maturity_years: number;
  tenor_label: string;
  label: string;
  smile: {
    deltas: number[];
    implied_vols: number[];
    log_moneyness: number[];
  };
  surface_slice: SurfaceSlice | null;
  points: AnalyticsPoint[];
}

export interface AnalyticsResponse {
  underlying: string;
  trade_date: string | null;
  n_maturities: number;
  maturities: AnalyticsMaturity[];
}

export interface RecordedDatesResponse {
  index: string;
  count: number;
  dates: string[];
}

// One narrow fetch helper: every page goes through here so error handling is uniform.
export async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) {
    let detail = "";
    try {
      detail = JSON.stringify(await response.json());
    } catch {
      detail = response.statusText;
    }
    throw new Error(`${response.status} ${detail}`);
  }
  return (await response.json()) as T;
}

export async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}
