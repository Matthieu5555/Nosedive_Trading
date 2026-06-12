// The one msw request server for every web test (M10). msw intercepts at the network level,
// so tests exercise the real api.ts fetch path (URL building, error detail extraction, abort
// signals) instead of a per-file hand-rolled fetch router — and they survive a future data-layer
// migration (useFetch → TanStack Query) unchanged.
//
// Defaults are the happy-path fixtures; a test overrides per endpoint with `server.use(...)`
// (prepended, so it wins; reset between tests in src/test/setup.ts). Any /api path without a
// handler answers 500 {"error": "not mocked"} — same contract the old hand-rolled routers had —
// while a request escaping /api entirely fails loudly via onUnhandledRequest: "error".

import { http, HttpResponse, type JsonBodyType } from "msw";
import { setupServer } from "msw/node";

import {
  ANALYTICS_AAA,
  CONSTITUENTS_TWO,
  PORTFOLIOS_ONE,
  PRICE_HISTORY_AAA,
  PRICE_HISTORY_BATCH_TWO,
  RECORDED_TWO_DATES,
  SCENARIOS_EMPTY,
} from "./fixtures";

// The old routers' fallback for an unmocked endpoint, kept as a named helper so a test can
// also force one specific endpoint onto it (e.g. "the batch preload fails").
export function notMocked() {
  return HttpResponse.json({ error: "not mocked" }, { status: 500 });
}

// A JSON 200 handler in one expression, for per-test overrides:
//   server.use(jsonGet("/api/analytics", ANALYTICS_AAA_MONEYNESS_FALLBACK))
export function jsonGet(path: string, body: JsonBodyType) {
  return http.get(path, () => HttpResponse.json(body));
}

export function jsonPost(path: string, body: JsonBodyType) {
  return http.post(path, () => HttpResponse.json(body));
}

export const server = setupServer(
  jsonGet("/api/recorded-dates", RECORDED_TWO_DATES),
  jsonGet("/api/constituents", CONSTITUENTS_TWO),
  jsonGet("/api/price-history", PRICE_HISTORY_AAA),
  jsonGet("/api/analytics", ANALYTICS_AAA),
  jsonPost("/api/price-history/batch", PRICE_HISTORY_BATCH_TWO),
  jsonGet("/api/risk/portfolios", PORTFOLIOS_ONE),
  jsonGet("/api/risk/scenarios", SCENARIOS_EMPTY),
  // Last (lowest precedence): any other /api endpoint behaves like the old routers' 500.
  http.all("/api/*", notMocked),
);
