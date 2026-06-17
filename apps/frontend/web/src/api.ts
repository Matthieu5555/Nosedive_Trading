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

// The maturity selector carries one tenor label, or this sentinel meaning "every captured tenor at
// once" — the natural read of a surface. Panels that are inherently per-tenor (the fitted smile, the
// by-band table) interpret it; the 3D surface and the term-structure curves already span all tenors.
export const ALL_MATURITIES = "All maturities";

// The pinned tenor grid the surface is projected onto (configs/universe.yaml `tenor_grid`, ADR 0011).
// The market page's single tenor selector lists exactly these; a grid tenor the capture didn't reach
// renders as a labelled projection gap, never hidden (blueprint §4.5 "show the gaps"). Kept in
// reading order (near → far). Mirrors the BFF config — keep the two in lockstep.
export const TENOR_GRID = ["10d", "1m", "3m", "6m", "12m", "18m", "2y", "3y"] as const;

export interface AnalyticsPoint {
  delta_band: string;
  target_delta: number;
  log_moneyness: number;
  strike: number;
  forward_price: number;
  implied_vol: number;
  total_variance: number;
  price: number;
  bid?: number | null;
  ask?: number | null;
  volume?: number | null;
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

// --- /api/risk/metrics greeks cell (TARGET §5.1) --------------------------------------------
// Mirrors apps/frontend/src/algotrading/frontend/serializers.py::pricing_result_to_dict and the
// /api/risk/metrics router body. The HTTP shape is the seam — keep both sides in lockstep. Each
// metric is the engine's own raw + already-monetized dollar value plus its unit string; the BFF
// re-derives no Greek and no $-conversion. The second-order set (vanna/volga/charm) is
// additive-nullable: a PricingResult that predates it carries those metrics with null raw/dollar
// but a populated unit string. Charm is a display Greek, never an attribution term.
export interface RiskMetricCell {
  delta: DollarMetric;
  gamma: DollarMetric;
  vega: DollarMetric;
  theta: DollarMetric;
  rho: DollarMetric;
  vanna: DollarMetric;
  volga: DollarMetric;
  charm: DollarMetric;
}

export interface RiskMetricResult {
  snapshot_ts: string;
  contract_key: string;
  pricer_version: string;
  price: number;
  metrics: RiskMetricCell;
  source_snapshot_ts: string;
  provenance: Provenance;
}

export interface RiskMetricsResponse {
  underlying: string | null;
  n_results: number;
  results: RiskMetricResult[];
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

// One capture run (fetch). Re-fetching a trade date adds another entry — same `date`, distinct
// `run_id` and `recorded_ts` — so the selector lists fetches newest-first, never collapsing them.
// `run_id`/`recorded_ts` are null for legacy flat data that predates run-partitioning: there is no
// addressable run= partition, so the date itself is the only handle and the time is unknown.
export interface AvailableDate {
  date: string;
  run_id: string | null;
  recorded_ts: string | null;
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

// --- F-SIG: the persisted strategy signal layer (read-only) ---------------------------------
// Mirrors apps/frontend/src/algotrading/frontend/routers/signals.py + strategy_signal_to_dict.
// The BFF recomputes no signal math; each row is the serialized StrategySignal plus a display
// label/unit keyed off its kind. The HTTP shape is the seam — keep both sides in lockstep.
export type SignalKind =
  | "iv_rank"
  | "iv_vs_realized"
  | "term_structure_slope"
  | "implied_correlation";

export interface Signal {
  signal_kind: string;
  label: string;
  subject: string;
  tenor_label: string;
  value: number;
  unit: string | null;
  snapshot_ts: string;
  source_snapshot_ts: string;
  provenance: Provenance;
}

export interface SignalsResponse {
  underlying: string;
  trade_date: string | null;
  snapshot_ts: string | null;
  n_signals: number;
  kinds: string[];
  by_kind: Record<string, Signal[]>;
  signals: Signal[];
}

export interface SignalUnderlyingsResponse {
  underlyings: string[];
}

// One plain-language sentence per kind: what the number is and how to read it. PM-legible, no
// quant jargon — these front each per-kind panel so a reader needs no prior context.
export const SIGNAL_CAPTIONS: Record<string, string> = {
  iv_rank:
    "Where today's implied vol sits in its 1-year range, 0–100%. High means options look expensive versus the past year.",
  iv_vs_realized:
    "Recently realized vol minus implied vol, in vol points. Positive means the market actually moved more than options were pricing.",
  term_structure_slope:
    "Longer-dated implied vol minus shorter-dated, in vol points. Positive (upward slope) is the calm-market norm; negative flags near-term stress.",
  implied_correlation:
    "Average implied correlation across the index members, −1 to +1. High means names are expected to move together, so the index looks expensive versus its parts.",
};

// --- F-POS: positions / execution blotter (the fills-based book, read-only) ----------------
// Mirrors apps/frontend/src/algotrading/frontend/serializers.py::position_book_to_dict and the
// fills_view ledger projection. The book is accounted FROM fills, never from intentions; the BFF
// recomputes nothing. The HTTP shape is the seam — keep both sides in lockstep.

// One append-only fill in the ledger (the §6 source of record). `signed_qty` is a string so the
// backend Decimal survives JSON intact; `fill_ts` is the venue-stamped instant.
export interface Fill {
  fill_id: string;
  booking_id: string;
  source_basket_id: string;
  trade_date: string;
  underlying: string;
  contract_key: string;
  signed_qty: string;
  price: number;
  fill_ts: string;
  mode: string;
  broker_contract_id: string | null;
}

export interface FillsResponse {
  trade_date: string | null;
  underlying: string | null;
  n_fills: number;
  fills: Fill[];
}

// A per-leg dollar-Greek component on a position line. `raw` is the per-unit Greek, `position` is
// the position-scaled raw (raw × signed_qty × multiplier), `dollar` is the banked dollar Greek for
// the held quantity, each carrying its unit string (the `$` placeholder is re-currencied on render).
export interface PositionGreek {
  raw: number;
  position: number;
  dollar: number;
  unit: string;
}

// One live contract in the booked book — the ledger folded by `contract_key` (net-zero legs are
// closed and absent). A booked leg with no banked pricing carries zeroed Greeks and is listed in
// `unpriced_contract_keys`, never silently dropped.
export interface PositionLine {
  contract_key: string;
  underlying: string;
  strike: number | null;
  expiry: string | null;
  option_right: string | null;
  multiplier: number;
  quantity: number;
  broker_contract_id: string | null;
  mark_price: number;
  market_value: number;
  greeks: {
    delta: PositionGreek;
    gamma: PositionGreek;
    vega: PositionGreek;
    theta: PositionGreek;
    rho: PositionGreek;
  };
}

// The book-additive sum of the per-leg dollar Greeks and market value across priced legs.
export interface BookGreek {
  dollar: number;
  unit: string;
}

export interface BookGreeks {
  delta: BookGreek;
  gamma: BookGreek;
  vega: BookGreek;
  theta: BookGreek;
  rho: BookGreek;
  market_value: number;
}

export interface PositionsResponse {
  source: string;
  source_ts: string;
  n_lines: number;
  lines: PositionLine[];
  book: BookGreeks;
  priced_contract_keys: number;
  unpriced_contract_keys: string[];
}

export const POSITION_GREEK_ORDER = ["delta", "gamma", "vega", "theta", "rho"] as const;
export type PositionGreekName = (typeof POSITION_GREEK_ORDER)[number];

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

export interface PutLineParams {
  put_tenor: string;
  put_delta_band: string;
  line_capacity: number;
  contracts_per_day: number;
  max_rv_minus_iv: number;
  exit_delta_ceiling?: number | null;
}

export interface BacktestCosts {
  commission_per_contract: number;
  slippage_rate: number;
}

export interface StressScenarioInput {
  scenario_id: string;
  spot_shock: number;
  vol_shock: number;
  time_shock: number;
}

export interface BacktestRunRequest {
  index: string;
  reference_tenor: string;
  start_date: string;
  end_date: string;
  provider: string;
  put_line: PutLineParams;
  costs?: BacktestCosts;
  stress_grid?: StressScenarioInput[];
}

export interface BacktestSummary {
  total_pnl: number;
  total_net_pnl: number;
  total_transaction_cost: number;
  max_drawdown: number;
  sharpe: number;
  turnover: number;
  worst_stress_loss: number;
}

export interface BacktestAttribution {
  delta: number;
  gamma: number;
  vega: number;
  theta: number;
  rho: number;
  vanna: number;
  volga: number;
}

export interface BacktestDayGreeks {
  delta: number;
  gamma: number;
  vega: number;
  theta: number;
}

export interface BacktestDay {
  as_of: string;
  open_contracts: number;
  entered: number;
  realized_pnl: number;
  cumulative_pnl: number;
  cumulative_net_pnl: number;
  transaction_cost: number;
  stress_loss: number;
  greeks: BacktestDayGreeks;
}

export interface BacktestResult {
  strategy_id: string;
  summary: BacktestSummary;
  cumulative_attribution: BacktestAttribution;
  days: BacktestDay[];
}

export async function runBacktest(
  body: BacktestRunRequest,
  signal?: AbortSignal,
): Promise<BacktestResult> {
  return postJson<BacktestResult>("/api/backtest/run", body, signal);
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

// --- Broker reconciliation (§7.9): does the broker agree with our book? ---------------------
// Mirrors apps/frontend/src/algotrading/frontend/reconciliation_view.py::reconciliation_report_to_dict
// and the /api/reconciliation router body. The BFF runs infra.risk.reconcile_account; the front
// only displays the diff. The HTTP shape is the seam — keep both sides in lockstep.

export type ReconStatus = "match" | "break" | "broker_only" | "book_only";

// Per-status line counts for one section (positions / cash / fills).
export interface ReconCounts {
  match: number;
  break: number;
  broker_only: number;
  book_only: number;
}

export interface ReconPositionLine {
  join_key: string;
  broker_contract_key: string | null;
  book_contract_key: string | null;
  broker_quantity: number | null;
  book_quantity: number | null;
  quantity_diff: number | null;
  status: ReconStatus;
  threshold: number | null;
  threshold_version: string;
}

export interface ReconCashLine {
  currency: string;
  broker_cash_balance: number | null;
  broker_settled_cash: number | null;
  broker_net_liquidation: number | null;
  status: ReconStatus;
  threshold_version: string;
}

export interface ReconFillLine {
  join_key: string;
  broker_contract_key: string | null;
  book_contract_key: string | null;
  broker_signed_quantity: number | null;
  book_signed_quantity: number | null;
  quantity_diff: number | null;
  status: ReconStatus;
  threshold: number | null;
  threshold_version: string;
}

export interface ReconSection<L> {
  counts: ReconCounts;
  n_lines: number;
  lines: L[];
}

// The /api/reconciliation body. `ok` is true when nothing breaks across all three sections.
export interface ReconciliationResponse {
  account_id: string;
  as_of_ts: string;
  book_source: string;
  book_source_ts: string;
  threshold_version: string;
  ok: boolean;
  positions: ReconSection<ReconPositionLine>;
  cash: ReconSection<ReconCashLine>;
  fills: ReconSection<ReconFillLine>;
}

// Fetch the broker-vs-book reconciliation. `account_id` absent resolves the account on the latest
// banked broker positions; no broker positions captured is a labelled 400 the ApiError surfaces.
export async function fetchReconciliation(
  accountId?: string,
  signal?: AbortSignal,
): Promise<ReconciliationResponse> {
  const suffix = accountId ? `?account_id=${encodeURIComponent(accountId)}` : "";
  return getJson<ReconciliationResponse>(`/api/reconciliation${suffix}`, signal);
}
