import type { components } from "./api/schema";
import type { BasketScenariosResponse } from "./stressApi";

export type RunRequest = components["schemas"]["RunRequest"];

export interface Provenance {
  calc_ts: string;
  code_version: string;
  // The BFF serializes a per-domain map of config digests (pricing/qc/scenarios/universe -> sha),
  // not a single string; see provenance_to_dict in serializers.py. Mirror that exact shape so
  // `provenance.config_hashes` is never silently undefined.
  config_hashes: Record<string, string>;
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
    // The surface-fit error in IV space (vol-point RMSE, e.g. 0.00055 = 0.06 vol pts), and the
    // fraction of quotes the fit treated as outliers. Both are present only on slices the fitter
    // actually fit (a slice with no surface_slice has neither); absent on older reads. Optional so
    // the front degrades to an honest "fit not available" rather than inventing a number.
    iv_rmse?: number | null;
    iv_outlier_fraction?: number | null;
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

// The honest IBKR Client-Portal session state, mirroring routers/ibkr.py::_status_payload. The BFF
// probes /iserver/auth/status WITHOUT ever triggering a browser login; every transport error
// degrades to a labelled state here, never a 500. `configured` is false when no gateway env is set
// (the default offline case); `detail` always carries the next operator step in plain language.
export interface IbkrStatus {
  configured: boolean;
  authenticated: boolean;
  established: boolean;
  competing: boolean;
  account: string | null;
  detail: string;
}

// One capture/recompute run as the BFF job ledger sees it. `stage`/`stage_index`/`stage_total` are
// additive-nullable: the BFF sets them as the engine walks its named stages (resolve → collect → fit
// → summarize); a payload that predates the passthrough, or a job that hasn't reported a stage yet,
// carries all three null and the running row degrades to an honest indeterminate bar (never a
// fabricated percent). `stage` is already the PM-register French label, not the engine enum.
export interface Job {
  job_id: string;
  provider: string;
  underlying: string;
  state: "queued" | "running" | "done" | "error";
  started_at: string | null;
  finished_at: string | null;
  message: string;
  summary: Record<string, unknown>;
  stage?: string | null;
  stage_index?: number | null;
  stage_total?: number | null;
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

// The second-order set (vanna/volga/charm) is additive-nullable on the analytics grid: the
// projection emits it on every freshly-projected cell, but a cell banked before the field existed
// (an older close) serializes the keys with a null raw/dollar. So it carries a nullable raw, unlike
// the always-present first-order DollarMetric.
export interface NullableDollarMetric {
  raw: number | null;
  dollar: number | null;
  unit: string | null;
}

// Which half of the smile the page is focused on. The persistent put/call switch in the market
// selector strip carries this; every surface/smile/Greeks panel reads it the same way — puts are
// the downside wing (log-moneyness ≤ 0, signed delta ≤ 0), calls the upside wing (≥ 0). ATM (k = 0)
// belongs to both, so it is never filtered out.
export type OptionSide = "put" | "call";

// The surface maturity control is a FLOOR, not a single point: it keeps every captured tenor at or
// above the chosen lower bound, so the 3D surface always renders (a surface needs several tenors; a
// single tenor is a 2D smile, which already lives in the Charting studio panel below). This sentinel
// is the "no floor" reading, every captured tenor in view. A floor reads as "min {tenor} and up".
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
  quote?: { bid: number | null; ask: number | null; volume: number | null };
  metrics: {
    delta: DollarMetric;
    gamma: DollarMetric;
    vega: DollarMetric;
    theta: DollarMetric;
    rho: DollarMetric;
    vanna?: NullableDollarMetric;
    volga?: NullableDollarMetric;
    charm?: NullableDollarMetric;
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

// Per-tenor forward + interest-rate diagnostics (core-explicit-rate-config A6). The forward is the
// PCP-observable forward; `implied_rate` is the interest rate the carry split uses (the explicit
// config `rate` when set, else the parity-DF-implied r = −ln(DF)/T), and `implied_carry` /
// `implied_dividend` are the carry split q = r − ln(F/S)/T (TARGET R1, blueprint Eq 5). Rates are
// annualized continuous fractions; `rate_unit` carries the unit string from the BFF. The whole
// object is null when no forward point was banked for the tenor (the surface-grid fallback path),
// and any field is null where the diagnostic itself wasn't computed.
export interface RateDiagnostics {
  forward_price: number | null;
  implied_rate: number | null;
  implied_carry: number | null;
  implied_dividend: number | null;
  rate_unit: string;
}

export interface AnalyticsMaturity {
  maturity_years: number;
  tenor_label: string;
  label: string;
  smile: SmileAxis;
  surface_slice: SurfaceSlice | null;
  rate_diagnostics?: RateDiagnostics | null;
  points: AnalyticsPoint[];
}

export interface SurfaceDense {
  log_moneyness: number[];
  maturity_years: number[];
  // The CLAMPED/holey grid: cells where strikes stop are null, so the RAW (honest) surface shows
  // gaps where coverage runs out. Use this when `filled` is false.
  implied_vol: (number | null)[][];
  // The FILLED, capped-at-0.60 grid with no holes: the classic smooth nappe look. The backend
  // refits from clean cells so it carries no degenerate slices. Use this for the CLEAN view.
  // Additive-nullable: an older payload without it parses, and the chart falls back to implied_vol.
  implied_vol_filled?: (number | null)[][];
  model_version: string;
  degenerate_maturity_years: number[];
}

// The one coverage block (MAT-LEGIBILITY-coverage-headline): how much of the captured option chain
// the surface actually rests on. Computed once in the BFF (grounding.coverage_from_snapshots) and
// shared with the assistant frame — never refabricated on the front. Additive-nullable: a payload
// without it (or with no option rows) parses with `coverage: null`, and the headline says
// "couverture indisponible" rather than vanishing.
export interface AnalyticsCoverage {
  option_rows: number;
  two_sided: number;
  excluded: number;
  two_sided_fraction: number | null;
}

// The three vol-surface sides the captured store carries. Calls and puts have genuinely different
// skew (the call wing and the put wing are quoted separately), so each is its own surface, not a
// re-slice of one combined set; `combined` is the union read the page opens on. The market page's
// Call / Put / Combined selector carries this; every surface/smile/Greeks panel reads the matching
// side's maturities + dense grid.
export type SurfaceSide = "combined" | "call" | "put";

export const SURFACE_SIDE_LABELS: Record<SurfaceSide, string> = {
  combined: "Combined",
  call: "Calls",
  put: "Puts",
};

// The per-side analytics views. Each side carries its own maturities (smile + per-band Greek points)
// and its own dense 3D grid built from those cells. A side the close did not capture is an empty
// maturity list + null dense, so the front degrades to an honest "per-side fit not available for this
// close, showing combined", never a fabricated surface. Additive-nullable: an older payload without
// the per-side block still parses, and the page falls back to the combined `maturities` / `surface`.
export interface AnalyticsSides {
  combined: AnalyticsMaturity[];
  call: AnalyticsMaturity[];
  put: AnalyticsMaturity[];
}

export interface AnalyticsSurfacesBySide {
  combined: SurfaceDense | null;
  call: SurfaceDense | null;
  put: SurfaceDense | null;
}

export interface AnalyticsResponse {
  underlying: string;
  trade_date: string | null;
  // The option settlement close as a PM-legible local instant ("2026-06-17 17:30 CEST"), resolved
  // server-side from the index registry (configs/universe.yaml calendar + option_settlement_close) —
  // the front never hard-codes "17:30 CET". Null/absent when the registry/session is unavailable;
  // additive-nullable so an older payload without it still parses (date-only as-of).
  close_instant?: string | null;
  n_maturities: number;
  // The combined view, kept top-level for backward compatibility (Scorecards / term-structure read
  // it directly). The same data is also `sides.combined` / `surfaces_by_side.combined`.
  maturities: AnalyticsMaturity[];
  surface: SurfaceDense | null;
  // The per-side views (additive). Absent on an older payload; the page falls back to combined.
  sides?: AnalyticsSides;
  surfaces_by_side?: AnalyticsSurfacesBySide;
  // Which sides actually carry captured maturities (e.g. ["call","combined","put"]), so the selector
  // can disable a side the close did not reach rather than offering an empty surface.
  sides_available?: SurfaceSide[];
  coverage?: AnalyticsCoverage | null;
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

// --- Realized day-over-day attribution (TARGET §2 #5) ---------------------------------------
// Mirrors apps/frontend/src/algotrading/frontend/serializers.py + the /api/attribution/realized
// router body. For a held position over a span of dates, this re-reads each day's actual P&L as
// the per-Greek contributions that produced it, day by day. The BFF re-decomposes nothing; every
// `dollars` is the engine's own term. The HTTP shape is the seam — keep both sides in lockstep.

// One day-over-day step: the seven by-Greek contributions for the move from start_date to
// end_date, the approximate P&L they sum to, the full-reprice P&L they approximate, and the
// honest residual between them (its own number, never folded into a term).
export interface RealizedAttributionStep {
  start_date: string;
  end_date: string;
  portfolio_id: string;
  // Seven entries in display order: Delta, Gamma, Vega, Theta, Rho, Vanna, Volga.
  terms: AttributionTerm[];
  approx_pnl: { dollars: number | null; unit: string };
  full_reprice_pnl: { dollars: number | null; unit: string };
  residual: { dollars: number | null; unit: string };
  verdict: {
    within_tolerance: boolean;
    diagnostic: string;
    residual_abs_tol: number;
    residual_rel_tol: number;
  };
  // The market move that drove the day: change in spot, vol, time (years), and rate.
  move: { d_spot: number; d_vol: number; d_time: number; d_rate: number };
}

// The /api/attribution/realized body. `found=false` is the labelled-empty case (steps []), 200.
export interface RealizedAttributionResponse {
  found: boolean;
  underlying: string;
  expiry: string;
  portfolio_id: string;
  term_unit: string;
  residual_unit: string;
  contracts: string[];
  dates: string[];
  steps: RealizedAttributionStep[];
}

export interface RealizedAttributionQuery {
  underlying?: string;
  expiry?: string;
  startDate?: string;
  endDate?: string;
}

// Fetch the realized day-over-day Greek waterfall for a held position. With no params it defaults
// to the demo September straddle the BFF seeds. A bad date range is a labelled 400 the ApiError
// surfaces; an unknown selection is a labelled-empty 200 (found=false, steps []).
export async function fetchRealizedAttribution(
  query: RealizedAttributionQuery = {},
  signal?: AbortSignal,
): Promise<RealizedAttributionResponse> {
  const params = new URLSearchParams();
  if (query.underlying) params.set("underlying", query.underlying);
  if (query.expiry) params.set("expiry", query.expiry);
  if (query.startDate) params.set("start_date", query.startDate);
  if (query.endDate) params.set("end_date", query.endDate);
  const suffix = params.toString();
  return getJson<RealizedAttributionResponse>(
    `/api/attribution/realized${suffix ? `?${suffix}` : ""}`,
    signal,
  );
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

// --- 2D book composition (TARGET §5.8 / Tab 2 ② The Book) ---------------------------------
// Mirrors apps/frontend/src/algotrading/frontend/routers/compose.py. The operator composes a
// named *book* from an ordered set of sub-strategies (each a 2A basket); the BFF resolves each
// layer's legs, calls the landed pure build_book_greeks / book_stress_surface, and serializes the
// result. No risk is recomputed on the front, no aggregation is forked: every dollar number is the
// engine's own value rendered from its unit string (never re-derived). The HTTP shape is the seam —
// keep both sides in lockstep.

// One $-Greek: the engine's already-monetized value plus its unit string. `value` null only when a
// metric predates the second-order set (still carries a unit). Rendered via sci/sciUnit on the
// front, never re-derived.
export interface ComposeDollarGreek {
  value: number | null;
  unit: string | null;
}

// One row of book Greeks — the combined "book" level or one "layer" level. `net_*` are the raw
// additive sensitivities; `dollar_*` are the monetized + unit-tagged values. `layer_index`/
// `layer_label` identify a layer (the combined book row carries the book-level label).
export interface ComposeGreeks {
  level: string;
  layer_label: string;
  layer_index: number;
  net_delta: number;
  net_gamma: number;
  net_vega: number;
  net_theta: number;
  dollar_delta: ComposeDollarGreek;
  dollar_gamma: ComposeDollarGreek;
  dollar_vega: ComposeDollarGreek;
  dollar_theta: ComposeDollarGreek;
  dollar_rho: ComposeDollarGreek;
}

// A per-layer row: its combined Greeks plus how many of its legs resolved to banked analytics
// (n_resolved of n_legs). The combined book Greeks equal the additive sum of these.
export interface ComposeLayer extends ComposeGreeks {
  n_legs: number;
  n_resolved: number;
}

// The combined stressed PnL surface — the joint full-reprice of the union of all layers over the
// same 2B spot × vol grid the basket stress uses. `pnl_grid[i][j]` is spot_axis[i] × vol_axis[j].
export interface ComposeSurface {
  scenario_version: string | null;
  spot_axis: number[];
  vol_axis: number[];
  pnl_grid: (number | null)[][];
}

export interface ComposeResponse {
  book_id: string;
  valuation_ts: string;
  composition_version: string;
  config_hashes: Record<string, string>;
  combined: ComposeGreeks;
  layers: ComposeLayer[];
  // Read-only diagnostic: the realised diversification of the operator's selection over the
  // per-layer net vegas (null when < 2 layers or all vegas zero). Never feeds the Greeks/PnL.
  diversification_ratio: number | null;
  surface: ComposeSurface;
}

export interface SubStrategiesResponse {
  n_sub_strategies: number;
  sub_strategies: string[];
}

// One compose layer the operator builds: an ordered, labelled sub-strategy (a 2A basket) with its
// legs. Sent as the request body to POST /api/compose.
export interface ComposeLayerInput {
  label: string;
  basket_id: string;
  underlying: string;
  legs: BasketLegInput[];
}

export interface ComposeRequest {
  book_id: string;
  trade_date?: string;
  layers: ComposeLayerInput[];
}

// List the available sub-strategies (the underlyings with banked analytics) the operator can layer.
export async function fetchSubStrategies(signal?: AbortSignal): Promise<SubStrategiesResponse> {
  return getJson<SubStrategiesResponse>("/api/compose/sub-strategies", signal);
}

// Compose a named book from an ordered set of sub-strategy layers. Returns the combined + per-layer
// Greeks and the combined stressed PnL surface. A malformed composition is a labelled 400 the
// ApiError surfaces, not a bare status line.
export async function composeBook(
  body: ComposeRequest,
  signal?: AbortSignal,
): Promise<ComposeResponse> {
  return postJson<ComposeResponse>("/api/compose", body, signal);
}
