export interface UnderlyingChoice {
  symbol: string;
  name: string;
  asset_class: string;
  currency: string;
}

export interface UnderlyingsResponse {
  underlyings: UnderlyingChoice[];
}

export interface Provenance {
  as_of: string;
  provider: string;
  code_version: string;
  config_hash: string;
  source: string;
  stamp_hash: string;
}

export interface SnapshotQuote {
  symbol: string;
  name: string;
  last: number;
  bid: number;
  ask: number;
  change_percent: number;
  volume: number;
  snapshot_ts: string;
  currency: string;
}

export interface GreekVector {
  delta: number;
  gamma: number;
  vega: number;
  theta: number;
  rho: number;
}

export interface OptionQuote {
  contract_key: string;
  underlying: string;
  expiry: string;
  strike: number;
  option_type: "call" | "put";
  bid: number;
  ask: number;
  mid: number;
  implied_vol: number;
  open_interest: number;
  volume: number;
  greeks: GreekVector;
}

export interface VolSurfacePoint {
  log_moneyness: number;
  maturity_years: number;
  implied_vol: number;
  total_variance: number;
}

export interface VolSurfaceSlice {
  maturity_years: number;
  expiry: string;
  atm_vol: number;
  skew_25_delta: number;
  svi_a: number;
  svi_b: number;
  svi_rho: number;
  svi_m: number;
  svi_sigma: number;
  rmse: number;
  n_points: number;
}

export interface VolatilitySurface {
  underlying: string;
  as_of: string;
  slices: VolSurfaceSlice[];
  points: VolSurfacePoint[];
}

export interface MarketDashboard {
  underlying: UnderlyingChoice;
  index_snapshot: SnapshotQuote;
  stock_snapshots: SnapshotQuote[];
  option_chain: OptionQuote[];
  greek_totals: GreekVector;
  volatility_surface: VolatilitySurface;
  provenance: Provenance;
}

export interface ScenarioInput {
  underlying: string;
  portfolio_id: string;
  spot_shock_percent: number;
  vol_shock_points: number;
  time_roll_days: number;
}

export interface ScenarioGridPoint {
  spot_shock_percent: number;
  vol_shock_points: number;
  pnl: number;
  delta_after: number;
  vega_after: number;
}

export interface SpotLadderPoint {
  spot_shock_percent: number;
  pnl: number;
  delta: number;
  gamma: number;
  vega: number;
  theta: number;
}

export interface ExpiryGreeks {
  expiry: string;
  contracts: number;
  greeks: GreekVector;
}

export interface ScenarioResult {
  scenario_id: string;
  requested: ScenarioInput;
  baseline_value: number;
  shocked_value: number;
  pnl: number;
  greek_before: GreekVector;
  greek_after: GreekVector;
  grid: ScenarioGridPoint[];
  ladder: SpotLadderPoint[];
  expiry_buckets: ExpiryGreeks[];
  provenance: Provenance;
}

export interface OrderTicket {
  side: "buy" | "sell";
  symbol: string;
  quantity: number;
  limit_price: number;
  instrument_type: "index_option" | "equity";
  expiry?: string | null;
  strike?: number | null;
  option_type?: "call" | "put" | null;
  time_in_force: "day" | "gtc";
}

export interface OrderPreview {
  ticket: OrderTicket;
  estimated_notional: number;
  estimated_commission: number;
  risk_check: "pass" | "warn" | "reject";
  risk_message: string;
  greek_impact: GreekVector;
  paper_only: boolean;
}

export interface OrderHistoryItem {
  order_id: string;
  submitted_at: string;
  ticket: OrderTicket;
  status: "paper_accepted" | "filled" | "cancelled";
  filled_quantity: number;
  average_price: number | null;
}

export interface OrdersDashboard {
  mode: "paper";
  open_orders: OrderHistoryItem[];
  history: OrderHistoryItem[];
  recent_preview: OrderPreview;
}

export async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) {
    let detail = response.statusText;
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
    let detail = response.statusText;
    try {
      detail = JSON.stringify(await response.json());
    } catch {
      detail = response.statusText;
    }
    throw new Error(`${response.status} ${detail}`);
  }
  return (await response.json()) as T;
}
