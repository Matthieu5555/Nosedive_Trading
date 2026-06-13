// Typed client for the BFF. Response shapes mirror
// apps/frontend/src/algotrading/frontend/serializers.py; keep them in sync when a
// serializer changes (the HTTP contract is the seam).

import type { BasketScenariosResponse } from "./stressApi";

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
  // bound_hits/converged are null on rows persisted before the degeneracy fields existed
  // (unknown, not clean). degenerate applies the backend policy: a railed, non-converged,
  // or arb-breached calibration is flagged, never served as clean.
  diagnostics: {
    rmse: number;
    n_points: number;
    arb_free: boolean;
    bound_hits: string[] | null;
    converged: boolean | null;
  };
  degenerate: boolean;
  degenerate_reasons: string[];
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

export interface PriceHistoryBatchResponse {
  underlyings: string[];
  start: string | null;
  end: string;
  n_underlyings: number;
  n_loaded: number;
  n_empty: number;
  n_bars: number;
  histories: PriceHistoryResponse[];
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

// The smile x-axis declares what it is (F-BFF-04): the rich projection serves signed target
// deltas; the surface-grid fallback serves moneyness buckets (in log-moneyness units) and
// must never relabel them as deltas.
export type SmileAxis =
  | { axis_type: "delta"; deltas: number[]; implied_vols: number[]; log_moneyness: number[] }
  | {
      axis_type: "moneyness";
      moneyness_buckets: number[];
      implied_vols: number[];
      log_moneyness: number[];
    };

export interface AnalyticsMaturity {
  maturity_years: number;
  tenor_label: string;
  label: string;
  smile: SmileAxis;
  surface_slice: SurfaceSlice | null;
  points: AnalyticsPoint[];
}

export interface AnalyticsResponse {
  underlying: string;
  trade_date: string | null;
  n_maturities: number;
  maturities: AnalyticsMaturity[];
}

// One enabled index from the registry (GET /api/indices). The selector is driven by this —
// never a hard-coded list — so it can only ever offer indices the backend actually captures.
export interface IndexOption {
  symbol: string;
  name: string;
}

export interface IndicesResponse {
  indices: IndexOption[];
}

export type QcVerdict = "pass" | "fail" | "unknown";

// A trade date the page can show, with its QC verdict. ``available`` includes qc-failing days
// (shown with a fail badge), not just the clean ones in ``dates`` (cahier des charges §3.1/§5).
export interface AvailableDate {
  date: string;
  qc: QcVerdict;
}

export interface RecordedDatesResponse {
  index: string;
  count: number;
  dates: string[];
  // Optional only for resilience during a rolling BFF restart (an older BFF omits it); the
  // current BFF always returns it. Callers guard with ``?? []``.
  available?: AvailableDate[];
}

// --- WS 2A: multi-leg basket builder -------------------------------------------------
// Mirrors apps/frontend/src/algotrading/frontend/serializers.py::basket_risk_to_dict and the
// /api/basket/risk router body. The HTTP shape is the seam — keep both sides in lockstep.

export type InstrumentKind = "option" | "stock";
export type LegSide = "long" | "short";

// One leg the operator composes (the request shape). For an option leg, tenor_label + delta_band
// name the WS-1F grid cell; a stock leg omits them. quantity is signed by side (long > 0, short < 0).
export interface BasketLegInput {
  instrument_kind: InstrumentKind;
  side: LegSide;
  quantity: number;
  underlying: string;
  tenor_label?: string | null;
  delta_band?: string | null;
}

export interface BasketRequest {
  basket_id: string;
  trade_date: string;
  underlying: string;
  provider?: string | null;
  legs: BasketLegInput[];
}

// One aggregate basket dollar Greek: the summed dollar value and the unit it is quoted in.
// dollar is null when the Greek is unavailable (an additive-nullable theta/rho missing on a leg).
export interface BasketMetric {
  dollar: number | null;
  unit: string | null;
}

interface BasketGreekMetrics {
  delta: BasketMetric;
  gamma: BasketMetric;
  vega: BasketMetric;
  theta: BasketMetric;
  rho: BasketMetric;
}

// One leg's signed contribution to each basket Greek, beside its matched-cell context.
export interface BasketLegResult {
  instrument_kind: InstrumentKind;
  side: LegSide;
  quantity: number;
  underlying: string;
  tenor_label: string | null;
  delta_band: string | null;
  resolved: boolean;
  gap_reason: string | null;
  forward_price: number | null;
  implied_vol: number | null;
  log_moneyness: number | null;
  strike: number | null;
  price: number | null;
  metrics: BasketGreekMetrics;
}

export interface BasketGap {
  underlying: string;
  tenor_label: string | null;
  delta_band: string | null;
  reason: string;
}

export interface BasketRiskResponse {
  basket_id: string;
  trade_date: string;
  underlying: string;
  price: number | null;
  metrics: BasketGreekMetrics;
  legs: BasketLegResult[];
  gaps: BasketGap[];
  n_legs: number;
  n_gaps: number;
}

// A request that the BFF never answers must not wedge a panel forever; abort past this.
export const FETCH_TIMEOUT_MS = 30_000;

// Combine an optional caller signal (cancel-on-unmount) with a timeout, so a fetch is aborted
// by whichever fires first. AbortSignal.any/timeout are standard in every target runtime; guard
// for a test/runtime that stubs an older fetch and lacks them rather than throwing.
function requestSignal(signal?: AbortSignal): AbortSignal | undefined {
  if (typeof AbortSignal === "undefined" || typeof AbortSignal.timeout !== "function") {
    return signal;
  }
  const timeout = AbortSignal.timeout(FETCH_TIMEOUT_MS);
  if (!signal) return timeout;
  return typeof AbortSignal.any === "function" ? AbortSignal.any([signal, timeout]) : signal;
}

// A non-2xx BFF response, carrying the typed error detail the BFF deliberately serves (the
// 400 `detail` of a malformed basket, a labelled error body) instead of a bare status line.
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(`${status} ${detail}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

// The most useful human-readable detail a failed response offers: the BFF's typed `detail`
// field when present, otherwise the whole JSON error body, otherwise the bare status text.
function errorDetail(payload: unknown, statusText: string): string {
  if (payload !== null && typeof payload === "object" && "detail" in payload) {
    return String((payload as { detail: unknown }).detail);
  }
  if (payload !== null) return JSON.stringify(payload);
  return statusText;
}

// The one fetch path every call goes through, so error handling cannot diverge per verb: a
// non-OK response throws a typed ApiError carrying the status and the BFF's labelled detail.
async function requestJson<T>(path: string, init: RequestInit, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, { ...init, signal: requestSignal(signal) });
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(response.status, errorDetail(payload, response.statusText));
  }
  return payload as T;
}

// GET a BFF path. An optional `signal` lets the caller (the data hook) cancel an in-flight
// request on unmount.
export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  return requestJson<T>(path, {}, signal);
}

export async function postJson<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  return requestJson<T>(
    path,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
    signal,
  );
}

// Price a composed basket. A labelled gap comes back inside a 200 payload (gaps[]); a malformed
// basket is a 400 whose labelled detail the ApiError surfaces, not a bare status line.
export async function priceBasket(body: BasketRequest): Promise<BasketRiskResponse> {
  return postJson<BasketRiskResponse>("/api/basket/risk", body);
}

// Stress a composed basket on demand: the full-reprice (spot × vol) surface plus the worst-case
// cell and labelled per-leg gaps. Same 400-detail handling as priceBasket.
export async function stressBasket(body: BasketRequest): Promise<BasketScenariosResponse> {
  return postJson<BasketScenariosResponse>("/api/basket/scenarios", body);
}
