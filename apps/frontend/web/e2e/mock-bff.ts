// One network-layer BFF mock for every e2e test, mirroring src/test/server.ts (the msw server
// the component tests use) — same endpoints, same fixtures, same "happy path by default"
// contract. Intercepting here means the tests exercise the app's real fetch/render path in a
// real browser without a running BFF and without ever reading the canonical data store.
//
// A single handler on **/api/** routes by pathname so glob precedence never bites; anything not
// listed falls through to an empty 200 JSON object (a page panel renders "no data", never a
// crash) and is recorded so a test can assert nothing unexpected was requested.

import type { Page, Route } from "@playwright/test";

import type { CoverageData } from "../src/components/CoverageTable";
import {
  ANALYTICS_AAA,
  BASKET_RISK_AAA,
  CONSTITUENTS_TWO,
  HEALTH_HEALTHY,
  INDICES_SPX_SX5E,
  PORTFOLIOS_ONE,
  PRICE_HISTORY_AAA,
  PRICE_HISTORY_BATCH_TWO,
  RECORDED_TWO_DATES,
  SCENARIOS_EMPTY,
} from "../src/test/fixtures";

// Capture-coverage payload: CoverageTable declares its shape locally (not in ../api), so the
// fixture is defined here against that type rather than reused from src/test/fixtures.
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

// Pathname → default response body. Query strings are ignored (the page sends ?index=, etc.);
// method is not branched because no two live endpoints share a pathname across GET/POST.
const ROUTES: Record<string, unknown> = {
  "/healthz": HEALTH_HEALTHY,
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
};

export interface BffMock {
  /** Pathnames that were requested but had no fixture (should stay empty in a healthy test). */
  readonly unmatched: string[];
}

/** Install the BFF mock on a page. Call before navigating. Returns a handle to inspect later. */
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
