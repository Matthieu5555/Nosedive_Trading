import type { components } from "./api/schema";
import type { BasketScenariosResponse } from "./stressApi";

export type RunRequest = components["schemas"]["RunRequest"];

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

export interface DollarMetric {
  raw: number;
  dollar: number | null;
  unit: string | null;
}

// Which half of the smile the page is focused on. The persistent put/call switch in the market
// selector strip carries this; every surface/smile/Greeks panel reads it the same way — puts are
// the downside wing (log-moneyness ≤ 0, signed delta ≤ 0), calls the upside wing (≥ 0). ATM (k = 0)
// belongs to both, so it is never filtered out.
export type OptionSide = "put" | "call";

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

export interface SurfaceDense {
  log_moneyness: number[];
  maturity_years: number[];
  implied_vol: number[][];
  model_version: string;
  degenerate_maturity_years: number[];
}

export interface AnalyticsResponse {
  underlying: string;
  trade_date: string | null;
  n_maturities: number;
  maturities: AnalyticsMaturity[];
  surface: SurfaceDense | null;
}

export interface IndexOption {
  symbol: string;
  name: string;

  currency: string;
}

export interface IndicesResponse {
  indices: IndexOption[];
}

export interface DeltaBandsResponse {
  delta_bands: string[];
}

export type QcVerdict = "pass" | "fail" | "unknown";

export interface AvailableDate {
  date: string;
  qc: QcVerdict;
}

export interface RecordedDatesResponse {
  index: string;
  count: number;
  dates: string[];

  available?: AvailableDate[];
}

export type InstrumentKind = "option" | "stock";
export type LegSide = "long" | "short";

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

export const FETCH_TIMEOUT_MS = 30_000;

function requestSignal(signal?: AbortSignal): AbortSignal | undefined {
  if (typeof AbortSignal === "undefined" || typeof AbortSignal.timeout !== "function") {
    return signal;
  }
  const timeout = AbortSignal.timeout(FETCH_TIMEOUT_MS);
  if (!signal) return timeout;
  return typeof AbortSignal.any === "function" ? AbortSignal.any([signal, timeout]) : signal;
}

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

// --- WS 3A: order ticket — build + preview from a composed basket (preview-only, paper) -------
// The ticket mirrors the basket's leg identity (grid cell for options, underlying for stock) and
// maps the basket side long/short -> order side buy/sell. NOTHING transmits: the response carries
// an explicit `gated` flag stating sign-and-send is WS 3B behind an owner gate. The HTTP shape is
// the seam — these interfaces stay in lockstep with ticket_to_dict on the BFF.
export type OrderSide = "buy" | "sell";

export interface TicketPriceSpec {
  kind: "market" | "limit";
  price?: number | null;
}

export interface TicketPreviewRequest {
  basket_id: string;
  underlying: string;
  trade_date: string;
  target_broker: string;
  time_in_force: string;
  price_spec: TicketPriceSpec;
  legs: BasketLegInput[];
}

export interface OrderTicketLeg {
  instrument_kind: InstrumentKind;
  underlying: string;
  side: OrderSide;
  quantity: number;
  price_spec: TicketPriceSpec;
  tenor_label: string | null;
  delta_band: string | null;
}

export interface OrderTicketGate {
  transmit: boolean;
  reason: string;
}

export interface OrderTicketResponse {
  source_basket_id: string;
  trade_date: string;
  underlying: string;
  target_broker: string;
  time_in_force: string;
  mode: string;
  legs: OrderTicketLeg[];
  n_legs: number;
  gated: OrderTicketGate;
}

// Build + preview an order ticket from a composed basket (3A). A malformed request is a 400 whose
// labelled detail the ApiError surfaces; nothing is transmitted (sign-and-send is 3B).
export async function previewTicket(body: TicketPreviewRequest): Promise<OrderTicketResponse> {
  return postJson<OrderTicketResponse>("/api/ticket/preview", body);
}

