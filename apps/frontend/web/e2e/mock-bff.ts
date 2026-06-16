import type { Page, Route } from "@playwright/test";

import type { CoverageData } from "../src/components/CoverageTable";
import {
  ANALYTICS_AAA,
  BASKET_RISK_AAA,
  CONSTITUENTS_TWO,
  DELTA_BANDS_32,
  HEALTH_HEALTHY,
  INDICES_SPX_SX5E,
  PORTFOLIOS_ONE,
  PRICE_HISTORY_AAA,
  PRICE_HISTORY_BATCH_TWO,
  RECORDED_TWO_DATES,
  SCENARIOS_EMPTY,
  SIGNAL_UNDERLYINGS,
  SIGNALS_SX5E,
} from "../src/test/fixtures";

const COVERAGE_AAA: CoverageData = {
  underlying: "AAA",
  trade_date: "2026-06-01",
  n_expiries: 1,
  expiries: [
    {
      expiry: "2026-08-31",
      tenor: "3m",
      n_strikes: 11,
      n_calls: 6,
      n_puts: 5,
      strike_min: 90,
      strike_max: 110,
    },
  ],
  tenors: [
    { tenor: "1m", measured: null, floor: 0.8, status: "unknown" },
    { tenor: "3m", measured: 0.95, floor: 0.8, status: "pass" },
  ],
  qc_status: "pass",
  delta_band_status: "pass",
};

const PROVIDERS_OPS = {
  providers: [
    {
      provider: "SAMPLE",
      asset_class: "equity",
      auth_required: false,
      data_latency: "offline",
      status: "ready",
      note: "Offline synthetic chain fixture.",
    },
    {
      provider: "IBKR",
      asset_class: "equity",
      auth_required: false,
      data_latency: "delayed",
      status: "unavailable",
      note: "Live IBKR needs an authenticated CP gateway.",
    },
  ],
};

const RUN_UNDERLYINGS_OPS = { underlyings: ["SPX", "SX5E"] };

const RUN_JOB = {
  job_id: "job-e2e",
  provider: "SAMPLE",
  underlying: "SPX",
  state: "queued",
  started_at: "2026-06-01T17:30:00",
  finished_at: null,
  message: "Queued",
  summary: {},
};

const JOBS_ONE = {
  jobs: [
    {
      job_id: "job-e2e",
      provider: "SAMPLE",
      underlying: "SPX",
      state: "done",
      started_at: "2026-06-01T17:30:00",
      finished_at: "2026-06-01T17:30:04",
      message: "Built a surface with 6 slices.",
      summary: {},
    },
  ],
};

const TICKET_AAA = {
  source_basket_id: "basket-SPX-latest",
  trade_date: "",
  underlying: "SPX",
  target_broker: "ibkr",
  time_in_force: "day",
  mode: "paper",
  legs: [
    {
      instrument_kind: "option",
      underlying: "SPX",
      side: "buy",
      quantity: 1,
      price_spec: { kind: "market" },
      tenor_label: "1m",
      delta_band: "atm",
    },
    {
      instrument_kind: "option",
      underlying: "SPX",
      side: "buy",
      quantity: 1,
      price_spec: { kind: "market" },
      tenor_label: "1m",
      delta_band: "atmp",
    },
  ],
  n_legs: 2,
  gated: { transmit: false, reason: "sign-and-send is behind an explicit owner gate" },
};

const BACKTEST_RESULT = {
  strategy_id: "bt-SX5E-put-line",
  summary: {
    total_pnl: 125000,
    total_net_pnl: 118500,
    total_transaction_cost: 6500,
    max_drawdown: -42000,
    sharpe: 1.37,
    turnover: 0.85,
    worst_stress_loss: -310000,
  },
  cumulative_attribution: {
    delta: -8000,
    gamma: -55000,
    vega: -22000,
    theta: 215000,
    rho: 1500,
    vanna: -3000,
    volga: -1000,
  },
  days: [
    {
      as_of: "2026-03-02",
      open_contracts: 1,
      entered: 1,
      realized_pnl: 4000,
      cumulative_pnl: 4000,
      cumulative_net_pnl: 3800,
      transaction_cost: 200,
      stress_loss: -90000,
      greeks: { delta: -12, gamma: -0.4, vega: 800, theta: -1500 },
    },
    {
      as_of: "2026-03-03",
      open_contracts: 2,
      entered: 1,
      realized_pnl: -1500,
      cumulative_pnl: 2500,
      cumulative_net_pnl: 2100,
      transaction_cost: 200,
      stress_loss: -180000,
      greeks: { delta: -25, gamma: -0.9, vega: 1700, theta: -3100 },
    },
  ],
};

const ROUTES: Record<string, unknown> = {
  "/healthz": HEALTH_HEALTHY,
  "/api/backtest/run": BACKTEST_RESULT,
  "/api/health": HEALTH_HEALTHY,
  "/api/indices": INDICES_SPX_SX5E,
  "/api/recorded-dates": RECORDED_TWO_DATES,
  "/api/constituents": CONSTITUENTS_TWO,
  "/api/coverage": COVERAGE_AAA,
  "/api/price-history": PRICE_HISTORY_AAA,
  "/api/price-history/batch": PRICE_HISTORY_BATCH_TWO,
  "/api/analytics": ANALYTICS_AAA,
  "/api/risk/portfolios": PORTFOLIOS_ONE,
  "/api/risk/scenarios": SCENARIOS_EMPTY,
  "/api/basket/risk": BASKET_RISK_AAA,
  "/api/ticket/preview": TICKET_AAA,
  "/api/config/delta-bands": { delta_bands: DELTA_BANDS_32 },
  "/api/signals/underlyings": SIGNAL_UNDERLYINGS,
  "/api/signals": SIGNALS_SX5E,
  "/api/providers": PROVIDERS_OPS,
  "/api/run/underlyings": RUN_UNDERLYINGS_OPS,
  "/api/run": RUN_JOB,
  "/api/jobs": JOBS_ONE,
};

export interface BffMock {
  readonly unmatched: string[];
}

export async function mockBff(page: Page): Promise<BffMock> {
  const unmatched: string[] = [];

  await page.route("**/api/**", (route: Route) => fulfill(route, unmatched));
  await page.route("**/healthz", (route: Route) => fulfill(route, unmatched));

  return { unmatched };
}

function fulfill(route: Route, unmatched: string[]) {
  const pathname = new URL(route.request().url()).pathname;
  const body = ROUTES[pathname];
  if (body === undefined) {
    unmatched.push(pathname);
    return route.fulfill({ json: {} });
  }
  return route.fulfill({ json: body });
}