// The selectable broker / time-in-force values, derived server-side from the
// `TargetBroker` / `TimeInForce` enums. The Ticket panel populates its selectors from this so it
// never hardcodes a parallel list that could drift from the backend.
export interface TicketOptions {
  brokers: string[];
  time_in_force: string[];
}

export async function getTicketOptions(): Promise<TicketOptions> {
  return getJson<TicketOptions>("/api/ticket/options");
}

// The password-gated booking commit (§7 #1): turn a previewed ticket into paper fill(s), but ONLY
// behind the password write barrier. The request is the ticket-preview body plus a `password`; the
// response is a decision — "commit" (fills written) or "block" (fail-closed, no fill, a labelled
// reason). A block is HTTP 200 (a normal answer); a malformed request is a labelled 400. This is
// the *paper* booking gate, NOT the 3B broker-send gate — nothing here transmits to a broker.
export interface BookingCommitRequest extends TicketPreviewRequest {
  password: string;
}

export interface BookingCommitResponse {
  decision: "commit" | "block";
  booking_id: string;
  // Present on a commit:
  fill_ids?: string[];
  fill_count?: number;
  // Present on a block:
  reason?: string;
  detail?: string;
}

export async function commitBooking(body: BookingCommitRequest): Promise<BookingCommitResponse> {
  return postJson<BookingCommitResponse>("/api/booking/commit", body);
}

// --- P&L attribution waterfall (TARGET §2 #5 / §7 #2) --------------------------------------
// Mirrors apps/frontend/src/algotrading/frontend/serializers.py::scenario_attribution_to_dict and
// the /api/attribution router body. The HTTP shape is the seam — keep both sides in lockstep. The
// BFF re-decomposes nothing; every `dollars` is the engine's own ScenarioAttribution term.

// One named by-Greek contribution: an already-monetized dollar PnL amount and its unit string.
// `dollars` is null only on the labelled-empty body (no record for the selection).
export interface AttributionTerm {
  name: string;
  dollars: number | null;
  unit: string;
}

// The residual against the full reprice — the honesty meter (§5.2). Its own bar, never folded.
export interface AttributionResidual {
  dollars: number | null;
  unit: string;
}

// The engine's tolerance ruling: within_tolerance against the echoed abs/rel bounds. Null on the
// labelled-empty body (no record was judged).
export interface AttributionVerdict {
  within_tolerance: boolean;
  residual_abs_tol: number;
  residual_rel_tol: number;
}

// The /api/attribution body. `found=false` is the labelled-empty case (terms []), HTTP 200.
export interface AttributionResponse {
  trade_date: string | null;
  portfolio_id: string | null;
  level: string;
  contract_key: string | null;
  found: boolean;
  terms: AttributionTerm[];
  residual: AttributionResidual;
  verdict: AttributionVerdict | null;
  approx_pnl?: number;
  full_reprice_pnl?: number;
  scenario_version?: string;
  attribution_version?: string;
  provenance?: Provenance;
}

export interface AttributionQuery {
  tradeDate?: string;
  portfolioId?: string;
  level?: "book" | "position";
  contractKey?: string;
}

// Fetch one attribution record's waterfall payload. The book aggregate by default; a position
// drill passes level=position + contractKey (the §5.8 drill target). An unknown (portfolio, date)
// comes back as a labelled-empty 200; a bad trade_date is a labelled 400 surfaced as an Error.
export async function fetchAttribution(
  query: AttributionQuery = {},
  signal?: AbortSignal,
): Promise<AttributionResponse> {
  const params = new URLSearchParams();
  if (query.tradeDate) params.set("trade_date", query.tradeDate);
  if (query.portfolioId) params.set("portfolio_id", query.portfolioId);
  if (query.level) params.set("level", query.level);
  if (query.contractKey) params.set("contract_key", query.contractKey);
  const suffix = params.toString();
  return getJson<AttributionResponse>(`/api/attribution${suffix ? `?${suffix}` : ""}`, signal);
}
